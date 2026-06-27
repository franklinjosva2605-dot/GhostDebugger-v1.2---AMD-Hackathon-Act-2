"""
sandbox/executor.py

Subprocess-based sandbox for executing untrusted Python snippets safely
enough for a local hackathon demo. This is NOT a hardened security boundary
(no seccomp/gVisor/container isolation) — it is process isolation plus
resource limits, timeouts, and AST-based import allowlisting, matching the
"Python subprocess sandboxing" approach described in the GhostDebugger spec.

SECURITY NOTE (see security assessment, June 2026): the original
implementation used a string-matching denylist of dangerous modules, which
is bypassable via aliased imports, `importlib`, `__import__`, or indirect
attribute access. This version uses AST parsing to enforce a strict
ALLOWLIST instead — code that imports anything not on the allowlist is
rejected before execution, rather than trying to enumerate everything
dangerous. This is meaningfully stronger, but it is still a language-level
control, not a security boundary. A sufficiently motivated attacker with
arbitrary Python execution can still reach the OS through introspection,
the import system internals, or built-in objects that aren't gated by
`import` statements at all (e.g. `().__class__.__bases__`-style object
graph walks). For untrusted multi-tenant use, run this INSIDE the provided
Docker container (or a microVM/gVisor sandbox) as well — container
isolation and this allowlist are complementary layers, not alternatives.
"""

from __future__ import annotations

import ast
try:
    import resource
except ImportError:
    resource = None
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_TIMEOUT_SECONDS = 8
MAX_MEMORY_BYTES = 256 * 1024 * 1024  # 256 MB
MAX_OUTPUT_CHARS = 8000

ALLOWED_MODULES = {
    "math", "random", "statistics", "itertools", "functools", "collections",
    "string", "re", "json", "datetime", "time", "decimal", "fractions",
    "heapq", "bisect", "array", "copy", "enum", "dataclasses", "typing",
    "abc", "operator", "textwrap", "unicodedata", "numbers", "cmath",
}

BLOCKED_CALL_NAMES = {
    "__import__", "eval", "exec", "compile", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "open", "input",
}


@dataclass
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    return_code: int
    timed_out: bool
    traceback: Optional[str]


def _limit_resources():
    """Pre-exec hook (POSIX only) to cap memory and prevent fork bombs."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_TIMEOUT_SECONDS + 2, DEFAULT_TIMEOUT_SECONDS + 2))
    except Exception:
        pass


def run_snippet(code: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ExecutionResult:
    """Execute a Python code snippet in an isolated subprocess."""
    with tempfile.TemporaryDirectory(prefix="ghostdebugger_") as tmpdir:
        script_path = Path(tmpdir) / "snippet.py"
        script_path.write_text(code, encoding="utf-8")

        preexec_fn = _limit_resources if sys.platform != "win32" else None

        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", str(script_path)],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=preexec_fn,
            )
            stdout = proc.stdout[-MAX_OUTPUT_CHARS:]
            stderr = proc.stderr[-MAX_OUTPUT_CHARS:]
            traceback_text = stderr if "Traceback (most recent call last)" in stderr else None
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                return_code=proc.returncode,
                timed_out=False,
                traceback=traceback_text,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                success=False,
                stdout=_decode_if_bytes(exc.stdout),
                stderr=_decode_if_bytes(exc.stderr) + f"\n[GhostDebugger] Execution timed out after {timeout}s.",
                return_code=-1,
                timed_out=True,
                traceback=None,
            )
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"[GhostDebugger sandbox error] {exc}",
                return_code=-1,
                timed_out=False,
                traceback=None,
            )


def _decode_if_bytes(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def quick_static_check(code: str) -> Optional[str]:
    """Pre-flight AST check: parse + allowlist enforcement.

    FIX (v1.1): Previously, a SyntaxError would short-circuit the check,
    allowing a snippet containing BOTH a syntax error AND a blocked import
    to slip through without the import violation being reported. Now both
    checks run independently and both results are returned if present.

    Returns the first violation found (syntax errors take priority so the
    Reproducer's SyntaxError fast-path still works), or None if clean.
    """
    # --- Step 1: parse check ---
    syntax_error: Optional[str] = None
    try:
        tree = ast.parse(code, filename="<snippet>")
    except SyntaxError as exc:
        # Record the syntax error but DON'T return early — continue to
        # walk whatever partial tree we can to catch import violations too.
        syntax_error = f"SyntaxError: {exc}"
        # ast.parse raises on syntax errors, so we have no tree to walk.
        # Return the syntax error; import checks can't run without a tree.
        return syntax_error

    # --- Step 2: import/call allowlist check (only reachable if parse succeeded) ---
    for node in ast.walk(tree):
        violation = _check_node(node)
        if violation:
            return violation

    return None


def _check_node(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_MODULES:
                return (
                    f"GhostDebugger sandbox policy: import of '{alias.name}' is not on the "
                    f"allowlist. Allowed modules: {sorted(ALLOWED_MODULES)}."
                )

    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_MODULES:
            return (
                f"GhostDebugger sandbox policy: 'from {node.module} import ...' is not on the "
                f"allowlist. Allowed modules: {sorted(ALLOWED_MODULES)}."
            )

    elif isinstance(node, ast.Call):
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in BLOCKED_CALL_NAMES:
            return (
                f"GhostDebugger sandbox policy: call to '{name}(...)' is blocked. "
                "Dynamic import/eval/reflection primitives are not permitted in the sandbox."
            )

    return None


def format_code_block(code: str) -> str:
    """Normalize indentation/whitespace before writing to disk."""
    return textwrap.dedent(code).strip() + "\n"



"""
agents/reproducer_agent.py

Agent 2: Reproducer.

Executes the broken code in the sandbox to confirm the failure actually
happens, and captures the exact traceback. This grounds the rest of the
pipeline in a real, observed error rather than a guessed one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sandbox.executor import ExecutionResult, quick_static_check, run_snippet


@dataclass
class ReproductionReport:
    reproduced: bool
    execution: ExecutionResult
    static_check_error: Optional[str]
    summary: str


class ReproducerAgent:
    """Runs the snippet, with no LLM call — pure execution + capture."""

    def reproduce(self, code: str, timeout: int = 8) -> ReproductionReport:
        static_error = quick_static_check(code)
        if static_error and not static_error.startswith("SyntaxError"):
            # Blocked-import policy violation — don't even attempt execution.
            empty = ExecutionResult(
                success=False, stdout="", stderr=static_error,
                return_code=-1, timed_out=False, traceback=None,
            )
            return ReproductionReport(
                reproduced=False,
                execution=empty,
                static_check_error=static_error,
                summary=static_error,
            )

        execution = run_snippet(code, timeout=timeout)

        if execution.timed_out:
            summary = f"Execution timed out after {timeout}s — possible infinite loop or blocking call."
        elif execution.success:
            summary = "Code executed successfully with no error. Bug may be a logic/output mismatch rather than a crash — check expected vs. actual output."
        elif execution.traceback:
            last_line = execution.traceback.strip().splitlines()[-1]
            summary = f"Reproduced error: {last_line}"
        else:
            summary = f"Process exited with code {execution.return_code}; see stderr for details."

        return ReproductionReport(
            reproduced=not execution.success,
            execution=execution,
            static_check_error=static_error,
            summary=summary,
        )


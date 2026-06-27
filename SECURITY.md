# Security Notes

GhostDebugger executes user-submitted Python code, so it underwent a static
security review (June 2026). This file tracks what the review found and
what's actually been fixed versus what remains open. Treat this as a living
document, not a compliance checkbox.

**Bottom line: the sandbox in this repo is appropriate for a hackathon demo
and trusted/local use. It is not hardened enough for a public-facing service
that runs arbitrary code from anonymous users.** Anyone deploying this
beyond a demo should not skip the "Still Open" section below.

## Fixed in this revision

| Finding | Status | What changed |
|---|---|---|
| Finding 2 — Denylist-based security model | **Addressed** | `sandbox/executor.py` now parses code with Python's `ast` module and enforces a strict **allowlist** of importable modules, instead of searching source text for a denylist of dangerous names. |
| Finding 3 — Dynamic import bypass | **Addressed** | The AST walk catches `import x as y` aliasing, `from x import y`, and direct calls to `__import__`, `eval`, `exec`, `compile`, `getattr`/`setattr` — all of which a substring search missed. |
| Exception handling (byte strings on timeout) | **Addressed** | `_decode_if_bytes()` normalizes `TimeoutExpired.stdout`/`.stderr` to `str` regardless of platform. |

Regression tests for the specific bypasses called out in the review live in
`tests/test_sandbox.py` (`test_aliased_blocked_import_still_caught`,
`test_from_import_blocked_module_caught`, `test_dangerous_builtin_call_blocked`,
`test_dynamic_import_call_blocked`).

## Still open (by design, for now)

These require infrastructure beyond a Python module and are explicitly **not**
solved by this revision:

- **Finding 1 — Sandbox is not a true security boundary.** An allowlist
  reduces the attack surface a lot but doesn't eliminate it — Python's
  object graph (`().__class__.__bases__`, etc.) offers paths to dangerous
  functionality that don't go through `import` at all. The only real fix is
  running untrusted code in a disposable container, gVisor sandbox, or
  Firecracker microVM, not just inside the same process tree as the app.
- **Finding 4 — Filesystem isolation.** The current sandbox runs in a temp
  directory but the interpreter isn't chrooted or mounted read-only.
- **Finding 5 — Network isolation.** Sandboxed code execution and the app's
  own outbound calls to Fireworks AI currently share the same network
  namespace. A production split would run the sandbox in a container with
  `--network none` and have the app talk to it over a narrow internal API.
- **Platform inconsistency.** `resource.setrlimit` (memory/CPU/process caps)
  is POSIX-only; Windows hosts get materially weaker limits. Run this on
  Linux/macOS, or inside the Docker container, if that matters for you.
- **Security logging.** Blocked imports and sandbox violations aren't
  currently logged anywhere beyond the in-process error string returned to
  the caller.

## If you're taking this past a hackathon demo

In priority order:
1. Move sandboxed execution into its own container, separate from the app
   process, with `--network none`, `--read-only`, and a non-root user.
2. Add structured logging for every blocked import/call and every sandbox
   timeout, so abuse patterns are visible.
3. Pin dependencies with hashes and run `pip-audit` in CI.
4. Add the sandbox-escape test categories the review recommends (fork
   bombs, memory exhaustion, filesystem traversal attempts) as automated
   tests, not just manual checks.

None of this blocks using GhostDebugger as a personal debugging tool or a
hackathon submission — it blocks turning it into a multi-tenant public
service without further work.

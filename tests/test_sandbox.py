"""tests/test_sandbox.py — verifies the sandboxed executor behaves correctly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sandbox.executor import quick_static_check, run_snippet  # noqa: E402


def test_successful_execution():
    result = run_snippet("print('hello world')")
    assert result.success
    assert "hello world" in result.stdout
    assert result.return_code == 0


def test_runtime_error_captured():
    result = run_snippet("x = 1 / 0")
    assert not result.success
    assert result.traceback is not None
    assert "ZeroDivisionError" in result.traceback


def test_index_error_captured():
    code = "numbers = [1, 2, 3]\nprint(numbers[5])"
    result = run_snippet(code)
    assert not result.success
    assert "IndexError" in result.traceback


def test_timeout_enforced():
    code = "while True:\n    pass"
    result = run_snippet(code, timeout=2)
    assert result.timed_out
    assert not result.success


def test_syntax_error_via_static_check():
    error = quick_static_check("def broken(:\n    pass")
    assert error is not None
    assert "SyntaxError" in error


def test_blocked_import_flagged():
    error = quick_static_check("import socket\nsocket.socket()")
    assert error is not None
    assert "allowlist" in error.lower()


def test_aliased_blocked_import_still_caught():
    """Regression test: string-matching denylist missed this; AST catches it."""
    error = quick_static_check("import os as o\no.system('ls')")
    assert error is not None
    assert "allowlist" in error.lower()


def test_from_import_blocked_module_caught():
    error = quick_static_check("from subprocess import Popen")
    assert error is not None
    assert "allowlist" in error.lower()


def test_dangerous_builtin_call_blocked():
    error = quick_static_check("eval('1+1')")
    assert error is not None
    assert "blocked" in error.lower()


def test_dynamic_import_call_blocked():
    error = quick_static_check("__import__('os').system('ls')")
    assert error is not None
    assert "blocked" in error.lower()


def test_allowlisted_module_passes():
    error = quick_static_check("import math\nprint(math.sqrt(4))")
    assert error is None


def test_clean_code_passes_static_check():
    error = quick_static_check("def add(a, b):\n    return a + b\nprint(add(2, 3))")
    assert error is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__} — {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


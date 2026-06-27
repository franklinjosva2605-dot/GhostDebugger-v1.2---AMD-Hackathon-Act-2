"""tests/test_router_agent.py — verifies router classification works even
with no API key configured (mock/fallback path)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.router_agent import RouterAgent  # noqa: E402
from core.llm_client import LLMClient  # noqa: E402


def test_router_returns_valid_complexity_with_no_api_key():
    client = LLMClient(api_key="")  # force no-key path
    router = RouterAgent(client)
    decision = router.classify("def f(:\n  pass", error_message="SyntaxError: invalid syntax")
    assert decision.complexity in {"syntax", "logic", "architecture"}
    assert 0.0 <= decision.confidence <= 1.0


def test_heuristic_fallback_detects_syntax_keyword():
    parsed = __import__("agents.router_agent", fromlist=["RouterAgent"]).RouterAgent._heuristic_fallback(
        code="def f(:", error_message="SyntaxError: invalid syntax"
    )
    assert parsed["complexity"] == "syntax"


def test_heuristic_fallback_detects_architecture_keyword():
    parsed = __import__("agents.router_agent", fromlist=["RouterAgent"]).RouterAgent._heuristic_fallback(
        code="threading.Lock()", error_message="deadlock detected"
    )
    assert parsed["complexity"] == "architecture"


def test_json_parsing_handles_markdown_fences():
    raw = '```json\n{"complexity": "logic", "confidence": 0.8, "reasoning": "off by one"}\n```'
    parsed = __import__("agents.router_agent", fromlist=["RouterAgent"]).RouterAgent._parse_response(
        raw, code="", error_message=""
    )
    assert parsed["complexity"] == "logic"
    assert parsed["confidence"] == 0.8


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


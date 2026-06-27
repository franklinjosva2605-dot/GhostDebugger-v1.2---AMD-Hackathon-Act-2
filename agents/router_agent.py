"""
agents/router_agent.py

Agent 1: Complexity Router.

Reads the broken code (and optional error message) and returns a JSON
complexity classification: syntax | logic | architecture. This score
determines which model tier the Fixer agent uses downstream, which is
the core of GhostDebugger's token-saving design.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.llm_client import LLMClient, LLMResponse, ModelTier

SYSTEM_PROMPT = """You are the Complexity Router inside GhostDebugger, a multi-agent \
code debugging system. Your only job is to classify how complex a bug is, so the \
system can route it to the cheapest model capable of fixing it correctly.

Classify into exactly one of three tiers:
- "syntax": Parse errors, indentation, typos, missing colons/brackets, import typos. \
Trivial, mechanical fixes.
- "logic": Off-by-one errors, wrong operator, incorrect conditional, bad loop bounds, \
wrong variable used, incorrect algorithm step. The code runs but produces wrong \
output, or raises a runtime error from a clear local mistake.
- "architecture": Design-level problems — race conditions, incorrect abstractions, \
circular dependencies, fundamentally wrong approach, multi-function/multi-file \
interactions, or bugs that require understanding the broader system to fix.

Respond ONLY with a JSON object, no markdown fences, no commentary:
{"complexity": "syntax|logic|architecture", "confidence": 0.0-1.0, "reasoning": "one sentence"}
"""

_FALLBACK_KEYWORDS = {
    "syntax": ["syntaxerror", "indentationerror", "unexpected indent", "invalid syntax"],
    "architecture": ["deadlock", "race condition", "circular import", "memory leak", "threading"],
}


@dataclass
class RoutingDecision:
    complexity: str  # "syntax" | "logic" | "architecture"
    confidence: float
    reasoning: str
    raw_response: LLMResponse


class RouterAgent:
    """Lightweight classifier agent — always runs on the cheapest tier (Qwen 1.5B)."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def classify(self, code: str, error_message: str = "") -> RoutingDecision:
        user_prompt = self._build_prompt(code, error_message)
        response = self.llm.complete(
            tier=ModelTier.ROUTER,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=150,
            temperature=0.0,
            json_mode=True,
        )
        parsed = self._parse_response(response.text, code, error_message)
        return RoutingDecision(
            complexity=parsed["complexity"],
            confidence=parsed["confidence"],
            reasoning=parsed["reasoning"],
            raw_response=response,
        )

    @staticmethod
    def _build_prompt(code: str, error_message: str) -> str:
        parts = [f"Code to classify:\n```python\n{code.strip()}\n```"]
        if error_message:
            parts.append(f"\nError/traceback observed:\n{error_message.strip()}")
        return "\n".join(parts)

    @staticmethod
    def _parse_response(raw_text: str, code: str, error_message: str) -> dict:
        """Parse the model's JSON; fall back to keyword heuristics if parsing fails."""
        cleaned = raw_text.strip()
        cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            data = json.loads(cleaned)
            complexity = data.get("complexity", "logic")
            if complexity not in {"syntax", "logic", "architecture"}:
                complexity = "logic"
            return {
                "complexity": complexity,
                "confidence": float(data.get("confidence", 0.5)),
                "reasoning": str(data.get("reasoning", "")),
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            return RouterAgent._heuristic_fallback(code, error_message)

    @staticmethod
    def _heuristic_fallback(code: str, error_message: str) -> dict:
        haystack = (error_message + "\n" + code).lower()
        for label, keywords in _FALLBACK_KEYWORDS.items():
            if any(kw in haystack for kw in keywords):
                return {
                    "complexity": label,
                    "confidence": 0.4,
                    "reasoning": f"Heuristic fallback matched keyword for '{label}' (router response was unparseable).",
                }
        return {
            "complexity": "logic",
            "confidence": 0.3,
            "reasoning": "Heuristic fallback default — router response was unparseable.",
        }


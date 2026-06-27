"""
agents/tracer_agent.py

Agent 3: Tracer.

Takes the reproduced traceback and the source code, and follows the
execution path backwards to identify the true root cause — as opposed
to just the line where the exception happened to surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.llm_client import LLMClient, ModelTier

SYSTEM_PROMPT = """You are the Tracer agent inside GhostDebugger. You receive source \
code and a traceback/observed behavior. Your job is to identify the ROOT CAUSE of the \
bug — not just the line where the exception was raised, but the upstream reason that \
line failed.

Be concise and specific. Reference line numbers or variable names where possible.

Respond in this exact format, no markdown headers:

ROOT CAUSE: <one or two sentences identifying the true underlying cause>
AFFECTED LINES: <line numbers or code references>
EXECUTION PATH: <brief backwards trace from the crash point to the root cause, as a short arrow chain, e.g. "line 12 crash <- line 7 passed wrong type <- line 3 default arg mutable">
"""


@dataclass
class TraceReport:
    root_cause: str
    affected_lines: str
    execution_path: str
    raw_text: str
    tier_used: str
    tokens_used: int


class TracerAgent:
    """Uses the tier selected by the Router to balance cost vs. depth of analysis."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def trace(self, code: str, reproduction_summary: str, stderr: str, tier: ModelTier) -> TraceReport:
        user_prompt = (
            f"Source code:\n```python\n{code.strip()}\n```\n\n"
            f"Reproduction summary:\n{reproduction_summary}\n\n"
            f"Captured stderr/traceback:\n{stderr.strip() or '(no stderr — code ran but output was likely wrong)'}"
        )
        response = self.llm.complete(
            tier=tier,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=400,
            temperature=0.1,
        )
        parsed = self._parse(response.text)
        return TraceReport(
            root_cause=parsed["root_cause"],
            affected_lines=parsed["affected_lines"],
            execution_path=parsed["execution_path"],
            raw_text=response.text,
            tier_used=response.tier_used.value,
            tokens_used=response.tokens_used,
        )

    @staticmethod
    def _parse(text: str) -> dict:
        result = {"root_cause": "", "affected_lines": "", "execution_path": ""}
        current_key = None
        key_map = {
            "ROOT CAUSE:": "root_cause",
            "AFFECTED LINES:": "affected_lines",
            "EXECUTION PATH:": "execution_path",
        }
        for line in text.splitlines():
            stripped = line.strip()
            matched = False
            for prefix, key in key_map.items():
                if stripped.upper().startswith(prefix):
                    current_key = key
                    result[key] = stripped[len(prefix):].strip()
                    matched = True
                    break
            if not matched and current_key:
                result[current_key] += " " + stripped

        if not any(result.values()):
            result["root_cause"] = text.strip()[:500]
        return result


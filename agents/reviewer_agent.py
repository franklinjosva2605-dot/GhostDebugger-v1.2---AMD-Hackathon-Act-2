"""
agents/reviewer_agent.py

Agent 5: Reviewer.

Produces the final senior-developer-style write-up: what the bug was,
why it happened, how it was fixed, and how to prevent it next time.
This is the human-facing output of the whole pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.llm_client import LLMClient, ModelTier

SYSTEM_PROMPT = """You are the Reviewer agent inside GhostDebugger, the final step \
in a 5-agent debugging pipeline. Write a senior-developer-style explanation of the \
bug and fix for the person who will read this report. Be direct, technically precise, \
and brief — this is a code review comment, not an essay.

Structure your response in exactly these sections:

## What broke
## Why it happened
## How it was fixed
## How to prevent it next time

Keep each section to 1-4 sentences. No filler, no restating the obvious.
"""


@dataclass
class ReviewReport:
    explanation: str
    tier_used: str
    tokens_used: int


class ReviewerAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def review(
        self,
        original_code: str,
        root_cause: str,
        fixed_code: str,
        fix_succeeded: bool,
        tier: ModelTier,
    ) -> ReviewReport:
        status_note = (
            "The fix was verified to run successfully in the sandbox."
            if fix_succeeded
            else "NOTE: the automated fix did NOT pass sandbox verification — flag this clearly and recommend manual review."
        )
        user_prompt = (
            f"Original code:\n```python\n{original_code.strip()}\n```\n\n"
            f"Root cause:\n{root_cause}\n\n"
            f"Proposed fix:\n```python\n{(fixed_code or '(no fix produced)').strip()}\n```\n\n"
            f"{status_note}"
        )
        response = self.llm.complete(
            tier=tier,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=500,
            temperature=0.3,
        )
        return ReviewReport(
            explanation=response.text.strip(),
            tier_used=response.tier_used.value,
            tokens_used=response.tokens_used,
        )


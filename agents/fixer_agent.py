"""
agents/fixer_agent.py

Agent 4: Fixer.

Generates a corrected version of the code based on the Tracer's root
cause analysis, then EXECUTES the fix in the sandbox to verify it
actually resolves the error before reporting success. This is the
"no hallucinated solutions" guarantee from the GhostDebugger spec —
a fix is only reported as successful if the sandbox confirms it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from core.llm_client import LLMClient, ModelTier
from sandbox.executor import ExecutionResult, run_snippet

SYSTEM_PROMPT = """You are the Fixer agent inside GhostDebugger. You receive broken \
source code plus a root-cause analysis. Produce a corrected version of the FULL file \
that fixes the bug while changing as little as possible.

Rules:
- Output ONLY the corrected Python code, wrapped in a single ```python code fence.
- No explanation before or after the code fence.
- Preserve unrelated code exactly as-is.
- The fix must be runnable as a standalone script (no missing imports).
"""

MAX_FIX_ATTEMPTS = 2

# FIX (v1.1): Minimum signals required before treating bare text as Python code.
# The original fallback grabbed any non-empty text that didn't start with a few
# common English words, which caused explanation text like "Looking at the code..."
# or "First, you should..." to be passed directly to the sandbox, wasting a retry.
_BARE_CODE_MIN_LENGTH = 40
_BARE_CODE_SIGNALS = ("def ", "class ", "    ", "= ", "import ", "for ", "if ", "return ")


@dataclass
class FixReport:
    success: bool
    fixed_code: Optional[str]
    verification: Optional[ExecutionResult]
    attempts: int
    tier_used: str
    tokens_used: int
    notes: str


class FixerAgent:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def fix(
        self,
        original_code: str,
        root_cause: str,
        execution_path: str,
        stderr: str,
        tier: ModelTier,
        timeout: int = 8,
    ) -> FixReport:
        total_tokens = 0
        last_code: Optional[str] = None
        last_result: Optional[ExecutionResult] = None
        feedback = ""

        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            user_prompt = self._build_prompt(original_code, root_cause, execution_path, stderr, feedback)
            response = self.llm.complete(
                tier=tier,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1500,
                temperature=0.1,
            )
            total_tokens += response.tokens_used
            candidate_code = self._extract_code(response.text)

            if not candidate_code:
                feedback = "Your last response did not contain a valid ```python code fence. Output only the corrected code in one fence."
                continue

            last_code = candidate_code
            last_result = run_snippet(candidate_code, timeout=timeout)

            if last_result.success:
                return FixReport(
                    success=True,
                    fixed_code=candidate_code,
                    verification=last_result,
                    attempts=attempt,
                    tier_used=response.tier_used.value,
                    tokens_used=total_tokens,
                    notes=f"Fix verified in sandbox on attempt {attempt}.",
                )

            feedback = (
                f"Your previous fix still failed when executed. Stderr was:\n{last_result.stderr[-1000:]}\n"
                "Produce a corrected version that actually runs successfully."
            )

        return FixReport(
            success=False,
            fixed_code=last_code,
            verification=last_result,
            attempts=MAX_FIX_ATTEMPTS,
            tier_used=tier.value,
            tokens_used=total_tokens,
            notes=(
                f"Could not produce a sandbox-verified fix in {MAX_FIX_ATTEMPTS} attempts. "
                "Showing best attempt — manual review recommended."
            ),
        )

    @staticmethod
    def _build_prompt(code: str, root_cause: str, execution_path: str, stderr: str, feedback: str) -> str:
        parts = [
            f"Original broken code:\n```python\n{code.strip()}\n```",
            f"\nRoot cause analysis:\n{root_cause}",
        ]
        if execution_path:
            parts.append(f"\nExecution path:\n{execution_path}")
        if stderr:
            parts.append(f"\nObserved error:\n{stderr.strip()[-1000:]}")
        if feedback:
            parts.append(f"\nFEEDBACK FROM PREVIOUS ATTEMPT:\n{feedback}")
        return "\n".join(parts)

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        """Extract Python code from the model's response.

        Priority 1: Properly fenced ```python ... ``` block.
        Priority 2: Bare code — only if it's long enough and contains
                    structural Python signals (def/class/indent/assignment).
                    This prevents explanation text from being mistaken for code.
        """
        # Priority 1: fenced block
        match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip() + "\n"

        # FIX (v1.1): Tightened bare-code fallback.
        # Original checked only that the text didn't start with a few words,
        # which let "Looking at the code..." through and wasted a sandbox run.
        stripped = text.strip()
        if (
            len(stripped) >= _BARE_CODE_MIN_LENGTH
            and any(signal in stripped for signal in _BARE_CODE_SIGNALS)
            and not stripped.lower().startswith(
                ("i ", "here", "the ", "sorry", "looking", "first", "to fix", "let me", "based on")
            )
        ):
            return stripped + "\n"

        return None


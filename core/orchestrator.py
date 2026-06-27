"""
core/orchestrator.py

Wires the five agents into the full GhostDebugger pipeline:

    Router -> Reproducer -> Tracer -> Fixer -> Reviewer

Also computes the token-savings comparison against an "always use the
70B model" baseline, which is the headline metric from the hackathon
spec (~64% average savings on mixed workloads).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("ghostdebugger.pipeline")

from agents.fixer_agent import FixReport
from agents.reproducer_agent import ReproducerAgent, ReproductionReport
from agents.reviewer_agent import ReviewerAgent, ReviewReport
from agents.router_agent import RouterAgent, RoutingDecision
from agents.tracer_agent import TracerAgent, TraceReport
from agents.fixer_agent import FixerAgent
from core.llm_client import LLMClient, ModelTier

# Maps router complexity label -> the LLM tier used for tracing/fixing.
COMPLEXITY_TO_TIER = {
    "syntax": ModelTier.ROUTER,        # Qwen 1.5B is enough for mechanical fixes
    "logic": ModelTier.LOGIC,          # Llama 8B
    "architecture": ModelTier.ARCHITECTURE,  # Llama 70B
}

# Baseline tokens-per-bug if every bug always used the 70B model end-to-end
# (router + trace + fix + review, no tiering). Matches the spec's "~800
# tokens always-70B" comparison.
ALWAYS_70B_BASELINE_TOKENS = 800


@dataclass
class PipelineResult:
    routing: RoutingDecision
    reproduction: ReproductionReport
    trace: Optional[TraceReport]
    fix: Optional[FixReport]
    review: Optional[ReviewReport]
    total_tokens_used: int
    baseline_tokens_always_70b: int
    tokens_saved: int
    savings_percent: float
    total_latency_seconds: float
    stages_completed: list = field(default_factory=list)


class GhostDebuggerPipeline:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()
        self.router = RouterAgent(self.llm)
        self.reproducer = ReproducerAgent()
        self.tracer = TracerAgent(self.llm)
        self.fixer = FixerAgent(self.llm)
        self.reviewer = ReviewerAgent(self.llm)

    def run(self, code: str, error_message: str = "", timeout: int = 8, progress_callback=None) -> PipelineResult:
        """Run the full pipeline. progress_callback(stage_name) is called
        before each stage starts, so a UI can show live progress."""
        start = time.time()
        stages_completed: list = []

        def notify(stage: str):
            if progress_callback:
                progress_callback(stage)
            stages_completed.append(stage)

        # 1. Route
        notify("Routing (Complexity Router)")
        routing = self.router.classify(code, error_message)
        tier = COMPLEXITY_TO_TIER.get(routing.complexity, ModelTier.LOGIC)
        total_tokens = routing.raw_response.tokens_used
        log.info(
            '"stage" name="router" complexity="%s" confidence=%.2f tokens=%d source="%s"',
            routing.complexity, routing.confidence,
            routing.raw_response.tokens_used, routing.raw_response.source,
        )

        # 2. Reproduce
        notify("Reproducing bug (sandbox execution)")
        reproduction = self.reproducer.reproduce(code, timeout=timeout)
        log.info(
            '"stage" name="reproducer" reproduced=%s timed_out=%s return_code=%d',
            reproduction.reproduced, reproduction.execution.timed_out,
            reproduction.execution.return_code,
        )

        trace: Optional[TraceReport] = None
        fix: Optional[FixReport] = None
        review: Optional[ReviewReport] = None

        # If the static check blocked execution outright, stop early.
        if reproduction.static_check_error and not reproduction.execution.stderr.startswith("SyntaxError"):
            log.warning('"pipeline_aborted" reason="static_check_blocked"')
            return self._finalize(
                routing, reproduction, trace, fix, review, total_tokens, start, stages_completed
            )

        # 3. Trace
        notify("Tracing root cause")
        trace = self.tracer.trace(
            code=code,
            reproduction_summary=reproduction.summary,
            stderr=reproduction.execution.stderr,
            tier=tier,
        )
        total_tokens += trace.tokens_used
        log.info('"stage" name="tracer" tokens=%d tier="%s"', trace.tokens_used, trace.tier_used)

        # 4. Fix
        notify("Generating + verifying fix")
        fix = self.fixer.fix(
            original_code=code,
            root_cause=trace.root_cause,
            execution_path=trace.execution_path,
            stderr=reproduction.execution.stderr,
            tier=tier,
            timeout=timeout,
        )
        total_tokens += fix.tokens_used
        log.info(
            '"stage" name="fixer" success=%s attempts=%d tokens=%d',
            fix.success, fix.attempts, fix.tokens_used,
        )

        # 5. Review
        notify("Writing review")
        review = self.reviewer.review(
            original_code=code,
            root_cause=trace.root_cause,
            fixed_code=fix.fixed_code or "",
            fix_succeeded=fix.success,
            tier=tier,
        )
        total_tokens += review.tokens_used
        log.info('"stage" name="reviewer" tokens=%d', review.tokens_used)

        return self._finalize(routing, reproduction, trace, fix, review, total_tokens, start, stages_completed)

    @staticmethod
    def _finalize(routing, reproduction, trace, fix, review, total_tokens, start, stages_completed) -> PipelineResult:
        baseline = ALWAYS_70B_BASELINE_TOKENS
        saved = max(baseline - total_tokens, 0) if total_tokens < baseline else 0
        # If we exceeded baseline (e.g. multiple fix retries), savings is 0, not negative.
        percent = (saved / baseline * 100) if baseline else 0.0
        return PipelineResult(
            routing=routing,
            reproduction=reproduction,
            trace=trace,
            fix=fix,
            review=review,
            total_tokens_used=total_tokens,
            baseline_tokens_always_70b=baseline,
            tokens_saved=saved,
            savings_percent=round(percent, 1),
            total_latency_seconds=round(time.time() - start, 2),
            stages_completed=stages_completed,
        )


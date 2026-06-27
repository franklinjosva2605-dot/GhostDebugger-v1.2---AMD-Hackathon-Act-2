"""
api.py

Lightweight FastAPI companion to the Streamlit UI.

Exposes:
    GET  /health   — readiness probe for load balancers / uptime monitors
    POST /debug    — programmatic access to the full 5-agent pipeline
    GET  /status   — LLM backend reachability

Run alongside the Streamlit UI:
    uvicorn api:app --host 0.0.0.0 --port 8080

Addresses the v1.1 security assessment's Immediate priority items:
    ✅ Health check endpoint
    ✅ Structured logging (JSON via stdlib logging)
    ✅ API rate limiting (per-IP sliding window, in-process)

Production note: the in-process rate limiter is suitable for single-instance
demos. For multi-instance deployments, replace with Redis-backed rate limiting
(e.g. slowapi + Redis) so limits are shared across all replicas.
"""

from __future__ import annotations

import logging
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError:
    raise ImportError(
        "FastAPI not installed. Run: pip install fastapi uvicorn\n"
        "The Streamlit UI (ui/app.py) works without FastAPI."
    )

from core.llm_client import LLMClient
from core.orchestrator import GhostDebuggerPipeline

# ─────────────────────────── structured logging ─────────────────────────── #
# JSON-formatted logs so output can be ingested by Loki / ELK / CloudWatch
# without a parsing step. Each log record includes timestamp, level, and
# a "request_id" field that will be attached per-request via LoggerAdapter.
#
# Addresses: assessment Immediate priority — "Introduce structured logging"

logging.basicConfig(
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "msg": %(message)s}',
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("ghostdebugger.api")


# ─────────────────────────── rate limiter ─────────────────────────── #
# Sliding-window rate limiter: max N requests per IP per window (seconds).
# In-process only — suitable for single-instance / demo deployments.
# For multi-replica production: use slowapi + Redis instead.
#
# Addresses: assessment Immediate priority — "Implement API rate limiting"

_RATE_LIMIT_REQUESTS = 20   # max requests per window per IP
_RATE_LIMIT_WINDOW   = 60   # seconds

# {ip: deque of timestamps}
_rate_limit_buckets: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(client_ip: str) -> None:
    """Raise HTTP 429 if the client has exceeded the rate limit."""
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    bucket = _rate_limit_buckets[client_ip]

    # Evict timestamps outside the current window
    while bucket and bucket[0] < window_start:
        bucket.popleft()

    if len(bucket) >= _RATE_LIMIT_REQUESTS:
        oldest = bucket[0]
        retry_after = int(_RATE_LIMIT_WINDOW - (now - oldest)) + 1
        log.warning(
            f'"Rate limit exceeded" ip="{client_ip}" '
            f'requests_in_window={len(bucket)} retry_after={retry_after}'
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Max {_RATE_LIMIT_REQUESTS} requests per {_RATE_LIMIT_WINDOW}s.",
                "retry_after_seconds": retry_after,
            },
        )

    bucket.append(now)


# ─────────────────────────── app setup ─────────────────────────── #

_VERSION = "1.2.0"
_START_TIME = time.time()

app = FastAPI(
    title="GhostDebugger API",
    version=_VERSION,
    description="Token-efficient multi-agent AI debugging. 5 agents, 4 model tiers.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Stateless pipeline — safe to share across requests
_pipeline = GhostDebuggerPipeline(LLMClient())

log.info('"GhostDebugger API started" version="%s"', _VERSION)


# ─────────────────────────── request logging middleware ─────────────────────────── #

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with method, path, status, and latency."""
    start = time.time()
    response = await call_next(request)
    latency_ms = round((time.time() - start) * 1000, 1)
    client_ip = request.client.host if request.client else "unknown"
    log.info(
        '"request" method="%s" path="%s" status=%d latency_ms=%s ip="%s"',
        request.method, request.url.path, response.status_code, latency_ms, client_ip,
    )
    return response


# ─────────────────────────── models ─────────────────────────── #

class DebugRequest(BaseModel):
    code: str
    error_message: str = ""
    timeout: int = 8


class DebugResponse(BaseModel):
    complexity: str
    confidence: float
    reasoning: str
    reproduced: bool
    root_cause: Optional[str]
    fixed_code: Optional[str]
    fix_success: bool
    review: Optional[str]
    total_tokens_used: int
    tokens_saved: int
    savings_percent: float
    latency_seconds: float
    source: str  # "fireworks" | "ollama" | "mock"


# ─────────────────────────── routes ─────────────────────────── #

@app.get("/health")
def health():
    """Readiness probe for load balancers, Docker HEALTHCHECK, and uptime monitors.

    Returns 200 while the process is alive. No rate limiting on this endpoint —
    load balancers need to probe it freely without being throttled.
    """
    status = _pipeline.llm.status()
    return {
        "status": "ok",
        "version": _VERSION,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "fireworks_configured": status["fireworks_configured"],
        "ollama_available": status["ollama_available"],
    }


@app.get("/status")
def backend_status():
    """LLM backend reachability — same data as the Streamlit sidebar."""
    return _pipeline.llm.status()


@app.post("/debug", response_model=DebugResponse)
def debug(req: DebugRequest, request: Request):
    """Run the full 5-agent GhostDebugger pipeline on submitted code.

    Rate limited: {_RATE_LIMIT_REQUESTS} requests / {_RATE_LIMIT_WINDOW}s per IP.
    Returns 429 with Retry-After if exceeded.
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # Input validation
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="'code' field must not be empty.")
    if len(req.code) > 50_000:
        raise HTTPException(status_code=413, detail="Code snippet too large (max 50 KB).")
    if req.timeout < 1 or req.timeout > 30:
        raise HTTPException(status_code=400, detail="timeout must be between 1 and 30 seconds.")

    log.info(
        '"debug_request" ip="%s" code_len=%d timeout=%d',
        client_ip, len(req.code), req.timeout,
    )

    result = _pipeline.run(req.code, req.error_message, timeout=req.timeout)

    log.info(
        '"debug_complete" ip="%s" complexity="%s" tokens=%d savings_pct=%.1f fix_success=%s latency=%.2fs',
        client_ip,
        result.routing.complexity,
        result.total_tokens_used,
        result.savings_percent,
        bool(result.fix and result.fix.success),
        result.total_latency_seconds,
    )

    return DebugResponse(
        complexity=result.routing.complexity,
        confidence=result.routing.confidence,
        reasoning=result.routing.reasoning,
        reproduced=result.reproduction.reproduced,
        root_cause=result.trace.root_cause if result.trace else None,
        fixed_code=result.fix.fixed_code if result.fix else None,
        fix_success=bool(result.fix and result.fix.success),
        review=result.review.explanation if result.review else None,
        total_tokens_used=result.total_tokens_used,
        tokens_saved=result.tokens_saved,
        savings_percent=result.savings_percent,
        latency_seconds=result.total_latency_seconds,
        source=result.routing.raw_response.source,
    )


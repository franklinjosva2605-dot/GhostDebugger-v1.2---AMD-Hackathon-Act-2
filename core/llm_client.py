"""
core/llm_client.py

Unified LLM client for GhostDebugger's 4-tier model routing system.

Tiers (per GhostDebugger spec):
    1. Qwen 1.5B   -> Complexity Router (cheap, fast classification)
    2. Llama 8B    -> Logic-error fixes (mid-complexity)
    3. Llama 70B   -> Architecture-level fixes (high complexity)
    4. Ollama local -> Offline fallback when no API key / API failure

All cloud tiers are served through Fireworks AI's OpenAI-compatible REST API.
If FIREWORKS_API_KEY is missing or a call fails, the client transparently
falls back to a local Ollama model (if available) or a deterministic mock
response, so the rest of the pipeline keeps working without any keys.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 2

# FIX (v1.1): Cache Ollama availability for 30s instead of forever.
# Previously _ollama_available was set once and never re-checked, causing
# stale "available" state if Ollama went down mid-session (2s timeout on
# every subsequent call, silently degrading demo performance).
_OLLAMA_CACHE_TTL_SECONDS = 30


class ModelTier(str, Enum):
    ROUTER = "router"          # Qwen 1.5B
    LOGIC = "logic"            # Llama 8B
    ARCHITECTURE = "architecture"  # Llama 70B
    LOCAL = "local"            # Ollama fallback


FIREWORKS_MODELS = {
    ModelTier.ROUTER: "accounts/fireworks/models/llama-v3p1-8b-instruct",
    ModelTier.LOGIC: "accounts/fireworks/models/llama-v3p1-8b-instruct",
    ModelTier.ARCHITECTURE: "accounts/fireworks/models/llama-v3p3-70b-instruct",
}

OLLAMA_MODELS = {
    ModelTier.ROUTER: "qwen2.5:1.5b",
    ModelTier.LOGIC: "llama3.1:8b",
    ModelTier.ARCHITECTURE: "llama3.1:70b",
    ModelTier.LOCAL: "llama3.1:8b",
}

APPROX_TOKENS_PER_CALL = {
    ModelTier.ROUTER: 50,
    ModelTier.LOGIC: 350,
    ModelTier.ARCHITECTURE: 800,
    ModelTier.LOCAL: 400,
}


@dataclass
class LLMResponse:
    text: str
    tier_used: ModelTier
    model_name: str
    tokens_used: int
    latency_seconds: float
    used_fallback: bool
    source: str  # "fireworks", "ollama", or "mock"


class LLMClient:
    """Routes chat completions to the correct tier, with graceful fallback."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ((lambda: __import__("streamlit").secrets.get("FIREWORKS_API_KEY", "") if hasattr(__import__("streamlit"), "secrets") else "")() or os.environ.get("FIREWORKS_API_KEY", ""))
        self._ollama_available: Optional[bool] = None
        self._ollama_checked_at: float = 0.0  # FIX: TTL cache timestamp

    # ---------------------------------------------------------------- #
    # Public API
    # ---------------------------------------------------------------- #

    def complete(
        self,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        start = time.time()

        if self.api_key:
            try:
                text, tokens = self._call_fireworks(
                    tier, system_prompt, user_prompt, max_tokens, temperature, json_mode
                )
                return LLMResponse(
                    text=text,
                    tier_used=tier,
                    model_name=FIREWORKS_MODELS.get(tier, FIREWORKS_MODELS[ModelTier.LOGIC]),
                    tokens_used=tokens,
                    latency_seconds=time.time() - start,
                    used_fallback=False,
                    source="fireworks",
                )
            except Exception as exc:  # noqa: BLE001
                import traceback; traceback.print_exc()
                last_error = exc
        else:
            last_error = RuntimeError("FIREWORKS_API_KEY not set")

        if self._check_ollama():
            try:
                text, tokens = self._call_ollama(tier, system_prompt, user_prompt, max_tokens, temperature)
                return LLMResponse(
                    text=text,
                    tier_used=ModelTier.LOCAL,
                    model_name=OLLAMA_MODELS.get(tier, OLLAMA_MODELS[ModelTier.LOCAL]),
                    tokens_used=tokens,
                    latency_seconds=time.time() - start,
                    used_fallback=True,
                    source="ollama",
                )
            except Exception as exc:  # noqa: BLE001
                import traceback; traceback.print_exc()
                last_error = exc

        text = self._mock_response(tier, system_prompt, user_prompt, json_mode)
        return LLMResponse(
            text=text,
            tier_used=tier,
            model_name="mock-offline",
            tokens_used=APPROX_TOKENS_PER_CALL.get(tier, 200),
            latency_seconds=time.time() - start,
            used_fallback=True,
            source="mock",
        )

    def status(self) -> dict:
        """Report which backends are currently reachable, for the UI sidebar."""
        return {
            "fireworks_configured": bool(self.api_key),
            "ollama_available": self._check_ollama(),
        }

    # ---------------------------------------------------------------- #
    # Backend implementations
    # ---------------------------------------------------------------- #

    def _call_fireworks(
        self,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, int]:
        model = FIREWORKS_MODELS.get(tier, FIREWORKS_MODELS[ModelTier.LOGIC])
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    FIREWORKS_BASE_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", APPROX_TOKENS_PER_CALL.get(tier, 200))
                return text, tokens
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def _call_ollama(
        self,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, int]:
        model = OLLAMA_MODELS.get(tier, OLLAMA_MODELS[ModelTier.LOCAL])
        payload = {
            "model": model,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        approx_tokens = max(len(text.split()) * 2, 50)
        return text, approx_tokens

    def _check_ollama(self) -> bool:
        """Check Ollama reachability with a 30-second TTL cache.

        FIX (v1.1): The original cached the result permanently. If Ollama
        went down after the first check, the client would keep routing to it
        and hitting 2s timeouts on every call until restart.
        """
        now = time.time()
        if self._ollama_available is not None and (now - self._ollama_checked_at) < _OLLAMA_CACHE_TTL_SECONDS:
            return self._ollama_available

        try:
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
            self._ollama_available = resp.status_code == 200
        except Exception:  # noqa: BLE001
            self._ollama_available = False

        self._ollama_checked_at = now
        return self._ollama_available

    @staticmethod
    def _mock_response(tier: ModelTier, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        if json_mode:
            return json.dumps(
                {
                    "complexity": "logic",
                    "confidence": 0.62,
                    "reasoning": (
                        "Mock router (no API key configured): defaulting to 'logic' tier. "
                        "Add FIREWORKS_API_KEY to .env for real classification."
                    ),
                }
            )
        return (
            "[MOCK RESPONSE — no FIREWORKS_API_KEY found and no local Ollama model reachable]\n"
            "This is a placeholder so GhostDebugger keeps running end-to-end without credentials.\n"
            "Add your Fireworks AI key to .env (FIREWORKS_API_KEY=...) to get real model output.\n\n"
            f"--- prompt received ---\n{user_prompt[:500]}"
        )










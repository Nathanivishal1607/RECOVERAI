"""Phase 12A-12C — minimal LLM provider abstraction.

    LLMProvider (Protocol)
        -> NvidiaNimProvider   (NVIDIA NIM, OpenAI-compatible chat completions)

The LLM is an explanation-only side channel (see
docs/architecture/decision-flow.md "Where the LLM participates"). No
provider in this module ever computes a recovery probability, EIRV, or
selects a financial action — it only produces natural-language text from
a prompt the caller supplies. See backend/services/explanation.py for the
boundary that keeps it that way.

Configured model (Phase 12C, default ``openai/gpt-oss-20b`` via
NVIDIA_NIM_MODEL): selected after live-testing several NVIDIA NIM
candidates against the real explanation workload — reliable ~10-14s
structured-JSON responses. (Phase 12B's ``moonshotai/kimi-k3`` measured
100s+ per request on this account's NIM entitlements and was swapped
out — see docs/architecture/decision-flow.md.) Text-only, non-streaming,
no reasoning-trace exposure — the explanation layer only needs a short
final answer, not a chat/vision/agentic capability, so none of a
reasoning model's other capabilities (streaming, high reasoning effort,
image input) are used here. The model is never hardcoded — only read from
config, so swapping it again is a config change, not a code change.

Configuration comes only from environment variables (backend/core/config.py
-> NVIDIA_NIM_API_KEY / NVIDIA_NIM_BASE_URL / NVIDIA_NIM_MODEL). Never
hardcode a key here.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from backend.core.config import settings


class LLMProviderError(RuntimeError):
    """Any provider failure — missing key, network error, timeout, bad
    response shape. Callers MUST treat this as non-fatal: the recovery
    decision path never depends on the LLM being reachable."""


class LLMProvider(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


class NvidiaNimProvider:
    """NVIDIA NIM (OpenAI-compatible ``/chat/completions``). Default model
    is ``openai/gpt-oss-20b`` (Phase 12C), configurable via
    ``NVIDIA_NIM_MODEL``. A single non-streaming request; only the final
    message content is returned — no reasoning trace, no chain-of-thought."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = settings.nvidia_nim_api_key if api_key is None else api_key
        self.base_url = (base_url or settings.nvidia_nim_base_url).rstrip("/")
        self.model = model or settings.nvidia_nim_model
        self.timeout_seconds = (
            settings.llm_request_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise LLMProviderError("NVIDIA_NIM_API_KEY is not configured")
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    # Conservative, deterministic-leaning settings for a
                    # short business explanation — not the vendor sample's
                    # temperature=1 / reasoning_effort="max" / stream=True,
                    # none of which this text-only, single-shot use case
                    # needs, and the latter two would risk exposing
                    # reasoning-trace content or adding needless latency.
                    "temperature": 0.2,
                    "max_tokens": 800,
                    "stream": False,
                },
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            body = resp.json()
            return body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"NVIDIA NIM request failed: {exc}") from exc

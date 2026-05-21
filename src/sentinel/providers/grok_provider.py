"""xAI Grok provider adapter.

xAI's API is OpenAI-compatible — same request/response shape, just a
different base URL (https://api.x.ai/v1) and API key format (xai-...).
We reuse the OpenAI SDK pointed at xAI's endpoint rather than writing
a bespoke HTTP client.

Supported models: grok-3, grok-3-fast, grok-3-mini, grok-3-mini-fast.
"""

from __future__ import annotations

import time

import httpx
import openai

from sentinel.core.config import get_settings
from sentinel.core.errors import ProviderError, ProviderTimeoutError
from sentinel.core.logging import get_logger
from sentinel.providers.base import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    Usage,
)
from sentinel.providers.pricing import price_for

log = get_logger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-3-mini"


class GrokProvider(LLMProvider):
    """xAI Grok via the OpenAI-compatible endpoint."""

    name = "grok"

    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        settings = get_settings()
        key = api_key or (
            settings.xai_api_key.get_secret_value() if settings.xai_api_key else None
        )
        if not key:
            raise ProviderError("xAI API key not configured (set XAI_API_KEY)")

        self._client = openai.AsyncOpenAI(
            api_key=key,
            base_url=XAI_BASE_URL,
            timeout=timeout or settings.request_timeout_seconds,
            max_retries=0,  # Gateway handles retries.
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        start = time.perf_counter()

        # Map model name: if caller sends a claude/gpt model as failover,
        # remap to grok-3-mini as a safe default.
        model = request.model if request.model.startswith("grok-") else DEFAULT_MODEL

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(
            {"role": m.role, "content": m.content} for m in request.messages
        )

        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        except openai.APITimeoutError as e:
            raise ProviderTimeoutError("Grok request timed out") from e
        except openai.APIStatusError as e:
            raise ProviderError(
                f"Grok returned {e.status_code}",
                details={"status": e.status_code},
            ) from e
        except (openai.APIConnectionError, httpx.HTTPError) as e:
            raise ProviderError("Grok connection failed") from e

        latency_ms = int((time.perf_counter() - start) * 1000)
        choice = resp.choices[0]
        usage_obj = resp.usage

        if usage_obj is None:
            usage = Usage(input_tokens=0, output_tokens=0)
        else:
            usage = Usage(
                input_tokens=usage_obj.prompt_tokens,
                output_tokens=usage_obj.completion_tokens,
            )

        cost = price_for(model).cost(usage.input_tokens, usage.output_tokens)

        log.info(
            "grok.complete",
            model=model,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )

        return CompletionResponse(
            id=resp.id,
            model=resp.model,
            provider=self.name,
            content=choice.message.content or "",
            stop_reason=choice.finish_reason,
            usage=usage,
            cost_usd=cost,
        )

    async def health(self) -> bool:
        return True

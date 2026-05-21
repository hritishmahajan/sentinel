"""Anthropic provider adapter.

Wraps the official Anthropic SDK behind the ``LLMProvider`` protocol.
Translates timeouts, rate limits, and API errors into the gateway's
typed exception hierarchy so the rest of the system stays
provider-agnostic.
"""

from __future__ import annotations

import time

import anthropic
import httpx

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


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        settings = get_settings()
        key = api_key or (
            settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        )
        if not key:
            raise ProviderError("Anthropic API key not configured")

        self._client = anthropic.AsyncAnthropic(
            api_key=key,
            timeout=timeout or settings.request_timeout_seconds,
            max_retries=0,  # We do our own retries at the gateway level.
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        start = time.perf_counter()
        try:
            resp = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=request.system or anthropic.NOT_GIVEN,
                messages=[{"role": m.role, "content": m.content} for m in request.messages],
            )
        except anthropic.APITimeoutError as e:
            raise ProviderTimeoutError(
                "Anthropic request timed out", details={"timeout_s": e.request.read}
            ) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(
                f"Anthropic returned {e.status_code}",
                details={"status": e.status_code, "body": str(e.response.text)[:500]},
            ) from e
        except (anthropic.APIConnectionError, httpx.HTTPError) as e:
            raise ProviderError("Anthropic connection failed") from e

        latency_ms = int((time.perf_counter() - start) * 1000)

        # The Messages API returns a list of content blocks; concat text parts.
        text = "".join(block.text for block in resp.content if block.type == "text")

        usage = Usage(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        cost = price_for(request.model).cost(usage.input_tokens, usage.output_tokens)

        log.info(
            "anthropic.complete",
            model=request.model,
            latency_ms=latency_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )

        return CompletionResponse(
            id=resp.id,
            model=resp.model,
            provider=self.name,
            content=text,
            stop_reason=resp.stop_reason,
            usage=usage,
            cost_usd=cost,
        )

    async def health(self) -> bool:
        """Lightweight health check — does NOT call the API to avoid cost.

        We rely on the circuit breaker for upstream-failure detection.
        Returning True here just means "the adapter is constructed."
        """
        return True

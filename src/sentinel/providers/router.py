"""Provider router.

Given a normalized request, this picks a provider, attempts the call
through its circuit breaker, retries with backoff on transient errors,
and falls over to the next provider in the chain if the primary is
unavailable.

Routing rules (v1):
- Explicit model prefix wins: ``claude-*`` -> Anthropic, ``gpt-*`` -> OpenAI.
- If the chosen provider's circuit is open, fall back to the alternate
  provider with a model mapping (best-effort, logged).
- If all providers are unavailable, raise ``CircuitOpenError``.
"""

from __future__ import annotations

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sentinel.core.errors import CircuitOpenError, ProviderError, ProviderTimeoutError
from sentinel.core.logging import get_logger
from sentinel.providers.base import CompletionRequest, CompletionResponse, LLMProvider
from sentinel.providers.circuit_breaker import CircuitBreaker

log = get_logger(__name__)


class ProviderRouter:
    def __init__(
        self,
        providers: dict[str, LLMProvider],
        breakers: dict[str, CircuitBreaker],
        max_retries: int = 2,
    ) -> None:
        self._providers = providers
        self._breakers = breakers
        self._max_retries = max_retries

    @staticmethod
    def _provider_for_model(model: str) -> str:
        """Map a model name to a provider by prefix."""
        if model.startswith(("claude-", "anthropic/")):
            return "anthropic"
        if model.startswith(("gpt-", "o1-", "o3-", "openai/")):
            return "openai"
        if model.startswith(("grok-", "xai/")):
            return "grok"
        # Default: try Anthropic, fall back to grok if anthropic unconfigured.
        return "anthropic"

    async def _try_provider(
        self, provider_name: str, request: CompletionRequest
    ) -> CompletionResponse:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ProviderError(f"Provider not configured: {provider_name}")

        breaker = self._breakers[provider_name]
        if not await breaker.is_callable():
            raise CircuitOpenError(f"Circuit open for {provider_name}")

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries + 1),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
                retry=retry_if_exception_type(ProviderTimeoutError),
                reraise=True,
            ):
                with attempt:
                    response = await provider.complete(request)
            await breaker.record_success()
            return response
        except ProviderError:
            await breaker.record_failure()
            raise

    async def route(self, request: CompletionRequest) -> CompletionResponse:
        """Route a request, with one failover attempt to the alternate provider."""
        primary = self._provider_for_model(request.model)

        try:
            return await self._try_provider(primary, request)
        except (ProviderError, CircuitOpenError) as primary_err:
            alternate = "openai" if primary == "anthropic" else "anthropic"

            if alternate not in self._providers:
                log.warning("router.no_failover", primary=primary, error=str(primary_err))
                raise

            log.warning(
                "router.failover",
                primary=primary,
                alternate=alternate,
                reason=str(primary_err),
            )

            # Best-effort model mapping for failover. Keep it simple: caller
            # gets logged that the model was substituted.
            failover_model = _failover_model_map(request.model, alternate)
            failover_request = request.model_copy(update={"model": failover_model})

            return await self._try_provider(alternate, failover_request)


def _failover_model_map(original_model: str, target_provider: str) -> str:
    """Map a model to a comparable one on a different provider.

    This is intentionally minimal — a real production system would
    have a richer capability matrix.
    """
    mapping = {
        # Anthropic -> OpenAI
        ("claude-opus-4-5", "openai"): "gpt-4o",
        ("claude-sonnet-4-5", "openai"): "gpt-4o",
        ("claude-haiku-4-5", "openai"): "gpt-4o-mini",
        # Anthropic -> Grok
        ("claude-opus-4-5", "grok"): "grok-3",
        ("claude-sonnet-4-5", "grok"): "grok-3",
        ("claude-haiku-4-5", "grok"): "grok-3-mini",
        # OpenAI -> Anthropic
        ("gpt-4o", "anthropic"): "claude-sonnet-4-5",
        ("gpt-4o-mini", "anthropic"): "claude-haiku-4-5",
        # OpenAI -> Grok
        ("gpt-4o", "grok"): "grok-3",
        ("gpt-4o-mini", "grok"): "grok-3-mini",
        # Grok -> Anthropic
        ("grok-3", "anthropic"): "claude-sonnet-4-5",
        ("grok-3-mini", "anthropic"): "claude-haiku-4-5",
        # Grok -> OpenAI
        ("grok-3", "openai"): "gpt-4o",
        ("grok-3-mini", "openai"): "gpt-4o-mini",
    }
    defaults = {
        "anthropic": "claude-sonnet-4-5",
        "openai": "gpt-4o",
        "grok": "grok-3-mini",
    }
    return mapping.get(
        (original_model, target_provider),
        defaults.get(target_provider, "grok-3-mini"),
    )

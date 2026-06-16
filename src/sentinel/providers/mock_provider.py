"""Mock LLM provider for local development without API keys."""

from __future__ import annotations

import uuid

from sentinel.providers.base import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    Usage,
)

RESPONSES = [
    (
        "Hello! I am Sentinel mock provider. "
        "The full pipeline is working: auth, policy, PII redaction, "
        "audit logging, and Prometheus metrics all fired on this request."
    ),
    (
        "Hi there! This response came through Sentinel governed LLM gateway. "
        "Every request is rate-limited, policy-checked, injection-scanned, "
        "and audit-logged."
    ),
    (
        "Greetings! Sentinel is running locally. "
        "Multi-provider routing, circuit breakers, and the admin dashboard "
        "are all operational."
    ),
    (
        "Hello! The gateway is live. "
        "Try the dashboard at http://localhost:8000/console "
        "to see this request in the audit log."
    ),
]

_counter = 0


class MockProvider(LLMProvider):
    """Drop-in mock provider — no API key needed, instant responses."""

    name = "mock"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        global _counter
        content = RESPONSES[_counter % len(RESPONSES)]
        _counter += 1
        input_tokens = len(" ".join(m.content for m in request.messages).split()) * 2
        output_tokens = len(content.split()) * 2
        return CompletionResponse(
            id=f"mock_{uuid.uuid4().hex[:16]}",
            model=request.model,
            provider=self.name,
            content=content,
            stop_reason="end_turn",
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            cost_usd=0.0,
        )

    async def health(self) -> bool:
        return True

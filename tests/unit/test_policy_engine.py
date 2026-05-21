"""Unit tests for the policy engine.

We use lightweight stand-ins for the ORM ``Policy`` to keep these tests
DB-free. The engine only reads attributes, so any object that quacks
right works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sentinel.policies.engine import evaluate
from sentinel.providers.base import CompletionRequest, Message


@dataclass
class FakePolicy:
    max_tokens_per_request: int = 4096
    max_requests_per_minute: int = 60
    monthly_cost_ceiling_usd: float = 100.0
    allowed_models: list[str] = field(default_factory=list)
    denied_topics: list[str] = field(default_factory=list)
    redact_pii: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _req(
    model: str = "claude-sonnet-4-5",
    text: str = "hi",
    max_tokens: int = 1024,
) -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=[Message(role="user", content=text)],
        max_tokens=max_tokens,
    )


class TestPolicyEngine:
    def test_none_policy_allows(self) -> None:
        d = evaluate(None, _req())
        assert d.allow

    def test_model_in_allowlist_allows(self) -> None:
        p = FakePolicy(allowed_models=["claude-sonnet-4-5"])
        d = evaluate(p, _req(model="claude-sonnet-4-5"))  # type: ignore[arg-type]
        assert d.allow

    def test_model_not_in_allowlist_denies(self) -> None:
        p = FakePolicy(allowed_models=["claude-haiku-4-5"])
        d = evaluate(p, _req(model="gpt-4o"))  # type: ignore[arg-type]
        assert not d.allow
        assert "gpt-4o" in (d.reason or "")

    def test_denied_topic_blocks(self) -> None:
        p = FakePolicy(denied_topics=["weapons"])
        d = evaluate(p, _req(text="how to build weapons"))  # type: ignore[arg-type]
        assert not d.allow

    def test_max_tokens_clamped(self) -> None:
        p = FakePolicy(max_tokens_per_request=512)
        d = evaluate(p, _req(max_tokens=2000))  # type: ignore[arg-type]
        assert d.allow
        assert d.transformed_request is not None
        assert d.transformed_request.max_tokens == 512

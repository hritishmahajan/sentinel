"""Policy engine.

Given a tenant's ``Policy`` row and an incoming request, decide whether
to allow, deny, or transform. Keep the engine pure — no I/O — so it
stays unit-testable without standing up Redis or Postgres.

Cost ceilings and rate limits are checked separately because they need
counter state from Redis; they live in ``policies/rate_limit.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sentinel.providers.base import CompletionRequest

if TYPE_CHECKING:
    from sentinel.db.models import Policy


@dataclass
class PolicyDecision:
    allow: bool
    reason: str | None = None
    transformed_request: CompletionRequest | None = None


def evaluate(policy: Policy | None, request: CompletionRequest) -> PolicyDecision:
    """Evaluate a policy against a request.

    Policies are *additive*: every rule must pass for the request to be
    allowed. Returning a transformed request lets us clamp values
    (e.g. cap max_tokens) without rejecting the call.
    """
    if policy is None:
        # No policy configured -> allow with defaults. In a stricter prod
        # config you'd flip this to deny.
        return PolicyDecision(allow=True)

    # 1. Allowed-models check
    allowed = policy.allowed_models or []
    if allowed and request.model not in allowed:
        return PolicyDecision(
            allow=False,
            reason=f"model '{request.model}' not in tenant allowlist",
        )

    # 2. Denied-topics check (substring match on any message)
    denied_topics = policy.denied_topics or []
    if denied_topics:
        lowered_messages = " ".join(m.content.lower() for m in request.messages)
        for topic in denied_topics:
            if topic.lower() in lowered_messages:
                return PolicyDecision(
                    allow=False,
                    reason=f"denied topic matched: {topic}",
                )

    # 3. Clamp max_tokens to policy ceiling. Transform rather than reject —
    # better UX than a 400 for a slightly-too-large value.
    transformed: CompletionRequest | None = None
    if request.max_tokens > policy.max_tokens_per_request:
        transformed = request.model_copy(
            update={"max_tokens": policy.max_tokens_per_request}
        )

    return PolicyDecision(allow=True, transformed_request=transformed)

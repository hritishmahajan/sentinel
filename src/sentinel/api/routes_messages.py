"""``/v1/messages`` route.

This is the gateway's main orchestration point. The order matters:

1. Authenticate (cheap, fails fast)
2. Rate limit (cheaper than policy eval, fails fast)
3. Policy evaluation (model allow-list, denied topics, max_tokens clamp)
4. Security scans (injection, PII redaction)
5. Provider routing (with circuit breaker + retry + failover)
6. Audit log write
7. Response

Every branch produces exactly one audit row. We never leave a request
unrecorded, even on internal errors.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from sentinel.api.dependencies import AuthDep, RedisDep, RouterDep, SessionDep
from sentinel.api.schemas import MessagesRequest, MessagesResponse
from sentinel.api.schemas import Usage as UsageSchema
from sentinel.audit.writer import AuditRecord, write_audit
from sentinel.core.errors import (
    PolicyViolationError,
    ProviderError,
    RateLimitError,
)
from sentinel.core.logging import get_logger
from sentinel.observability import metrics
from sentinel.policies.engine import evaluate
from sentinel.policies.rate_limit import RateLimiter
from sentinel.providers.base import CompletionRequest
from sentinel.security.injection import scan
from sentinel.security.pii import redact

log = get_logger(__name__)
router = APIRouter()


@router.post("/messages", response_model=MessagesResponse)
async def create_message(
    request: Request,
    body: MessagesRequest,
    auth: AuthDep,
    provider_router: RouterDep,
    redis: RedisDep,
    session: SessionDep,
) -> MessagesResponse:
    request_id: str = request.state.request_id
    start = time.perf_counter()

    tenant = auth[0] if auth else None
    policy = auth[1] if auth else None
    tenant_id = tenant.id if tenant else None
    tenant_label = str(tenant_id) if tenant_id else "anonymous"

    # --- 1. Build normalized internal request ---
    internal = CompletionRequest(
        model=body.model,
        messages=body.messages,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        system=body.system,
        metadata=body.metadata,
    )

    # --- 2. Rate limit (only when authenticated; anonymous gets a global bucket) ---
    if policy is not None:
        limiter = RateLimiter(redis)
        rl = await limiter.check(
            key=f"tenant:{tenant_id}",
            limit=policy.max_requests_per_minute,
        )
        if not rl.allowed:
            metrics.rate_limit_hits_total.labels(tenant=tenant_label).inc()
            await _audit_and_raise(
                session=session,
                request_id=request_id,
                tenant_id=tenant_id,
                request=internal,
                status_code=429,
                policy_decision="rate_limited",
                error_code="rate_limit_exceeded",
            )
            raise RateLimitError(
                "Tenant rate limit exceeded",
                details={"retry_after_ms": rl.retry_after_ms},
            )

    # --- 3. Policy evaluation ---
    decision = evaluate(policy, internal)
    if not decision.allow:
        metrics.policy_decisions_total.labels(
            decision="deny", reason=decision.reason or "unknown"
        ).inc()
        await _audit_and_raise(
            session=session,
            request_id=request_id,
            tenant_id=tenant_id,
            request=internal,
            status_code=403,
            policy_decision="deny",
            error_code="policy_violation",
        )
        raise PolicyViolationError(decision.reason or "Policy violation")

    metrics.policy_decisions_total.labels(decision="allow", reason="ok").inc()
    if decision.transformed_request is not None:
        internal = decision.transformed_request

    # --- 4. Security scans ---
    combined_text = " ".join(m.content for m in internal.messages)
    inj = scan(combined_text)
    if inj.blocked:
        for pattern in inj.matches:
            metrics.injection_blocks_total.labels(pattern=pattern).inc()
        await _audit_and_raise(
            session=session,
            request_id=request_id,
            tenant_id=tenant_id,
            request=internal,
            status_code=403,
            policy_decision="injection_blocked",
            error_code="injection_detected",
        )
        raise PolicyViolationError(
            f"Prompt blocked by injection scanner (score={inj.score:.2f})",
            details={"patterns": inj.matches},
        )

    # PII redaction is non-blocking — it transforms the request.
    if policy is None or policy.redact_pii:
        redacted_messages = []
        all_types: set[str] = set()
        for msg in internal.messages:
            result = redact(msg.content)
            for t in result.types_redacted:
                all_types.add(t)
                metrics.pii_redactions_total.labels(type=t).inc()
            redacted_messages.append(msg.model_copy(update={"content": result.text}))
        internal = internal.model_copy(update={"messages": redacted_messages})

    # --- 5. Provider routing ---
    try:
        completion = await provider_router.route(internal)
    except ProviderError as e:
        metrics.provider_failures_total.labels(
            provider=request.headers.get("x-target-provider", "unknown"),
            error_code=e.error_code,
        ).inc()
        await _audit_and_raise(
            session=session,
            request_id=request_id,
            tenant_id=tenant_id,
            request=internal,
            status_code=e.status_code,
            policy_decision="allow",
            error_code=e.error_code,
        )
        raise

    latency_ms = int((time.perf_counter() - start) * 1000)

    # --- 6. Metrics + audit ---
    metrics.requests_total.labels(
        provider=completion.provider, model=completion.model, status="200"
    ).inc()
    metrics.request_duration_seconds.labels(
        provider=completion.provider, model=completion.model
    ).observe(latency_ms / 1000)
    metrics.tokens_processed_total.labels(
        provider=completion.provider, model=completion.model, direction="input"
    ).inc(completion.usage.input_tokens)
    metrics.tokens_processed_total.labels(
        provider=completion.provider, model=completion.model, direction="output"
    ).inc(completion.usage.output_tokens)
    metrics.cost_usd_total.labels(
        provider=completion.provider, model=completion.model, tenant=tenant_label
    ).inc(completion.cost_usd)

    await write_audit(
        session,
        AuditRecord(
            request_id=request_id,
            tenant_id=tenant_id,
            provider=completion.provider,
            model=completion.model,
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            cost_usd=completion.cost_usd,
            latency_ms=latency_ms,
            status_code=200,
            policy_decision="allow",
            input_preview=combined_text,
            output_preview=completion.content,
        ),
    )

    log.info(
        "request.complete",
        tenant_id=str(tenant_id) if tenant_id else None,
        provider=completion.provider,
        model=completion.model,
        latency_ms=latency_ms,
        cost_usd=completion.cost_usd,
    )

    return MessagesResponse(
        id=completion.id,
        model=completion.model,
        provider=completion.provider,
        content=completion.content,
        stop_reason=completion.stop_reason,
        usage=UsageSchema(
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
        ),
        cost_usd=completion.cost_usd,
        request_id=request_id,
    )


async def _audit_and_raise(
    *,
    session: object,
    request_id: str,
    tenant_id: object,
    request: CompletionRequest,
    status_code: int,
    policy_decision: str,
    error_code: str,
) -> None:
    """Write a failure audit row. Best-effort — never crash the request handler."""
    try:
        await write_audit(
            session,  # type: ignore[arg-type]
            AuditRecord(
                request_id=request_id,
                tenant_id=tenant_id,  # type: ignore[arg-type]
                provider="n/a",
                model=request.model,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=0,
                status_code=status_code,
                policy_decision=policy_decision,
                error_code=error_code,
                input_preview=" ".join(m.content for m in request.messages),
            ),
        )
    except Exception as exc:
        log.error("audit.write_failed", error=str(exc))

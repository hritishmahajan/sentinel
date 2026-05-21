"""Audit log writer.

Every request that reaches the gateway produces exactly one audit row,
regardless of whether it succeeded, was denied by policy, or failed at
the upstream. This is the system of record for governance review.

Writes happen on the request critical path because losing an audit row
is worse than adding ~2ms of latency. If write throughput becomes an
issue, the right move is a Kafka producer here with a separate consumer
upserting to Postgres — not skipping rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.core.logging import get_logger
from sentinel.db.models import AuditLog

log = get_logger(__name__)

PREVIEW_CHARS = 500


@dataclass
class AuditRecord:
    request_id: str
    tenant_id: uuid.UUID | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status_code: int
    policy_decision: str = "allow"
    error_code: str | None = None
    input_preview: str | None = None
    output_preview: str | None = None
    request_metadata: dict[str, Any] | None = None


def _truncate(text: str | None) -> str | None:
    if text is None:
        return None
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + "…[truncated]"


async def write_audit(session: AsyncSession, record: AuditRecord) -> None:
    """Insert an audit row. Caller's transaction commits it."""
    row = AuditLog(
        request_id=record.request_id,
        tenant_id=record.tenant_id,
        provider=record.provider,
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_usd=record.cost_usd,
        latency_ms=record.latency_ms,
        status_code=record.status_code,
        policy_decision=record.policy_decision,
        error_code=record.error_code,
        input_preview=_truncate(record.input_preview),
        output_preview=_truncate(record.output_preview),
        request_metadata=record.request_metadata or {},
    )
    session.add(row)
    log.debug(
        "audit.write",
        request_id=record.request_id,
        status=record.status_code,
        cost_usd=record.cost_usd,
    )

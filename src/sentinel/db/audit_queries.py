"""Audit log query functions.

Read-only queries over the append-only audit_logs table. Designed for
the admin dashboard and compliance export use-cases.

Pagination uses keyset (cursor) pagination via ``before_id`` rather than
OFFSET — OFFSET degrades to O(n) on large tables. The audit log can grow
very large in production; keyset pagination stays O(log n).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.db.models import AuditLog


@dataclass
class AuditPage:
    rows: list[AuditLog]
    total: int
    has_more: bool


async def query_audit_logs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    provider: str | None = None,
    status_code: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = 50,
    cursor: uuid.UUID | None = None,  # last-seen id for keyset pagination
) -> AuditPage:
    """Query the audit log with optional filters. Returns a page of results."""

    base = select(AuditLog)

    if tenant_id is not None:
        base = base.where(AuditLog.tenant_id == tenant_id)
    if provider is not None:
        base = base.where(AuditLog.provider == provider)
    if status_code is not None:
        base = base.where(AuditLog.status_code == status_code)
    if after is not None:
        base = base.where(AuditLog.created_at >= after)
    if before is not None:
        base = base.where(AuditLog.created_at <= before)
    if cursor is not None:
        # Keyset: fetch rows older than the cursor row
        cursor_row = await session.get(AuditLog, cursor)
        if cursor_row is not None:
            base = base.where(AuditLog.created_at < cursor_row.created_at)

    # Count before applying limit
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    # Fetch one extra to detect has_more
    rows_stmt = base.order_by(AuditLog.created_at.desc()).limit(limit + 1)
    rows = list((await session.execute(rows_stmt)).scalars().all())

    has_more = len(rows) > limit
    return AuditPage(rows=rows[:limit], total=total, has_more=has_more)


@dataclass
class CostSummary:
    tenant_id: uuid.UUID | None
    total_requests: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    avg_latency_ms: float


async def cost_summary(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
) -> list[CostSummary]:
    """Per-tenant cost aggregation for the billing dashboard."""
    stmt = select(
        AuditLog.tenant_id,
        func.count(AuditLog.id).label("total_requests"),
        func.sum(AuditLog.cost_usd).label("total_cost_usd"),
        func.sum(AuditLog.input_tokens).label("total_input_tokens"),
        func.sum(AuditLog.output_tokens).label("total_output_tokens"),
        func.avg(AuditLog.latency_ms).label("avg_latency_ms"),
    ).where(AuditLog.status_code == 200)

    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    if after is not None:
        stmt = stmt.where(AuditLog.created_at >= after)
    if before is not None:
        stmt = stmt.where(AuditLog.created_at <= before)

    stmt = stmt.group_by(AuditLog.tenant_id).order_by(
        func.sum(AuditLog.cost_usd).desc()
    )

    result = await session.execute(stmt)
    return [
        CostSummary(
            tenant_id=row.tenant_id,
            total_requests=row.total_requests,
            total_cost_usd=float(row.total_cost_usd or 0),
            total_input_tokens=row.total_input_tokens or 0,
            total_output_tokens=row.total_output_tokens or 0,
            avg_latency_ms=float(row.avg_latency_ms or 0),
        )
        for row in result
    ]

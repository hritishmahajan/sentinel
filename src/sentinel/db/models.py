"""ORM models for tenants, API keys, policies, and audit logs.

Design notes:
- All tables have created_at/updated_at, set server-side.
- Tenant is the unit of isolation; every other row carries tenant_id.
- API keys are stored as bcrypt hashes; the plaintext is shown to the user
  exactly once at creation.
- Audit log is append-only — never updated, only inserted. Indexed on
  (tenant_id, created_at) for dashboard time-range queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sentinel.db.session import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    """Adds server-side created_at / updated_at to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="tenant")
    policy: Mapped[Policy | None] = relationship(back_populates="tenant", uselist=False)


class ApiKey(Base, TimestampMixin):
    """API key for a tenant. Stored as a bcrypt hash; prefix stored plain for lookups."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # First 8 chars of the key, e.g. "sk_live_". Used as an index for fast lookup.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="default", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")


class Policy(Base, TimestampMixin):
    """Per-tenant governance policy.

    Stored as a structured row plus a JSONB blob for forward-compatible
    additions without migrations. Keep hot-path fields as columns.
    """

    __tablename__ = "policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    max_tokens_per_request: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    max_requests_per_minute: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    monthly_cost_ceiling_usd: Mapped[float] = mapped_column(
        Numeric(10, 2), default=100.00, nullable=False
    )
    allowed_models: Mapped[dict] = mapped_column(JSONB, default=list, nullable=False)
    denied_topics: Mapped[dict] = mapped_column(JSONB, default=list, nullable=False)
    redact_pii: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="policy")


class AuditLog(Base):
    """Append-only audit record for every gateway request.

    No updated_at on purpose — these rows are immutable. The (tenant_id,
    created_at) composite index supports the dashboard's most common
    query: "show me last 24h for tenant X".
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "created_at"),
        Index("ix_audit_request_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(32), default="allow", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))

    # Truncated input/output for compliance review. NEVER store full content
    # without explicit tenant opt-in (data residency considerations).
    input_preview: Mapped[str | None] = mapped_column(Text)
    output_preview: Mapped[str | None] = mapped_column(Text)

    request_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

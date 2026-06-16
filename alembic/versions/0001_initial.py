"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Inspector

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _is_sqlite() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "sqlite"


def _uuid_col(name: str, **kwargs):
    """UUID column — TEXT on SQLite, UUID on Postgres."""
    if _is_sqlite():
        return sa.Column(name, sa.String(36), **kwargs)
    from sqlalchemy.dialects.postgresql import UUID
    return sa.Column(name, UUID(as_uuid=True), **kwargs)


def _uuid_fk(name: str, fk: str, **kwargs):
    if _is_sqlite():
        return sa.Column(name, sa.String(36), sa.ForeignKey(fk, ondelete="CASCADE"), **kwargs)
    from sqlalchemy.dialects.postgresql import UUID
    return sa.Column(name, UUID(as_uuid=True), sa.ForeignKey(fk, ondelete="CASCADE"), **kwargs)


def upgrade() -> None:
    op.create_table(
        "tenants",
        _uuid_col("id", primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1" if _is_sqlite() else "true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "api_keys",
        _uuid_col("id", primary_key=True),
        _uuid_fk("tenant_id", "tenants.id", nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False, index=True),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default="default"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1" if _is_sqlite() else "true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "policies",
        _uuid_col("id", primary_key=True),
        _uuid_fk("tenant_id", "tenants.id", unique=True, nullable=False),
        sa.Column("max_tokens_per_request", sa.Integer, nullable=False, server_default="4096"),
        sa.Column("max_requests_per_minute", sa.Integer, nullable=False, server_default="60"),
        sa.Column("monthly_cost_ceiling_usd", sa.Numeric(10, 2), nullable=False, server_default="100.00"),
        sa.Column("allowed_models", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("denied_topics", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("redact_pii", sa.Boolean, nullable=False, server_default="1" if _is_sqlite() else "true"),
        sa.Column("extra", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit_logs",
        _uuid_col("id", primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        _uuid_col("tenant_id"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("policy_decision", sa.String(32), nullable=False, server_default="allow"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("input_preview", sa.Text),
        sa.Column("output_preview", sa.Text),
        sa.Column("request_metadata", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_tenant_time", "audit_logs", ["tenant_id", "created_at"])
    op.create_index("ix_audit_request_id", "audit_logs", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_tenant_time", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("policies")
    op.drop_table("api_keys")
    op.drop_table("tenants")

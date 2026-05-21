"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False, index=True),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default="default"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("max_tokens_per_request", sa.Integer, nullable=False, server_default="4096"),
        sa.Column("max_requests_per_minute", sa.Integer, nullable=False, server_default="60"),
        sa.Column("monthly_cost_ceiling_usd", sa.Numeric(10, 2), nullable=False, server_default="100.00"),
        sa.Column("allowed_models", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("denied_topics", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("redact_pii", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("extra", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
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

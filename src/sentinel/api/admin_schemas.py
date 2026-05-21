"""Admin API schemas.

Separate from the LLM-facing schemas in api/schemas.py to keep the two
surfaces independently versionable. The admin API is internal (accessed
by operators / CI), not exposed to end-user developers via the SDK.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# ---- Tenants ----


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="Unique tenant name.")


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- API Keys ----


class CreateApiKeyRequest(BaseModel):
    label: str = Field(default="default", max_length=255)


class ApiKeyResponse(BaseModel):
    """Returned on creation only — plaintext is shown exactly once."""

    id: uuid.UUID
    label: str
    prefix: str
    plaintext: str  # only present on creation
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyListItem(BaseModel):
    """Safe to return on list — no plaintext."""

    id: uuid.UUID
    label: str
    prefix: str
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- Policies ----


class PolicyRequest(BaseModel):
    max_tokens_per_request: int = Field(default=4096, ge=1, le=16384)
    max_requests_per_minute: int = Field(default=60, ge=1, le=10000)
    monthly_cost_ceiling_usd: float = Field(default=100.0, ge=0)
    allowed_models: list[str] = Field(default_factory=list)
    denied_topics: list[str] = Field(default_factory=list)
    redact_pii: bool = True


class PolicyResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    max_tokens_per_request: int
    max_requests_per_minute: int
    monthly_cost_ceiling_usd: float
    allowed_models: list[str]
    denied_topics: list[str]
    redact_pii: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---- Audit ----


class AuditLogItem(BaseModel):
    id: uuid.UUID
    request_id: str
    tenant_id: uuid.UUID | None
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status_code: int
    policy_decision: str
    error_code: str | None
    input_preview: str | None
    output_preview: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditPageResponse(BaseModel):
    rows: list[AuditLogItem]
    total: int
    has_more: bool


class CostSummaryItem(BaseModel):
    tenant_id: uuid.UUID | None
    total_requests: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    avg_latency_ms: float

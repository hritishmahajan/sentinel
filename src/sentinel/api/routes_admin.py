"""Admin API routes.

These endpoints are for operators, not end-user developers. In a real
deployment they'd sit behind a separate ingress rule with stricter IP
allowlisting or mTLS. For the gateway v1, they're on the same process
but under a distinct /admin prefix that's easy to firewall.

No rate limiting is applied here (operators need bursting access), but
all actions are audit-logged via the standard request middleware.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from sentinel.api.admin_schemas import (
    ApiKeyListItem,
    ApiKeyResponse,
    AuditLogItem,
    AuditPageResponse,
    CostSummaryItem,
    CreateApiKeyRequest,
    CreateTenantRequest,
    PolicyRequest,
    PolicyResponse,
    TenantResponse,
)
from sentinel.api.dependencies import SessionDep
from sentinel.core.errors import ValidationError
from sentinel.db.audit_queries import cost_summary, query_audit_logs
from sentinel.db.repositories import (
    create_api_key,
    create_tenant,
    deactivate_tenant,
    get_policy,
    get_tenant,
    get_tenant_by_name,
    list_api_keys,
    list_tenants,
    revoke_api_key,
    upsert_policy,
)
from sentinel.security.api_keys import generate_api_key

router = APIRouter(prefix="/admin", tags=["admin"])


# ---- Tenants ----


@router.post("/tenants", response_model=TenantResponse, status_code=201)
async def create_tenant_endpoint(
    body: CreateTenantRequest,
    session: SessionDep,
) -> TenantResponse:
    existing = await get_tenant_by_name(session, body.name)
    if existing:
        raise ValidationError(f"Tenant '{body.name}' already exists")

    tenant = await create_tenant(session, body.name)
    return TenantResponse.model_validate(tenant)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants_endpoint(session: SessionDep) -> list[TenantResponse]:
    tenants = await list_tenants(session)
    return [TenantResponse.model_validate(t) for t in tenants]


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant_endpoint(tenant_id: uuid.UUID, session: SessionDep) -> TenantResponse:
    tenant = await get_tenant(session, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantResponse.model_validate(tenant)


@router.delete("/tenants/{tenant_id}", status_code=204)
async def deactivate_tenant_endpoint(tenant_id: uuid.UUID, session: SessionDep) -> None:
    ok = await deactivate_tenant(session, tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Tenant not found")


# ---- API Keys ----


@router.post("/tenants/{tenant_id}/keys", response_model=ApiKeyResponse, status_code=201)
async def issue_api_key(
    tenant_id: uuid.UUID,
    body: CreateApiKeyRequest,
    session: SessionDep,
) -> ApiKeyResponse:
    tenant = await get_tenant(session, tenant_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=404, detail="Tenant not found or inactive")

    generated = generate_api_key()
    key = await create_api_key(session, tenant_id, body.label, generated)

    return ApiKeyResponse(
        id=key.id,
        label=key.label,
        prefix=generated.prefix,
        plaintext=generated.plaintext,  # shown ONCE — caller must store it
        created_at=key.created_at,
    )


@router.get("/tenants/{tenant_id}/keys", response_model=list[ApiKeyListItem])
async def list_tenant_keys(tenant_id: uuid.UUID, session: SessionDep) -> list[ApiKeyListItem]:
    tenant = await get_tenant(session, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    keys = await list_api_keys(session, tenant_id)
    return [
        ApiKeyListItem(
            id=k.id,
            label=k.label,
            prefix=k.key_prefix,
            is_active=k.is_active,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.delete("/tenants/{tenant_id}/keys/{key_id}", status_code=204)
async def revoke_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    session: SessionDep,
) -> None:
    ok = await revoke_api_key(session, key_id, tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")


# ---- Policies ----


@router.get("/tenants/{tenant_id}/policy", response_model=PolicyResponse | None)
async def get_tenant_policy(tenant_id: uuid.UUID, session: SessionDep) -> PolicyResponse | None:
    policy = await get_policy(session, tenant_id)
    if not policy:
        return None
    return PolicyResponse.model_validate(policy)


@router.put("/tenants/{tenant_id}/policy", response_model=PolicyResponse)
async def set_tenant_policy(
    tenant_id: uuid.UUID,
    body: PolicyRequest,
    session: SessionDep,
) -> PolicyResponse:
    tenant = await get_tenant(session, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    policy = await upsert_policy(
        session,
        tenant_id,
        max_tokens_per_request=body.max_tokens_per_request,
        max_requests_per_minute=body.max_requests_per_minute,
        monthly_cost_ceiling_usd=body.monthly_cost_ceiling_usd,
        allowed_models=body.allowed_models,
        denied_topics=body.denied_topics,
        redact_pii=body.redact_pii,
    )
    return PolicyResponse.model_validate(policy)


# ---- Audit Log ----


@router.get("/audit", response_model=AuditPageResponse)
async def get_audit_log(
    session: SessionDep,
    tenant_id: uuid.UUID | None = None,
    provider: str | None = None,
    status_code: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    limit: int = 50,
    cursor: uuid.UUID | None = None,
) -> AuditPageResponse:
    page = await query_audit_logs(
        session,
        tenant_id=tenant_id,
        provider=provider,
        status_code=status_code,
        after=after,
        before=before,
        limit=min(limit, 200),
        cursor=cursor,
    )
    return AuditPageResponse(
        rows=[AuditLogItem.model_validate(r) for r in page.rows],
        total=page.total,
        has_more=page.has_more,
    )


@router.get("/audit/cost-summary", response_model=list[CostSummaryItem])
async def get_cost_summary(
    session: SessionDep,
    tenant_id: uuid.UUID | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
) -> list[CostSummaryItem]:
    summaries = await cost_summary(session, tenant_id=tenant_id, after=after, before=before)
    return [
        CostSummaryItem(
            tenant_id=s.tenant_id,
            total_requests=s.total_requests,
            total_cost_usd=s.total_cost_usd,
            total_input_tokens=s.total_input_tokens,
            total_output_tokens=s.total_output_tokens,
            avg_latency_ms=s.avg_latency_ms,
        )
        for s in summaries
    ]

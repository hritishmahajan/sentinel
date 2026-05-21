"""Tenant repository.

Thin query layer over the ORM. Routes call these functions instead of
writing raw SQLAlchemy expressions — keeps business logic out of the
HTTP layer and makes queries testable in isolation.

All functions accept an ``AsyncSession`` and return ORM objects or
``None``. Transaction management is the caller's responsibility
(handled by ``get_session`` in normal request flows).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.db.models import ApiKey, Policy, Tenant
from sentinel.security.api_keys import GeneratedKey

# ---- Tenant ----


async def create_tenant(session: AsyncSession, name: str) -> Tenant:
    tenant = Tenant(id=uuid.uuid4(), name=name, is_active=True)
    session.add(tenant)
    await session.flush()  # populate id before returning
    return tenant


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def get_tenant_by_name(session: AsyncSession, name: str) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.name == name))
    return result.scalar_one_or_none()


async def list_tenants(session: AsyncSession) -> list[Tenant]:
    result = await session.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return list(result.scalars().all())


async def deactivate_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    result = await session.execute(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(is_active=False)
        .returning(Tenant.id)
    )
    return result.scalar_one_or_none() is not None


# ---- API Keys ----


async def create_api_key(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    label: str,
    generated: GeneratedKey,
) -> ApiKey:
    key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        key_prefix=generated.prefix,
        key_hash=generated.hash,
        label=label,
        is_active=True,
    )
    session.add(key)
    await session.flush()
    return key


async def list_api_keys(session: AsyncSession, tenant_id: uuid.UUID) -> list[ApiKey]:
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.tenant_id == tenant_id)
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_api_key(
    session: AsyncSession, key_id: uuid.UUID, tenant_id: uuid.UUID
) -> bool:
    """Revoke a key. Requires tenant_id to prevent cross-tenant revocation."""
    result = await session.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
        .values(is_active=False)
        .returning(ApiKey.id)
    )
    return result.scalar_one_or_none() is not None


async def touch_api_key(session: AsyncSession, key_id: uuid.UUID) -> None:
    """Update last_used_at. Fire-and-forget — caller doesn't await the commit."""
    await session.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id)
        .values(last_used_at=datetime.now(UTC))
    )


# ---- Policy ----


async def get_policy(session: AsyncSession, tenant_id: uuid.UUID) -> Policy | None:
    result = await session.execute(
        select(Policy).where(Policy.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def upsert_policy(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    max_tokens_per_request: int = 4096,
    max_requests_per_minute: int = 60,
    monthly_cost_ceiling_usd: float = 100.0,
    allowed_models: list[str] | None = None,
    denied_topics: list[str] | None = None,
    redact_pii: bool = True,
) -> Policy:
    existing = await get_policy(session, tenant_id)
    if existing:
        existing.max_tokens_per_request = max_tokens_per_request
        existing.max_requests_per_minute = max_requests_per_minute
        existing.monthly_cost_ceiling_usd = monthly_cost_ceiling_usd
        existing.allowed_models = allowed_models or []
        existing.denied_topics = denied_topics or []
        existing.redact_pii = redact_pii
        await session.flush()
        await session.refresh(existing)
        return existing

    policy = Policy(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        max_tokens_per_request=max_tokens_per_request,
        max_requests_per_minute=max_requests_per_minute,
        monthly_cost_ceiling_usd=monthly_cost_ceiling_usd,
        allowed_models=allowed_models or [],
        denied_topics=denied_topics or [],
        redact_pii=redact_pii,
    )
    session.add(policy)
    await session.flush()
    await session.refresh(policy)
    return policy

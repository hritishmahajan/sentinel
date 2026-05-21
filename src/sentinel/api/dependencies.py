"""FastAPI dependency providers.

Centralizes construction of shared objects (router, redis client, etc.)
so that routes stay declarative. Anything stateful gets built once at
app startup and stored on ``app.state``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.core.config import Settings, get_settings
from sentinel.core.errors import AuthenticationError
from sentinel.db.models import ApiKey, Policy, Tenant
from sentinel.db.session import get_session
from sentinel.providers.router import ProviderRouter
from sentinel.security.api_keys import extract_prefix, verify_api_key

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_router(request: Request) -> ProviderRouter:
    """Return the singleton ProviderRouter stored on app.state."""
    router: ProviderRouter = request.app.state.router
    return router


RouterDep = Annotated[ProviderRouter, Depends(get_router)]


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


RedisDep = Annotated[Redis, Depends(get_redis)]


async def get_authenticated_tenant(
    settings: SettingsDep,
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> tuple[Tenant, Policy | None] | None:
    """Resolve the requesting tenant from the API key, if present.

    Accepts either ``Authorization: Bearer sk_live_...`` or
    ``X-API-Key: sk_live_...`` for SDK convenience.

    In ``dev`` (``require_auth=False``), an unauthenticated request is
    allowed and returns ``None``. In prod, missing or bad keys raise.
    """
    key: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        key = authorization.split(" ", 1)[1].strip()
    elif x_api_key:
        key = x_api_key.strip()

    if key is None:
        if settings.require_auth:
            raise AuthenticationError("Missing API key")
        return None

    prefix = extract_prefix(key)
    stmt = (
        select(ApiKey)
        .where(ApiKey.key_prefix == prefix, ApiKey.is_active.is_(True))
    )
    result = await session.execute(stmt)
    candidates = result.scalars().all()

    matched: ApiKey | None = next(
        (c for c in candidates if verify_api_key(key, c.key_hash)),
        None,
    )
    if matched is None:
        raise AuthenticationError("Invalid API key")

    tenant_stmt = select(Tenant).where(Tenant.id == matched.tenant_id)
    tenant = (await session.execute(tenant_stmt)).scalar_one()

    policy_stmt = select(Policy).where(Policy.tenant_id == tenant.id)
    policy = (await session.execute(policy_stmt)).scalar_one_or_none()

    return tenant, policy


AuthDep = Annotated[
    tuple[Tenant, Policy | None] | None,
    Depends(get_authenticated_tenant),
]

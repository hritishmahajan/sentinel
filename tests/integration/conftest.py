"""Integration test fixtures.

Uses an in-memory SQLite DB (via aiosqlite) so integration tests run
without a real Postgres instance in CI — Docker-free, fast.

The mock provider replaces real HTTP calls to Anthropic/OpenAI so tests
don't cost money and don't require API keys.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sentinel.db.models import Base
from sentinel.db.session import get_session
from sentinel.providers.base import CompletionResponse, LLMProvider, Usage
from sentinel.providers.circuit_breaker import CircuitBreaker
from sentinel.providers.router import ProviderRouter

# ---- In-memory DB ----

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


def _patch_jsonb_for_sqlite(target, connection, **kw):
    """SQLite doesn't understand JSONB — remap to JSON at DDL time."""
    from sqlalchemy.dialects.postgresql import JSONB
    for table in target.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    event.listen(Base.metadata, "before_create", _patch_jsonb_for_sqlite)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    event.remove(Base.metadata, "before_create", _patch_jsonb_for_sqlite)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


# ---- Mock provider ----

MOCK_RESPONSE = CompletionResponse(
    id="msg_test_001",
    model="claude-sonnet-4-5",
    provider="anthropic",
    content="Paris is the capital of France.",
    stop_reason="end_turn",
    usage=Usage(input_tokens=15, output_tokens=8),
    cost_usd=0.000069,
)


class MockProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, response: CompletionResponse = MOCK_RESPONSE) -> None:
        self._response = response
        self.calls: list[Any] = []

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls.append(request)
        return self._response

    async def health(self) -> bool:
        return True


# ---- Redis mock ----


class MockRedis:
    """Minimal Redis stand-in for integration tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: Any) -> None:
        self._store[key] = str(value)

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, _ttl: int) -> None:
        pass

    def pipeline(self) -> MockPipeline:
        return MockPipeline(self)

    def register_script(self, _script: str) -> MockScript:
        return MockScript()

    async def aclose(self) -> None:
        pass


class MockPipeline:
    def __init__(self, parent: MockRedis) -> None:
        self._parent = parent
        self._ops: list[tuple[str, str]] = []

    def delete(self, key: str) -> MockPipeline:
        self._ops.append(("delete", key))
        return self

    async def execute(self) -> None:
        for op, key in self._ops:
            if op == "delete":
                self._parent._store.pop(key, None)


class MockScript:
    async def __call__(self, keys: list[str], args: list[Any]) -> list[int]:
        # Always allow: (1=allowed, 999=remaining, 0=retry_after)
        return [1, 999, 0]


# ---- Test app ----


@pytest_asyncio.fixture
async def test_app(db_engine, db_session):
    """Build a fully wired test app with mock providers and in-memory DB."""
    from sentinel.api.main import create_app

    app = create_app()

    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _override_get_session

    mock_redis = MockRedis()
    mock_provider = MockProvider()
    mock_breaker = CircuitBreaker(mock_redis, "anthropic")  # type: ignore[arg-type]

    app.state.redis = mock_redis
    app.state.breakers = {"anthropic": mock_breaker}
    app.state.router = ProviderRouter(
        providers={"anthropic": mock_provider},
        breakers={"anthropic": mock_breaker},
    )

    return app


@pytest_asyncio.fixture
async def client(test_app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as c:
        yield c

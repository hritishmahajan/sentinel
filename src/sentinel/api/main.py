"""Sentinel FastAPI application factory.

This is the entry point. Lifespan manages async resources (Redis pool,
provider clients, circuit breakers) so they live for the lifetime of
the process rather than per-request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from redis.asyncio import Redis

from sentinel.api.middleware import RequestContextMiddleware
from sentinel.api.routes_admin import router as admin_router
from sentinel.api.routes_messages import router as messages_router
from sentinel.api.routes_ops import router as ops_router
from sentinel.api.schemas import ErrorResponse
from sentinel.core.config import get_settings
from sentinel.core.errors import SentinelError
from sentinel.core.logging import configure_logging, get_logger
from sentinel.providers.anthropic_provider import AnthropicProvider
from sentinel.providers.circuit_breaker import CircuitBreaker
from sentinel.providers.grok_provider import GrokProvider
from sentinel.providers.mock_provider import MockProvider
from sentinel.providers.openai_provider import OpenAIProvider
from sentinel.providers.router import ProviderRouter

log = get_logger(__name__)


class _NoOpRedis:
    """Stand-in for Redis when no Redis URL is configured (dev/SQLite mode)."""
    async def get(self, key): return None
    async def set(self, key, value): pass
    async def incr(self, key): return 0
    async def expire(self, key, ttl): pass
    def pipeline(self): return self
    def delete(self, key): return self
    async def execute(self): pass
    def register_script(self, _): return lambda keys, args: [1, 999, 0]
    async def aclose(self): pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build long-lived resources on startup; tear them down on shutdown."""
    configure_logging()
    settings = get_settings()

    log.info("startup.begin", environment=settings.environment)

    # ---- Redis pool (optional — skipped if REDIS_URL not set) ----
    redis_url = settings.redis_url or ''
    if redis_url and redis_url.startswith(('redis://', 'rediss://', 'unix://')):
        redis = Redis.from_url(redis_url, decode_responses=True)
    else:
        from sentinel.core.logging import get_logger as _gl
        _gl(__name__).warning('startup.redis_skipped', reason='REDIS_URL not set or invalid')
        redis = None

    # ---- Providers + circuit breakers ----
    providers = {}
    breakers = {}

    # Use a no-op Redis stand-in if Redis is not configured
    breaker_redis = redis if redis is not None else _NoOpRedis()

    if settings.anthropic_api_key:
        providers["anthropic"] = AnthropicProvider()
        breakers["anthropic"] = CircuitBreaker(breaker_redis, "anthropic")
        log.info("startup.provider_loaded", provider="anthropic")

    if settings.openai_api_key:
        providers["openai"] = OpenAIProvider()
        breakers["openai"] = CircuitBreaker(breaker_redis, "openai")
        log.info("startup.provider_loaded", provider="openai")

    if settings.xai_api_key:
        providers["grok"] = GrokProvider()
        breakers["grok"] = CircuitBreaker(breaker_redis, "grok")
        log.info("startup.provider_loaded", provider="grok")

    if not providers:
        log.warning("startup.no_providers_configured — loading mock provider")
        providers["mock"] = MockProvider()
        breakers["mock"] = CircuitBreaker(breaker_redis, "mock")
        log.info("startup.provider_loaded", provider="mock")

    app.state.redis = redis
    app.state.breakers = breakers
    app.state.router = ProviderRouter(
        providers=providers,
        breakers=breakers,
        max_retries=settings.max_retries,
    )

    log.info("startup.complete")
    try:
        yield
    finally:
        log.info("shutdown.begin")
        if redis is not None:
            await redis.aclose()
        log.info("shutdown.complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Sentinel",
        description="Governed LLM gateway for enterprises.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_prod else None,
        redoc_url=None,
    )

    # CORS — allows the dashboard to call the API from any origin.
    # Tighten to specific origins in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(messages_router, prefix="/v1", tags=["messages"])
    app.include_router(admin_router, tags=["admin"])
    app.include_router(ops_router, tags=["ops"])

    @app.exception_handler(SentinelError)
    async def _handle_sentinel_error(_request: Request, exc: SentinelError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse.build(
                exc.error_code, exc.message, **exc.details
            ).model_dump(),
        )

    # Serve the landing page at root and /console for the ops dashboard
    @app.get('/', include_in_schema=False)
    async def landing() -> FileResponse:
        p = Path(__file__).resolve().parents[4] / 'dashboard' / 'landing.html'
        if not p.exists():
            p = Path(__file__).resolve().parents[3] / 'dashboard' / 'landing.html'
        return FileResponse(str(p), media_type='text/html')

    @app.get('/console', include_in_schema=False)
    async def dashboard() -> FileResponse:
        p = Path(__file__).resolve().parents[4] / 'dashboard' / 'index.html'
        if not p.exists():
            p = Path(__file__).resolve().parents[3] / 'dashboard' / 'index.html'
        return FileResponse(str(p), media_type='text/html')

    return app


app = create_app()


def run() -> None:
    """Entry point for ``sentinel`` console script."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "sentinel.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=not settings.is_prod,
        log_config=None,  # We configure our own logging.
    )

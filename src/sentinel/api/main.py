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
from sentinel.providers.openai_provider import OpenAIProvider
from sentinel.providers.router import ProviderRouter

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build long-lived resources on startup; tear them down on shutdown."""
    configure_logging()
    settings = get_settings()

    log.info("startup.begin", environment=settings.environment)

    # ---- Redis pool ----
    redis = Redis.from_url(settings.redis_url, decode_responses=True)

    # ---- Providers + circuit breakers ----
    providers = {}
    breakers = {}

    if settings.anthropic_api_key:
        providers["anthropic"] = AnthropicProvider()
        breakers["anthropic"] = CircuitBreaker(redis, "anthropic")
        log.info("startup.provider_loaded", provider="anthropic")

    if settings.openai_api_key:
        providers["openai"] = OpenAIProvider()
        breakers["openai"] = CircuitBreaker(redis, "openai")
        log.info("startup.provider_loaded", provider="openai")

    if settings.xai_api_key:
        providers["grok"] = GrokProvider()
        breakers["grok"] = CircuitBreaker(redis, "grok")
        log.info("startup.provider_loaded", provider="grok")

    if not providers:
        log.warning("startup.no_providers_configured")

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

    # Serve the admin dashboard at /console
    @app.get('/console', include_in_schema=False)
    async def dashboard() -> FileResponse:
        dash_path = Path(__file__).parent.parent.parent.parent.parent / 'dashboard' / 'index.html'
        if dash_path.exists():
            return FileResponse(dash_path, media_type='text/html')
        fallback = Path(__file__).resolve().parent / 'dashboard_fallback.html'
        return FileResponse(fallback, media_type='text/html')

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

"""Health, metrics, and admin routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from sentinel.api.schemas import HealthResponse
from sentinel.providers.circuit_breaker import CircuitState

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness + readiness check.

    Reports per-provider circuit state so a load balancer or k8s probe
    can see when the gateway is degraded but still serving (one provider
    out, the other working).
    """
    providers_status: dict[str, str] = {}

    for name, breaker in request.app.state.breakers.items():
        state = await breaker.state()
        if state == CircuitState.CLOSED:
            providers_status[name] = "ok"
        elif state == CircuitState.HALF_OPEN:
            providers_status[name] = "degraded"
        else:
            providers_status[name] = "circuit_open"

    return HealthResponse(
        status="ok",
        version="0.1.0",
        providers=providers_status,
    )


@router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus scrape endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

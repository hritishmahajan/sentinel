"""Request middleware: request_id propagation and log context binding."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request_id to every request and bind it to the log context.

    Looks for an incoming ``X-Request-ID`` (for trace propagation from
    upstream load balancers); generates one if absent. The ID is bound
    via ``structlog.contextvars`` so every log line in the request
    lifetime carries it without explicit threading.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            structlog.contextvars.bind_contextvars(duration_ms=duration_ms)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response

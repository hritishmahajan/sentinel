"""Structured logging configuration.

Uses structlog so every log line carries the same contextual fields
(request_id, tenant_id, provider, model) without having to thread them
through call sites. JSON output in prod (machine-parseable), pretty
console output in dev.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from sentinel.core.config import get_settings


def configure_logging() -> None:
    """Configure stdlib logging + structlog. Idempotent."""
    settings = get_settings()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level.upper(),
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger. Use module __name__ by convention."""
    return structlog.get_logger(name)

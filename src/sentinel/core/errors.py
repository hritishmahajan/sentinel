"""Typed exception hierarchy.

Every exception that escapes a route handler is mapped to a JSON error
response by the global exception handler. Using explicit exception types
(rather than HTTPException with magic numbers) keeps error handling
greppable and testable.
"""

from __future__ import annotations


class SentinelError(Exception):
    """Base class for all application errors."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# ---- 4xx client errors ----


class AuthenticationError(SentinelError):
    status_code = 401
    error_code = "authentication_error"


class AuthorizationError(SentinelError):
    status_code = 403
    error_code = "authorization_error"


class PolicyViolationError(SentinelError):
    """Request violated a tenant policy (e.g. denied model, blocked content)."""

    status_code = 403
    error_code = "policy_violation"


class RateLimitError(SentinelError):
    status_code = 429
    error_code = "rate_limit_exceeded"


class ValidationError(SentinelError):
    status_code = 400
    error_code = "invalid_request"


# ---- 5xx server errors ----


class ProviderError(SentinelError):
    """Upstream LLM provider returned an error or was unreachable."""

    status_code = 502
    error_code = "provider_error"


class ProviderTimeoutError(ProviderError):
    status_code = 504
    error_code = "provider_timeout"


class CircuitOpenError(ProviderError):
    """All configured providers have open circuits — fail fast."""

    status_code = 503
    error_code = "service_unavailable"

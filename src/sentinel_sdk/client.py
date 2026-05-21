"""Sentinel SDK client.

Sync + async clients sharing a single HTTP layer. Typed dataclasses
for request/response shapes, typed exceptions for error mapping.

We deliberately keep dependencies minimal — only httpx — so the SDK is
trivial to vendor into other services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_S = 60.0


# ---- Errors --------------------------------------------------------------


class SentinelAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **details: Any) -> None:
        super().__init__(f"[{code}] {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class SentinelAuthError(SentinelAPIError):
    pass


class SentinelRateLimitError(SentinelAPIError):
    pass


# ---- Models --------------------------------------------------------------


@dataclass
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class MessagesResponse:
    id: str
    model: str
    provider: str
    content: str
    stop_reason: str | None
    usage: Usage
    cost_usd: float
    request_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessagesResponse:
        usage = data["usage"]
        return cls(
            id=data["id"],
            model=data["model"],
            provider=data["provider"],
            content=data["content"],
            stop_reason=data.get("stop_reason"),
            usage=Usage(usage["input_tokens"], usage["output_tokens"]),
            cost_usd=data["cost_usd"],
            request_id=data["request_id"],
        )


# ---- Client --------------------------------------------------------------


def _raise_from_response(resp: httpx.Response) -> None:
    """Map gateway error payloads to typed exceptions."""
    try:
        payload = resp.json().get("error", {})
    except ValueError:
        payload = {}

    code = payload.get("code", "unknown_error")
    message = payload.get("message", resp.text or "Unknown error")

    if resp.status_code == 401:
        raise SentinelAuthError(resp.status_code, code, message)
    if resp.status_code == 429:
        raise SentinelRateLimitError(resp.status_code, code, message)
    raise SentinelAPIError(resp.status_code, code, message)


@dataclass
class _MessagesResource:
    """Namespacing object so we can write ``client.messages.create(...)``."""

    client: Sentinel

    def create(
        self,
        *,
        model: str,
        messages: list[Message] | list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 1.0,
        system: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> MessagesResponse:
        return self.client._request(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            metadata=metadata,
        )


@dataclass
class Sentinel:
    """Synchronous client.

    Use ``api_key`` for authenticated requests. ``base_url`` defaults to
    a local gateway; point it at your production deployment.
    """

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT_S
    _http: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        headers = {"User-Agent": "sentinel-sdk-python/0.1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._http = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=self.timeout,
            headers=headers,
        )

    @property
    def messages(self) -> _MessagesResource:
        return _MessagesResource(self)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Sentinel:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- Internal ----

    def _request(
        self,
        *,
        model: str,
        messages: list[Message] | list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        system: str | None,
        metadata: dict[str, str] | None,
    ) -> MessagesResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() if isinstance(m, Message) else m for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system is not None:
            payload["system"] = system
        if metadata is not None:
            payload["metadata"] = metadata

        resp = self._http.post("/v1/messages", json=payload)
        if resp.is_error:
            _raise_from_response(resp)
        return MessagesResponse.from_dict(resp.json())

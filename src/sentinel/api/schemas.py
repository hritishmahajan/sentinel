"""API schemas (request/response shapes).

These are the *public contract* of the gateway. Backwards compatibility
matters once we ship the SDK, so changes here go through versioning.

We deliberately keep the wire shape close to the Anthropic Messages API
so the SDK can be a drop-in replacement.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sentinel.providers.base import Message


class MessagesRequest(BaseModel):
    model: str
    messages: list[Message] = Field(min_length=1)
    max_tokens: int = Field(default=1024, ge=1, le=16384)
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    system: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class MessagesResponse(BaseModel):
    id: str
    model: str
    provider: str
    content: str
    stop_reason: str | None
    usage: Usage
    cost_usd: float
    request_id: str


class ErrorResponse(BaseModel):
    error: dict[str, object]

    @classmethod
    def build(cls, code: str, message: str, **details: object) -> ErrorResponse:
        return cls(error={"code": code, "message": message, **details})


class HealthResponse(BaseModel):
    status: str
    version: str
    providers: dict[str, str]  # provider name -> "ok" | "circuit_open" | "unconfigured"

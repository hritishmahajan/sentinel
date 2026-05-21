"""Provider abstraction.

Every LLM backend (Anthropic, OpenAI, future ones) implements the same
``LLMProvider`` protocol. This is what lets the gateway swap providers,
route by policy, and fail over without leaking provider-specific shapes
into the rest of the system.

The shared request/response shape is intentionally close to the
Anthropic Messages API. We translate per-provider in each adapter.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "system"]


class Message(BaseModel):
    role: Role
    content: str


class CompletionRequest(BaseModel):
    """Normalized inbound request shape."""

    model: str
    messages: list[Message] = Field(min_length=1)
    max_tokens: int = Field(default=1024, ge=1, le=16384)
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    system: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class CompletionResponse(BaseModel):
    """Normalized outbound response shape."""

    id: str
    model: str
    provider: str
    content: str
    stop_reason: str | None = None
    usage: Usage
    cost_usd: float


class LLMProvider(Protocol):
    """The contract every provider adapter implements.

    Implementations must:
    - Be safe to call concurrently.
    - Raise ``ProviderError`` or one of its subclasses for any failure.
    - Compute and return cost_usd based on token usage.
    """

    name: str

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    async def health(self) -> bool: ...

"""Application configuration loaded from environment variables.

All settings are validated at startup via Pydantic. Fail-fast on missing or
malformed config — the gateway must never run with ambiguous settings, since
it sits in the request path for every downstream LLM call.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "staging", "prod"]


class Settings(BaseSettings):
    """Top-level application configuration.

    Reads from environment variables and an optional .env file. Names are
    UPPER_SNAKE_CASE in the environment. Secrets use Pydantic's SecretStr so
    they never accidentally leak into logs or repr output.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_name: str = "sentinel"
    environment: Environment = "dev"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ---- HTTP server ----
    host: str = "0.0.0.0"
    port: int = 8000

    # ---- Database ----
    database_url: str = Field(
        default="postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel",
        description="Async SQLAlchemy DSN. Use postgresql+asyncpg:// for prod.",
    )

    # ---- Redis (rate limiting, circuit breaker state, cache) ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- Provider API keys ----
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None

    # ---- Defaults ----
    default_provider: Literal["anthropic", "openai", "grok"] = "anthropic"
    request_timeout_seconds: float = 60.0
    max_retries: int = 2

    # ---- Security ----
    # Bcrypt cost factor for API key hashing. 12 is the production default.
    bcrypt_rounds: int = 12
    # If True, requests with no API key are rejected. Disabled in dev for ease.
    require_auth: bool = False

    # ---- Observability ----
    prometheus_enabled: bool = True
    otel_enabled: bool = False
    otel_endpoint: str | None = None

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this everywhere instead of instantiating directly."""
    return Settings()

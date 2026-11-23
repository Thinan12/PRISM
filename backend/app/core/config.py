from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRISM_", env_file=".env", extra="ignore")

    env: str = "development"  # development | production | test
    database_url: str = "sqlite:///./var/prism.db"
    data_dir: Path = Path("./var/data")
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    token_ttl_minutes: int = 60 * 12
    max_upload_mb: int = 200
    cors_origins: list[str] = ["http://localhost:5173"]
    # jobs: "inline" runs jobs inside the request (tests), "embedded" runs a worker thread
    # inside the API process (dev), "external" relies on `python -m app.worker` (prod)
    jobs_mode: str = "embedded"
    worker_poll_seconds: float = 1.0
    timezone: str = "Asia/Vientiane"
    admin_email: str | None = None
    admin_password: str | None = None
    # AI
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"  # low | medium | high | xhigh | max
    ai_max_retries: int = 2  # extra attempts when the model's JSON fails validation
    ai_timeout_seconds: float = 180.0
    # send bounded, masked example values (samples, frequent values) to the AI; when false
    # only schema metadata and statistics leave the server
    ai_send_values: bool = True
    # rate limiting (requests per minute per client for auth endpoints)
    auth_rate_per_minute: int = 20

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("PRISM_SECRET_KEY"):
        if s.env == "production":
            raise RuntimeError("PRISM_SECRET_KEY must be set (>= 32 random characters) in production.")
        # development: persist a generated key so tokens survive reloads
        key_file = s.data_dir / ".dev_secret"
        if key_file.exists():
            s.secret_key = key_file.read_text().strip()
        else:
            key_file.write_text(s.secret_key)
    elif len(s.secret_key) < 32:
        raise RuntimeError("PRISM_SECRET_KEY must be at least 32 characters.")
    return s

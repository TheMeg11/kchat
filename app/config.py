"""Application settings (environment driven, Railway compatible)."""
from __future__ import annotations

import os
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

SCHEMA_VERSION = "1.0.0"


def _parse_ids(raw: str) -> List[int]:
    out: List[int] = []
    for part in (raw or "").replace(",", " ").split():
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Database (SQLite for local dev/tests, Postgres on Railway)
    database_url: str = "sqlite+aiosqlite:///./kanarchat.db"

    # Bot tokens
    telegram_bot_token: str = ""
    bale_bot_token: str = ""
    delivery_bot_token: str = ""
    bale_provider_token: str = ""

    # Secrets
    encryption_key: str = ""
    backup_password: str = ""
    webhook_secret: str = ""

    # Networking
    public_url: str = ""
    port: int = int(os.getenv("PORT", "8000"))

    # Bot-side owner identifiers (independent per platform)
    admin_telegram_ids: str = ""
    admin_bale_ids: str = ""

    # Backup delivery target (product bot -> this Telegram user)
    backup_owner_telegram_id: int = 860835914
    backup_hour_tehran: int = 3

    # Web-panel admin bootstrap (set as Railway variables — v2.3.1):
    # every startup reconciles the owner account to these credentials.
    admin_panel_username: str = ""
    admin_panel_password: str = ""

    # Media relay caps (verified official limits: getFile 20MB on both platforms)
    cross_media_max_bytes: int = 20 * 1024 * 1024
    # Telegram multipart upload cap is 50MB per file; stay safely below it
    telegram_chunk_bytes: int = 40 * 1024 * 1024

    @property
    def owner_telegram_ids(self) -> List[int]:
        return _parse_ids(self.admin_telegram_ids)

    @property
    def owner_bale_ids(self) -> List[int]:
        return _parse_ids(self.admin_bale_ids)

    @property
    def is_pg(self) -> bool:
        return self.database_url.startswith("postgres")


settings = Settings()

# Ephemeral fallbacks so the process never boots without a secret; real
# deployments must provide ENCRYPTION_KEY / WEBHOOK_SECRET via environment.
import secrets as _secrets  # noqa: E402

if not settings.webhook_secret:
    settings.webhook_secret = _secrets.token_urlsafe(24)

# Runtime shared state (adapters, bot runners, restore lock) set by main.py.
RUNTIME: dict = {}

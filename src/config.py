from __future__ import annotations

import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MATRIX_NIO_DEVICE_ID = "MATRIX_MAX_BRIDGE"

# Формат MXID: localpart (любой непустой, без двоеточия) + ":" + имя сервера.
_MXID_RE = re.compile(r"^@[^:]+:.+$")
# Тексты команд link/unlink короткие; ограничиваем их на всякий случай.
_MAX_TOKEN_LEN = 4096


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    max_bot_token: str

    matrix_hs_url: str
    matrix_user_id: str
    matrix_access_token: str

    database_path: str = "/data/bridge.db"
    link_code_ttl_seconds: int = 3600

    matrix_allowed_server: str | None = None

    # Максимальный размер одного медиа-файла в любом направлении.
    max_download_bytes: int = 20 * 1024 * 1024

    # Если True, для привязки (link) требуются права администратора Matrix
    # (power-level >= 100), а не только знание кода.
    require_mod_to_link: bool = False

    @field_validator("matrix_allowed_server", mode="before")
    @classmethod
    def _strip_allowed_server(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return None

    @field_validator("max_bot_token", "matrix_access_token")
    @classmethod
    def _nonempty_secret(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("не должен быть пустым — задайте его в .env (см. .env.example)")
        return v.strip()

    @field_validator("matrix_hs_url")
    @classmethod
    def _https_hs_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.startswith("https://"):
            raise ValueError("MATRIX_HS_URL должен начинаться с https://")
        return v

    @field_validator("matrix_user_id")
    @classmethod
    def _valid_mxid(cls, v: str) -> str:
        v = (v or "").strip()
        if not _MXID_RE.match(v):
            raise ValueError("MATRIX_USER_ID должен быть полным MXID вида '@bot:example.org'")
        return v

    @field_validator("max_download_bytes")
    @classmethod
    def _positive_limit(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("MAX_DOWNLOAD_BYTES должен быть положительным")
        return v

    rate_limit_link_attempts: int = 20
    rate_limit_link_window_seconds: int = 300
    rate_limit_code_generations: int = 10
    rate_limit_code_window_seconds: int = 3600

    def matrix_homeserver_base(self) -> str:
        return self.matrix_hs_url.rstrip("/")

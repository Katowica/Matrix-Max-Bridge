from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings


def _base_env(**overrides):
    env = {
        "max_bot_token": "token-abc",
        "matrix_hs_url": "https://matrix.example.org",
        "matrix_user_id": "@max.bot:example.org",
        "matrix_access_token": "syt_secret",
    }
    env.update(overrides)
    return env


def test_valid_settings(monkeypatch):
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.max_download_bytes == 20 * 1024 * 1024
    assert s.matrix_homeserver_base() == "https://matrix.example.org"


def test_rejects_plain_http_hs_url(monkeypatch):
    for k, v in _base_env(matrix_hs_url="http://matrix.example.org").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()


def test_rejects_bad_mxid(monkeypatch):
    for k, v in _base_env(matrix_user_id="max.bot:example.org").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()


def test_rejects_empty_token(monkeypatch):
    for k, v in _base_env(max_bot_token="   ").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()


def test_allowed_server_strips_to_none(monkeypatch):
    for k, v in _base_env(matrix_allowed_server="   ").items():
        monkeypatch.setenv(k, v)
    assert Settings().matrix_allowed_server is None


def test_negative_size_limit_rejected(monkeypatch):
    for k, v in _base_env(max_download_bytes="0").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValidationError):
        Settings()

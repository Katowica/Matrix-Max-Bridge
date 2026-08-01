from __future__ import annotations

import pytest

from src.command_router import Intent, classify, is_command
from src.linking import (
    generate_link_code,
    is_expired,
    is_max_link_request_command,
    is_max_unlink_command,
    parse_max_link_command,
)


@pytest.mark.parametrize(
    "body,expected,payload",
    [
        ("/max link ABC123", Intent.LINK, "ABC123"),
        ("/Max LINK   xyz-9_A", Intent.LINK, "xyz-9_A"),
        ("/max unlink", Intent.UNLINK, None),
        ("/max link", Intent.LINK_REQUEST, None),
        ("  /max link  ", Intent.LINK_REQUEST, None),
        ("hello world", Intent.RELAY, None),
        ("", Intent.RELAY, None),
        ("/maxlink code", Intent.RELAY, None),
        ("/max linkcode", Intent.RELAY, None),
    ],
)
def test_classify(body, expected, payload):
    intent, code = classify(body)
    assert intent == expected
    assert code == payload


def test_is_command_helper():
    assert is_command("/max link ABC")
    assert is_command("/max unlink")
    assert not is_command("just chatting")
    assert not is_command("")


def test_parse_link_command_returns_code():
    assert parse_max_link_command("/max link ABC123") == "ABC123"
    assert parse_max_link_command("/max link") is None
    assert parse_max_link_command("hello") is None
    assert parse_max_link_command("") is None


def test_unlink_command():
    assert is_max_unlink_command("/max unlink")
    assert is_max_unlink_command("/MAX UNLINK")
    assert not is_max_unlink_command("/max unlink extra")
    assert not is_max_unlink_command("")


def test_link_request_command():
    assert is_max_link_request_command("/max link")
    assert not is_max_link_request_command("/max link CODE")


def test_generate_link_code_format():
    code = generate_link_code()
    assert len(code) == 10
    for ch in code:
        assert ch not in "O0I1"


def test_is_expired():
    import time

    future = time.time() + 100
    past = time.time() - 100
    assert is_expired(past)
    assert not is_expired(future)

from __future__ import annotations

import enum

from src.linking import (
    is_max_link_request_command,
    is_max_unlink_command,
    parse_max_link_command,
)


class Intent(enum.Enum):
    RELAY = "relay"
    LINK = "link"
    UNLINK = "unlink"
    LINK_REQUEST = "link_request"


def classify(body: str) -> tuple[Intent, str | None]:
    if not body:
        return Intent.RELAY, None
    if is_max_unlink_command(body):
        return Intent.UNLINK, None
    code = parse_max_link_command(body)
    if code:
        return Intent.LINK, code
    if is_max_link_request_command(body):
        return Intent.LINK_REQUEST, None
    return Intent.RELAY, None


def is_command(body: str) -> bool:
    intent, _ = classify(body)
    return intent != Intent.RELAY

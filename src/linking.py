from __future__ import annotations

import re
import secrets
import string
import time

_LINK_RE = re.compile(r"^/max\s+link\s+(\S+)\s*$", re.IGNORECASE | re.UNICODE)
_UNLINK_RE = re.compile(r"^/max\s+unlink\s*$", re.IGNORECASE | re.UNICODE)
_LINK_REQUEST_RE = re.compile(r"^/max\s+link\s*$", re.IGNORECASE | re.UNICODE)

_ALPHABET = (
    (string.ascii_uppercase + string.digits)
    .replace("O", "")
    .replace("0", "")
    .replace("I", "")
    .replace("1", "")
)


def generate_link_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def parse_max_link_command(body: str) -> str | None:
    if not body:
        return None
    m = _LINK_RE.match(body.strip())
    return m.group(1) if m else None


def is_max_unlink_command(body: str) -> bool:
    if not body:
        return False
    return bool(_UNLINK_RE.match(body.strip()))


def is_max_link_request_command(body: str) -> bool:
    if not body:
        return False
    return bool(_LINK_REQUEST_RE.match(body.strip()))


def is_expired(expires_at: float) -> bool:
    return time.time() > expires_at

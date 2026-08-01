from __future__ import annotations

import time

import pytest

from src.db import Database


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "bridge.db")
    db = await Database.connect(path)
    yield db
    await db.close()


async def test_insert_and_get_pending(db):
    expires = time.time() + 100
    await db.insert_pending("CODE1", 100, expires)
    pending = await db.get_pending_by_code("CODE1")
    assert pending is not None
    assert pending.max_chat_id == 100
    assert pending.expires_at == pytest.approx(expires)


async def test_try_link_atomic_consumes_code_and_creates_bridge(db):
    expires = time.time() + 100
    await db.insert_pending("CODE2", 200, expires)
    linked = await db.try_link_atomic("CODE2", 200, "!room1:example.org")
    assert linked is True
    assert await db.get_pending_by_code("CODE2") is None
    bridge = await db.get_bridge_by_max(200)
    assert bridge is not None
    assert bridge.matrix_room_id == "!room1:example.org"


async def test_try_link_atomic_conflict_on_max_chat(db):
    expires = time.time() + 100
    await db.insert_pending("CODE3", 300, expires)
    await db.insert_pending("CODE4", 300, expires)
    assert await db.try_link_atomic("CODE3", 300, "!r1:example.org")
    assert await db.try_link_atomic("CODE4", 300, "!r2:example.org") is False


async def test_try_link_atomic_conflict_on_matrix_room(db):
    expires = time.time() + 100
    await db.insert_pending("CODE5", 500, expires)
    await db.insert_pending("CODE6", 600, expires)
    assert await db.try_link_atomic("CODE5", 500, "!shared:example.org")
    assert await db.try_link_atomic("CODE6", 600, "!shared:example.org") is False


async def test_try_link_atomic_unknown_code(db):
    assert await db.try_link_atomic("NOPE", 700, "!r:example.org") is False


async def test_welcome_meta(db):
    assert await db.is_welcome_sent("!room:example.org") is False
    await db.mark_welcome_sent("!room:example.org")
    assert await db.is_welcome_sent("!room:example.org") is True
    await db.mark_welcome_sent("!room:example.org")


async def test_cleanup_expired_pending(db):
    past = time.time() - 100
    future = time.time() + 100
    await db.insert_pending("EXPIRED", 1, past)
    await db.insert_pending("VALID", 2, future)
    await db.cleanup_expired_pending()
    assert await db.get_pending_by_code("EXPIRED") is None
    assert await db.get_pending_by_code("VALID") is not None


async def test_delete_bridge_returns_flag(db):
    expires = time.time() + 100
    await db.insert_pending("C", 900, expires)
    await db.try_link_atomic("C", 900, "!r:example.org")
    assert await db.delete_bridge_by_max(999) is False
    assert await db.delete_bridge_by_max(900) is True


async def test_pragma_foreign_keys_is_on(db):
    cur = await db.conn.execute("PRAGMA foreign_keys")
    row = await cur.fetchone()
    assert row[0] == 1


async def test_pragma_secure_delete_is_on(db):
    cur = await db.conn.execute("PRAGMA secure_delete")
    row = await cur.fetchone()
    assert row[0] == 1

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_INSERT_BRIDGE_SQL = (
    "INSERT INTO bridges (max_chat_id, matrix_room_id, created_at) VALUES (?, ?, ?)"
)


@dataclass
class PendingLink:
    code: str
    max_chat_id: int
    created_at: float
    expires_at: float


@dataclass
class Bridge:
    max_chat_id: int
    matrix_room_id: str
    created_at: float


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("База данных использована до connect() или после close()")
        return self._db

    @classmethod
    async def connect(cls, path: str) -> Database:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Не удалось создать каталог SQLite: {resolved.parent!s} ({e}). "
                "Проверьте права на каталог или DATABASE_PATH."
            ) from e
        self = cls(str(resolved))
        self._db = await aiosqlite.connect(str(resolved))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.execute("PRAGMA secure_delete=ON;")
        await self._db.commit()
        await self.init_schema()
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def init_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_links (
                code TEXT PRIMARY KEY,
                max_chat_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pending_links_max
                ON pending_links(max_chat_id);

            CREATE TABLE IF NOT EXISTS bridges (
                max_chat_id INTEGER NOT NULL UNIQUE,
                matrix_room_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bridges_max ON bridges(max_chat_id);
            CREATE INDEX IF NOT EXISTS idx_bridges_matrix ON bridges(matrix_room_id);

            CREATE TABLE IF NOT EXISTS matrix_room_meta (
                room_id TEXT PRIMARY KEY,
                welcome_sent INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS relayed_events (
                event_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_relayed_events_created
                ON relayed_events(created_at);
            """
        )
        await self.conn.commit()
        logger.info("Схема SQLite готова по пути %s", self.path)

    async def cleanup_expired_pending(self) -> None:
        now = time.time()
        await self.conn.execute("DELETE FROM pending_links WHERE expires_at < ?", (now,))
        await self.conn.commit()

    async def revoke_pending_for_max(self, max_chat_id: int) -> None:
        await self.conn.execute("DELETE FROM pending_links WHERE max_chat_id = ?", (max_chat_id,))
        await self.conn.commit()

    async def insert_pending(
        self,
        code: str,
        max_chat_id: int,
        expires_at: float,
    ) -> None:
        now = time.time()
        await self.conn.execute(
            "INSERT INTO pending_links (code, max_chat_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)",
            (code, max_chat_id, now, expires_at),
        )
        await self.conn.commit()

    async def get_pending_by_code(self, code: str) -> PendingLink | None:
        cur = await self.conn.execute(
            "SELECT code, max_chat_id, created_at, expires_at FROM pending_links WHERE code = ?",
            (code,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return PendingLink(
            code=row["code"],
            max_chat_id=int(row["max_chat_id"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    async def delete_pending(self, code: str) -> None:
        await self.conn.execute("DELETE FROM pending_links WHERE code = ?", (code,))
        await self.conn.commit()

    async def try_link_atomic(
        self,
        code: str,
        max_chat_id: int,
        matrix_room_id: str,
    ) -> bool:
        now = time.time()
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self.conn.execute(
                "SELECT max_chat_id FROM pending_links WHERE code = ?", (code,)
            )
            if await cur.fetchone() is None:
                await self.conn.execute("ROLLBACK")
                return False
            cur2 = await self.conn.execute(
                "SELECT 1 FROM bridges WHERE max_chat_id = ? OR matrix_room_id = ?",
                (max_chat_id, matrix_room_id),
            )
            if await cur2.fetchone() is not None:
                await self.conn.execute("ROLLBACK")
                return False
            await self.conn.execute("DELETE FROM pending_links WHERE code = ?", (code,))
            await self.conn.execute(_INSERT_BRIDGE_SQL, (max_chat_id, matrix_room_id, now))
            await self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            await self.conn.execute("ROLLBACK")
            return False

    async def get_bridge_by_max(self, max_chat_id: int) -> Bridge | None:
        cur = await self.conn.execute(
            "SELECT max_chat_id, matrix_room_id, created_at FROM bridges WHERE max_chat_id = ?",
            (max_chat_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return Bridge(
            max_chat_id=int(row["max_chat_id"]),
            matrix_room_id=row["matrix_room_id"],
            created_at=float(row["created_at"]),
        )

    async def get_bridge_by_matrix(self, matrix_room_id: str) -> Bridge | None:
        cur = await self.conn.execute(
            "SELECT max_chat_id, matrix_room_id, created_at FROM bridges WHERE matrix_room_id = ?",
            (matrix_room_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return Bridge(
            max_chat_id=int(row["max_chat_id"]),
            matrix_room_id=row["matrix_room_id"],
            created_at=float(row["created_at"]),
        )

    async def delete_bridge_by_max(self, max_chat_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM bridges WHERE max_chat_id = ?", (max_chat_id,))
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def delete_bridge_by_matrix(self, matrix_room_id: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM bridges WHERE matrix_room_id = ?",
            (matrix_room_id,),
        )
        await self.conn.commit()
        return (cur.rowcount or 0) > 0

    async def is_welcome_sent(self, room_id: str) -> bool:
        cur = await self.conn.execute(
            "SELECT welcome_sent FROM matrix_room_meta WHERE room_id = ?", (room_id,)
        )
        row = await cur.fetchone()
        return bool(row and row[0])

    async def mark_welcome_sent(self, room_id: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO matrix_room_meta (room_id, welcome_sent) VALUES (?, 1)
            ON CONFLICT(room_id) DO UPDATE SET welcome_sent = 1
            """,
            (room_id,),
        )
        await self.conn.commit()

    async def was_relayed(self, event_id: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM relayed_events WHERE event_id = ?", (event_id,)
        )
        return (await cur.fetchone()) is not None

    async def mark_relayed(self, event_id: str) -> None:
        if not event_id:
            return
        await self.conn.execute(
            "INSERT OR IGNORE INTO relayed_events (event_id, created_at) VALUES (?, ?)",
            (event_id, time.time()),
        )
        await self.conn.commit()

    async def cleanup_relayed_events(self, ttl_seconds: int = 86400) -> None:
        cutoff = time.time() - float(ttl_seconds)
        await self.conn.execute("DELETE FROM relayed_events WHERE created_at < ?", (cutoff,))
        await self.conn.commit()

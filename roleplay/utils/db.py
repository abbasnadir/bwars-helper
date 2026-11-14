from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS roleplay_channels (
    channel_id    INTEGER PRIMARY KEY,
    guild_id      INTEGER NOT NULL,
    character_name TEXT NOT NULL,
    persona       TEXT NOT NULL,
    scenario      TEXT NOT NULL DEFAULT '',
    style         TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL,
    temperature   REAL NOT NULL DEFAULT 0.8,
    max_history   INTEGER NOT NULL DEFAULT 20,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_by    INTEGER,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS roleplay_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    author_id   INTEGER,
    author_name TEXT,
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_roleplay_messages_channel
    ON roleplay_messages (channel_id, id);

CREATE TABLE IF NOT EXISTS roleplay_memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    about       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_roleplay_memories_channel
    ON roleplay_memories (channel_id, kind, id);
"""

MEMORY_PINNED = "pinned"
MEMORY_AUTO = "auto"


class Database:
    """Small SQLite store used for roleplay configs and conversation memory.

    Every call opens its own short-lived connection and runs in a worker thread,
    which keeps the event loop free while avoiding a shared sqlite3 connection
    being used from multiple threads at once.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = Path(__file__).resolve().parent.parent / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Older versions stored a free-text summary that small models often
            # filled with transcript text instead of facts. It is replaced by
            # roleplay_memories.
            conn.execute("DROP TABLE IF EXISTS roleplay_summaries")
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def _read(self, func, *args) -> Any:
        return await asyncio.to_thread(func, *args)

    async def _write(self, func, *args) -> Any:
        return await asyncio.to_thread(func, *args)

    # ------------------------------------------------------------------ configs

    async def get_channel(self, channel_id: int) -> Optional[dict]:
        return await self._read(self._get_channel_sync, channel_id)

    def _get_channel_sync(self, channel_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM roleplay_channels WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        return dict(row) if row else None

    async def list_channels(self) -> list[dict]:
        return await self._read(self._list_channels_sync)

    def _list_channels_sync(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM roleplay_channels ORDER BY guild_id, channel_id"
            ).fetchall()
        return [dict(row) for row in rows]

    async def upsert_channel(
        self,
        *,
        channel_id: int,
        guild_id: int,
        character_name: str,
        persona: str,
        scenario: str,
        model: str,
        temperature: float,
        max_history: int,
        created_by: int,
    ) -> None:
        await self._write(
            self._upsert_channel_sync,
            channel_id,
            guild_id,
            character_name,
            persona,
            scenario,
            model,
            temperature,
            max_history,
            created_by,
        )

    def _upsert_channel_sync(
        self,
        channel_id: int,
        guild_id: int,
        character_name: str,
        persona: str,
        scenario: str,
        model: str,
        temperature: float,
        max_history: int,
        created_by: int,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO roleplay_channels (
                    channel_id, guild_id, character_name, persona, scenario, style,
                    model, temperature, max_history, enabled, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    character_name = excluded.character_name,
                    persona = excluded.persona,
                    scenario = excluded.scenario,
                    style = '',
                    model = excluded.model,
                    temperature = excluded.temperature,
                    max_history = excluded.max_history,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    channel_id,
                    guild_id,
                    character_name,
                    persona,
                    scenario,
                    model,
                    temperature,
                    max_history,
                    created_by,
                    now,
                    now,
                ),
            )

    async def set_enabled(self, channel_id: int, enabled: bool) -> bool:
        return await self._write(self._set_enabled_sync, channel_id, enabled)

    def _set_enabled_sync(self, channel_id: int, enabled: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE roleplay_channels SET enabled = ?, updated_at = ? WHERE channel_id = ?",
                (1 if enabled else 0, time.time(), channel_id),
            )
        return cur.rowcount > 0

    async def set_temperature(self, channel_id: int, temperature: float) -> bool:
        return await self._write(self._set_temperature_sync, channel_id, temperature)

    def _set_temperature_sync(self, channel_id: int, temperature: float) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE roleplay_channels SET temperature = ?, updated_at = ? WHERE channel_id = ?",
                (float(temperature), time.time(), channel_id),
            )
        return cur.rowcount > 0

    async def append_style(self, channel_id: int, instruction: str, limit: int = 4000) -> bool:
        return await self._write(self._append_style_sync, channel_id, instruction, limit)

    def _append_style_sync(self, channel_id: int, instruction: str, limit: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT style FROM roleplay_channels WHERE channel_id = ?", (channel_id,)
            ).fetchone()
            if not row:
                return False
            style = row["style"] or ""
            merged = f"{style}\n{instruction}".strip() if style else instruction.strip()
            if len(merged) > limit:
                merged = merged[-limit:].lstrip()
            conn.execute(
                "UPDATE roleplay_channels SET style = ?, updated_at = ? WHERE channel_id = ?",
                (merged, time.time(), channel_id),
            )
        return True

    async def clear_style(self, channel_id: int) -> bool:
        return await self._write(self._clear_style_sync, channel_id)

    def _clear_style_sync(self, channel_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE roleplay_channels SET style = '', updated_at = ? WHERE channel_id = ?",
                (time.time(), channel_id),
            )
        return cur.rowcount > 0

    async def delete_channel(self, channel_id: int) -> bool:
        return await self._write(self._delete_channel_sync, channel_id)

    def _delete_channel_sync(self, channel_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM roleplay_channels WHERE channel_id = ?", (channel_id,)
            )
            conn.execute(
                "DELETE FROM roleplay_messages WHERE channel_id = ?", (channel_id,)
            )
            conn.execute(
                "DELETE FROM roleplay_memories WHERE channel_id = ?", (channel_id,)
            )
        return cur.rowcount > 0

    # ----------------------------------------------------------------- messages

    async def add_message(
        self,
        channel_id: int,
        role: str,
        content: str,
        author_id: Optional[int] = None,
        author_name: Optional[str] = None,
    ) -> int:
        return await self._write(
            self._add_message_sync, channel_id, role, content, author_id, author_name
        )

    def _add_message_sync(
        self,
        channel_id: int,
        role: str,
        content: str,
        author_id: Optional[int],
        author_name: Optional[str],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO roleplay_messages
                    (channel_id, role, author_id, author_name, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (channel_id, role, author_id, author_name, content, time.time()),
            )
        return int(cur.lastrowid)

    async def get_recent_messages(self, channel_id: int, limit: int) -> list[dict]:
        return await self._read(self._get_recent_messages_sync, channel_id, limit)

    def _get_recent_messages_sync(self, channel_id: int, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM roleplay_messages
                    WHERE channel_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (channel_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    async def get_oldest_messages(self, channel_id: int, limit: int) -> list[dict]:
        return await self._read(self._get_oldest_messages_sync, channel_id, limit)

    def _get_oldest_messages_sync(self, channel_id: int, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM roleplay_messages
                WHERE channel_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (channel_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    async def count_messages(self, channel_id: int) -> int:
        return await self._read(self._count_messages_sync, channel_id)

    def _count_messages_sync(self, channel_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM roleplay_messages WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        return int(row["count"])

    async def delete_messages_through(self, channel_id: int, through_id: int) -> int:
        return await self._write(self._delete_messages_through_sync, channel_id, through_id)

    def _delete_messages_through_sync(self, channel_id: int, through_id: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM roleplay_messages WHERE channel_id = ? AND id <= ?",
                (channel_id, through_id),
            )
        return cur.rowcount

    # ----------------------------------------------------------------- memories

    async def list_memories(self, channel_id: int) -> list[dict]:
        return await self._read(self._list_memories_sync, channel_id)

    def _list_memories_sync(self, channel_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM roleplay_memories
                WHERE channel_id = ?
                ORDER BY CASE kind WHEN 'pinned' THEN 0 ELSE 1 END, id
                """,
                (channel_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def add_memory(self, channel_id: int, kind: str, content: str, about: str = "") -> int:
        return await self._write(self._add_memory_sync, channel_id, kind, content, about)

    def _add_memory_sync(self, channel_id: int, kind: str, content: str, about: str) -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO roleplay_memories (channel_id, kind, about, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (channel_id, kind, about, content, now, now),
            )
        return int(cur.lastrowid)

    async def delete_memory(self, channel_id: int, memory_id: int) -> Optional[dict]:
        return await self._write(self._delete_memory_sync, channel_id, memory_id)

    def _delete_memory_sync(self, channel_id: int, memory_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM roleplay_memories WHERE channel_id = ? AND id = ?",
                (channel_id, memory_id),
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM roleplay_memories WHERE id = ?", (memory_id,))
        return dict(row)

    async def replace_auto_memories(
        self,
        channel_id: int,
        facts: list[tuple[str, str]],
        through_id: Optional[int] = None,
    ) -> None:
        """Atomically swap the automatic memories and drop the messages they cover."""
        await self._write(self._replace_auto_memories_sync, channel_id, facts, through_id)

    def _replace_auto_memories_sync(
        self,
        channel_id: int,
        facts: list[tuple[str, str]],
        through_id: Optional[int],
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM roleplay_memories WHERE channel_id = ? AND kind = ?",
                (channel_id, MEMORY_AUTO),
            )
            conn.executemany(
                """
                INSERT INTO roleplay_memories (channel_id, kind, about, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(channel_id, MEMORY_AUTO, about, content, now, now) for about, content in facts],
            )
            if through_id is not None:
                conn.execute(
                    "DELETE FROM roleplay_messages WHERE channel_id = ? AND id <= ?",
                    (channel_id, through_id),
                )

    async def clear_context(self, channel_id: int) -> tuple[int, int]:
        """Delete stored messages and automatic memories. Pinned memories are kept."""
        return await self._write(self._clear_context_sync, channel_id)

    def _clear_context_sync(self, channel_id: int) -> tuple[int, int]:
        with self._connect() as conn:
            messages = conn.execute(
                "DELETE FROM roleplay_messages WHERE channel_id = ?", (channel_id,)
            ).rowcount
            memories = conn.execute(
                "DELETE FROM roleplay_memories WHERE channel_id = ? AND kind = ?",
                (channel_id, MEMORY_AUTO),
            ).rowcount
        return messages, memories

    async def context_stats(self, channel_id: int) -> dict:
        return await self._read(self._context_stats_sync, channel_id)

    def _context_stats_sync(self, channel_id: int) -> dict:
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM roleplay_messages WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()["count"]
            kinds = conn.execute(
                """
                SELECT kind, COUNT(*) AS count FROM roleplay_memories
                WHERE channel_id = ? GROUP BY kind
                """,
                (channel_id,),
            ).fetchall()
        by_kind = {row["kind"]: int(row["count"]) for row in kinds}
        return {
            "messages": int(count),
            "pinned_memories": by_kind.get(MEMORY_PINNED, 0),
            "auto_memories": by_kind.get(MEMORY_AUTO, 0),
        }

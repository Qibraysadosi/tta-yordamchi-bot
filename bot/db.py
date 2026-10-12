"""SQLite: foydalanuvchi sozlamalari, suhbat tarixi va xarajatlar jurnali."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    user_id          INTEGER PRIMARY KEY,
    model            TEXT    NOT NULL,
    effort           TEXT,
    web_search       INTEGER NOT NULL DEFAULT 1,
    show_cost        INTEGER NOT NULL DEFAULT 1,
    conv_id          INTEGER NOT NULL DEFAULT 1,
    history_start_id INTEGER NOT NULL DEFAULT 0,
    model_override   TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    conv_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (user_id, conv_id, id);
CREATE TABLE IF NOT EXISTS usage_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    model       TEXT    NOT NULL,
    input       INTEGER NOT NULL DEFAULT 0,
    output      INTEGER NOT NULL DEFAULT 0,
    thinking    INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    cache_read  INTEGER NOT NULL DEFAULT 0,
    searches    INTEGER NOT NULL DEFAULT 0,
    cost        REAL    NOT NULL,
    estimated   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_log (user_id, created_at);
CREATE TABLE IF NOT EXISTS members (
    user_id   INTEGER PRIMARY KEY,
    name      TEXT,
    username  TEXT,
    joined_at TEXT    NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS bot_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


@dataclass
class UserSettings:
    user_id: int
    model: str
    effort: str | None
    web_search: bool
    show_cost: bool
    conv_id: int
    history_start_id: int
    model_override: str | None

    @property
    def active_model(self) -> str:
        return self.model_override or self.model


@dataclass
class StoredMessage:
    id: int
    role: str
    content: Any


@dataclass
class Member:
    """Parol bilan kirgan foydalanuvchi. active=False — egasi chiqarib yuborgan."""

    user_id: int
    name: str | None
    username: str | None
    joined_at: str
    active: bool

    @property
    def display(self) -> str:
        parts = [self.name or "Nomsiz"]
        if self.username:
            parts.append(f"@{self.username}")
        return " ".join(parts)


class Database:
    _SETTINGS_FIELDS = frozenset(
        {"model", "effort", "web_search", "show_cost", "conv_id", "history_start_id", "model_override"}
    )

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def open(cls, path: Path) -> "Database":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self._conn.close()

    # --- sozlamalar ---
    async def get_settings(self, user_id: int, default_model: str, default_web_search: bool) -> UserSettings:
        async with self._conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            await self._conn.execute(
                "INSERT INTO settings (user_id, model, web_search) VALUES (?, ?, ?)",
                (user_id, default_model, int(default_web_search)),
            )
            await self._conn.commit()
            return UserSettings(user_id, default_model, None, default_web_search, True, 1, 0, None)
        return UserSettings(
            user_id=row["user_id"],
            model=row["model"],
            effort=row["effort"],
            web_search=bool(row["web_search"]),
            show_cost=bool(row["show_cost"]),
            conv_id=row["conv_id"],
            history_start_id=row["history_start_id"],
            model_override=row["model_override"],
        )

    async def update_settings(self, user_id: int, **fields: Any) -> None:
        unknown = set(fields) - self._SETTINGS_FIELDS
        if unknown:
            raise ValueError(f"Noma'lum sozlama: {unknown}")
        if not fields:
            return
        columns = ", ".join(f"{name} = ?" for name in fields)
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        await self._conn.execute(f"UPDATE settings SET {columns} WHERE user_id = ?", (*values, user_id))
        await self._conn.commit()

    async def new_conversation(self, user_id: int) -> None:
        await self._conn.execute(
            "UPDATE settings SET conv_id = conv_id + 1, history_start_id = 0, model_override = NULL WHERE user_id = ?",
            (user_id,),
        )
        await self._conn.commit()

    # --- suhbat tarixi ---
    async def history(self, user_id: int, conv_id: int, start_id: int) -> list[StoredMessage]:
        async with self._conn.execute(
            "SELECT id, role, content FROM messages WHERE user_id = ? AND conv_id = ? AND id >= ? ORDER BY id",
            (user_id, conv_id, start_id),
        ) as cur:
            rows = await cur.fetchall()
        return [StoredMessage(row["id"], row["role"], json.loads(row["content"])) for row in rows]

    async def add_turn(self, user_id: int, conv_id: int, user_content: list[dict], assistant_text: str) -> None:
        now = _now()
        assistant_content = [{"type": "text", "text": assistant_text}]
        await self._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
            (user_id, conv_id, json.dumps(user_content, ensure_ascii=False), now),
        )
        await self._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, ?)",
            (user_id, conv_id, json.dumps(assistant_content, ensure_ascii=False), now),
        )
        await self._conn.commit()

    # --- xarajatlar ---
    async def log_usage(
        self, user_id: int, model: str, numbers: dict[str, int], cost: float, *, estimated: bool = False
    ) -> None:
        await self._conn.execute(
            "INSERT INTO usage_log (user_id, model, input, output, thinking, cache_write, cache_read, searches,"
            " cost, estimated, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                model,
                numbers.get("input", 0),
                numbers.get("output", 0),
                numbers.get("thinking", 0),
                numbers.get("cache_write", 0),
                numbers.get("cache_read", 0),
                numbers.get("searches", 0),
                round(cost, 6),
                int(estimated),
                _now(),
            ),
        )
        await self._conn.commit()

    async def spend(self, user_id: int, since: datetime = EPOCH) -> tuple[float, int]:
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0), COUNT(*) FROM usage_log WHERE user_id = ? AND created_at >= ?",
            (user_id, _iso(since)),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]), int(row[1])

    async def spend_all(self, since: datetime = EPOCH) -> tuple[float, int]:
        """Barcha foydalanuvchilar sarfi (kredit hammaga umumiy)."""
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0), COUNT(*) FROM usage_log WHERE created_at >= ?", (_iso(since),)
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]), int(row[1])

    async def spend_by_user(self, since: datetime) -> list[tuple[int, float, int]]:
        async with self._conn.execute(
            "SELECT user_id, SUM(cost), COUNT(*) FROM usage_log WHERE created_at >= ?"
            " GROUP BY user_id ORDER BY SUM(cost) DESC",
            (_iso(since),),
        ) as cur:
            rows = await cur.fetchall()
        return [(int(row[0]), float(row[1]), int(row[2])) for row in rows]

    # --- parol va a'zolar ---
    async def get_config(self, key: str) -> str | None:
        async with self._conn.execute("SELECT value FROM bot_config WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_config(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO bot_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn.commit()

    async def delete_config(self, key: str) -> None:
        await self._conn.execute("DELETE FROM bot_config WHERE key = ?", (key,))
        await self._conn.commit()

    async def member(self, user_id: int) -> Member | None:
        async with self._conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return self._member(row) if row else None

    async def members(self) -> list[Member]:
        async with self._conn.execute("SELECT * FROM members ORDER BY active DESC, joined_at") as cur:
            rows = await cur.fetchall()
        return [self._member(row) for row in rows]

    async def add_member(self, user_id: int, name: str | None, username: str | None) -> None:
        await self._conn.execute(
            "INSERT INTO members (user_id, name, username, joined_at, active) VALUES (?, ?, ?, ?, 1)"
            " ON CONFLICT(user_id) DO UPDATE SET name = excluded.name, username = excluded.username, active = 1",
            (user_id, name, username, _now()),
        )
        await self._conn.commit()

    async def set_member_active(self, user_id: int, active: bool) -> None:
        await self._conn.execute("UPDATE members SET active = ? WHERE user_id = ?", (int(active), user_id))
        await self._conn.commit()

    @staticmethod
    def _member(row: Any) -> Member:
        return Member(
            user_id=row["user_id"],
            name=row["name"],
            username=row["username"],
            joined_at=row["joined_at"],
            active=bool(row["active"]),
        )

    async def spend_by_model(self, user_id: int, since: datetime) -> list[tuple[str, float, int]]:
        async with self._conn.execute(
            "SELECT model, SUM(cost), COUNT(*) FROM usage_log WHERE user_id = ? AND created_at >= ?"
            " GROUP BY model ORDER BY SUM(cost) DESC",
            (user_id, _iso(since)),
        ) as cur:
            rows = await cur.fetchall()
        return [(row[0], float(row[1]), int(row[2])) for row in rows]

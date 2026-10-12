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
CREATE TABLE IF NOT EXISTS conversations (
    user_id    INTEGER NOT NULL,
    conv_id    INTEGER NOT NULL,
    title      TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    turns      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, conv_id)
);
CREATE TABLE IF NOT EXISTS templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_alerts (
    cycle TEXT    NOT NULL,
    level INTEGER NOT NULL,
    PRIMARY KEY (cycle, level)
);
CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    conv_id    INTEGER NOT NULL DEFAULT 0,
    name       TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    size       INTEGER NOT NULL,
    file_id    TEXT,
    path       TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_user ON files (user_id, id);
"""

# Eski bazalarga qo'shiladigan ustunlar (jadval: ustun -> ta'rif)
MIGRATIONS = {
    "settings": {"mode": "TEXT"},
}
TITLE_LIMIT = 48

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
    mode: str | None = None

    @property
    def active_model(self) -> str:
        return self.model_override or self.model


@dataclass
class Conversation:
    conv_id: int
    title: str
    created_at: str
    updated_at: str
    turns: int


@dataclass
class Template:
    id: int
    user_id: int
    name: str
    text: str


@dataclass
class StoredFile:
    """Bot yaratgan fayl (Mini App galereyasi uchun): Telegram file_id va diskdagi nusxa."""

    id: int
    user_id: int
    conv_id: int
    name: str
    kind: str
    size: int
    file_id: str | None
    path: str | None
    created_at: str


def conversation_title(user_content: list[dict]) -> str:
    """Birinchi xabardan suhbat sarlavhasi: matnning boshi, matn bo'lmasa — fayl nomi."""
    for block in user_content:
        if block.get("type") == "text":
            text = " ".join(block["text"].split())
            if text.startswith("[Iqtibos]") and "[/Iqtibos]" in text:
                text = text.split("[/Iqtibos]", 1)[1].strip() or text
            if text:
                return text if len(text) <= TITLE_LIMIT else text[: TITLE_LIMIT - 1].rstrip() + "…"
    for block in user_content:
        if block.get("type") == "document":
            name = block.get("title") or (block.get("source") or {}).get("name")
            if name:
                return f"📎 {name}"[:TITLE_LIMIT]
        if block.get("type") == "image":
            return "🖼 Rasm bilan suhbat"
    return "Suhbat"


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
        {"model", "effort", "web_search", "show_cost", "conv_id", "history_start_id", "model_override", "mode"}
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
        db = cls(conn)
        await db._migrate()
        return db

    async def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            async with self._conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        # Eski suhbatlar uchun sarlavha yozuvlari (bir marta)
        async with self._conn.execute(
            "SELECT m.user_id, m.conv_id, MIN(m.id), MIN(m.created_at), MAX(m.created_at),"
            " SUM(CASE WHEN m.role = 'assistant' THEN 1 ELSE 0 END)"
            " FROM messages m LEFT JOIN conversations c ON c.user_id = m.user_id AND c.conv_id = m.conv_id"
            " WHERE c.conv_id IS NULL AND m.conv_id > 0 GROUP BY m.user_id, m.conv_id"
        ) as cur:
            missing = await cur.fetchall()
        for user_id, conv_id, first_id, created, updated, turns in missing:
            async with self._conn.execute("SELECT content FROM messages WHERE id = ?", (first_id,)) as cur:
                row = await cur.fetchone()
            try:
                title = conversation_title(json.loads(row[0])) if row else "Suhbat"
            except (ValueError, TypeError, AttributeError):
                title = "Suhbat"
            await self._conn.execute(
                "INSERT OR IGNORE INTO conversations (user_id, conv_id, title, created_at, updated_at, turns)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, conv_id, title, created, updated, turns or 0),
            )
        await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()

    async def vacuum_into(self, target: Path) -> None:
        """Bazaning izchil nusxasini alohida faylga yozadi (zaxira uchun)."""
        await self._conn.execute("VACUUM INTO ?", (str(target),))

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
            return UserSettings(user_id, default_model, None, default_web_search, True, 1, 0, None, None)
        return UserSettings(
            user_id=row["user_id"],
            model=row["model"],
            effort=row["effort"],
            web_search=bool(row["web_search"]),
            show_cost=bool(row["show_cost"]),
            conv_id=row["conv_id"],
            history_start_id=row["history_start_id"],
            model_override=row["model_override"],
            mode=row["mode"],
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
        # Yangi raqam — shu foydalanuvchining eng katta suhbat raqamidan keyingisi (tarixdan qaytilgan bo'lsa ham)
        await self._conn.execute(
            "UPDATE settings SET conv_id = (SELECT COALESCE(MAX(conv_id), 0) FROM messages WHERE user_id = ?) + 1,"
            " history_start_id = 0, model_override = NULL WHERE user_id = ?",
            (user_id, user_id),
        )
        await self._conn.commit()

    async def switch_conversation(self, user_id: int, conv_id: int) -> Conversation | None:
        conversation = await self.conversation(user_id, conv_id)
        if conversation is None:
            return None
        await self._conn.execute(
            "UPDATE settings SET conv_id = ?, history_start_id = 0, model_override = NULL WHERE user_id = ?",
            (conv_id, user_id),
        )
        await self._conn.commit()
        return conversation

    # --- suhbatlar ro'yxati ---
    async def conversation(self, user_id: int, conv_id: int) -> Conversation | None:
        async with self._conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? AND conv_id = ?", (user_id, conv_id)
        ) as cur:
            row = await cur.fetchone()
        return self._conversation(row) if row else None

    async def conversations(self, user_id: int, limit: int = 8, offset: int = 0) -> tuple[list[Conversation], int]:
        async with self._conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC, conv_id DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        async with self._conn.execute("SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)) as cur:
            total = (await cur.fetchone())[0]
        return [self._conversation(row) for row in rows], int(total)

    async def search_conversations(self, user_id: int, query: str, limit: int = 8) -> list[Conversation]:
        """Sarlavha yoki xabarlar matnida qidiradi (katta-kichik harf farqsiz)."""
        pattern = f"%{query.lower()}%"
        async with self._conn.execute(
            "SELECT c.* FROM conversations c WHERE c.user_id = ? AND (LOWER(c.title) LIKE ?"
            " OR c.conv_id IN (SELECT conv_id FROM messages WHERE user_id = ? AND LOWER(content) LIKE ?))"
            " ORDER BY c.updated_at DESC LIMIT ?",
            (user_id, pattern, user_id, pattern, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [self._conversation(row) for row in rows]

    @staticmethod
    def _conversation(row: Any) -> Conversation:
        return Conversation(
            conv_id=row["conv_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=row["turns"],
        )

    # --- suhbat tarixi ---
    async def history(self, user_id: int, conv_id: int, start_id: int) -> list[StoredMessage]:
        async with self._conn.execute(
            "SELECT id, role, content FROM messages WHERE user_id = ? AND conv_id = ? AND id >= ? ORDER BY id",
            (user_id, conv_id, start_id),
        ) as cur:
            rows = await cur.fetchall()
        return [StoredMessage(row["id"], row["role"], json.loads(row["content"])) for row in rows]

    async def add_turn(self, user_id: int, conv_id: int, user_content: list[dict], assistant_text: str) -> int:
        """Savol-javobni saqlaydi; javob yozuvining id'sini qaytaradi (javob ostidagi tugmalar uchun)."""
        now = _now()
        assistant_content = [{"type": "text", "text": assistant_text}]
        await self._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
            (user_id, conv_id, json.dumps(user_content, ensure_ascii=False), now),
        )
        cur = await self._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, ?)",
            (user_id, conv_id, json.dumps(assistant_content, ensure_ascii=False), now),
        )
        answer_id = int(cur.lastrowid)
        await self._conn.execute(
            "INSERT INTO conversations (user_id, conv_id, title, created_at, updated_at, turns) VALUES (?, ?, ?, ?, ?, 1)"
            " ON CONFLICT(user_id, conv_id) DO UPDATE SET updated_at = excluded.updated_at, turns = turns + 1",
            (user_id, conv_id, conversation_title(user_content), now, now),
        )
        await self._conn.commit()
        return answer_id

    async def save_quick_answer(self, user_id: int, text: str) -> int:
        """Tarixga kirmaydigan javob (qisqartirish, tarjima) — tugmalar ishlashi uchun conv_id = 0 bilan saqlanadi."""
        cur = await self._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (?, 0, 'assistant', ?, ?)",
            (user_id, json.dumps([{"type": "text", "text": text}], ensure_ascii=False), _now()),
        )
        await self._conn.commit()
        return int(cur.lastrowid)

    async def answer(self, answer_id: int) -> tuple[int, int, str] | None:
        """Javob yozuvi: (user_id, conv_id, matn)."""
        async with self._conn.execute(
            "SELECT user_id, conv_id, content FROM messages WHERE id = ? AND role = 'assistant'", (answer_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        blocks = json.loads(row["content"])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return int(row["user_id"]), int(row["conv_id"]), text

    async def pop_last_turn(self, user_id: int, conv_id: int, answer_id: int) -> list[dict] | None:
        """Qayta javob olish uchun: oxirgi savol-javobni o'chirib, savol matnini qaytaradi.
        answer_id suhbatning oxirgi javobi bo'lmasa — None."""
        async with self._conn.execute(
            "SELECT id, role, content FROM messages WHERE user_id = ? AND conv_id = ? ORDER BY id DESC LIMIT 2",
            (user_id, conv_id),
        ) as cur:
            rows = await cur.fetchall()
        if len(rows) != 2 or rows[0]["id"] != answer_id or rows[0]["role"] != "assistant" or rows[1]["role"] != "user":
            return None
        await self._conn.execute("DELETE FROM messages WHERE id IN (?, ?)", (rows[0]["id"], rows[1]["id"]))
        await self._conn.execute(
            "UPDATE conversations SET turns = MAX(turns - 1, 0) WHERE user_id = ? AND conv_id = ?", (user_id, conv_id)
        )
        await self._conn.commit()
        return json.loads(rows[1]["content"])

    # --- shablonlar ---
    async def templates(self, user_id: int) -> list[Template]:
        async with self._conn.execute(
            "SELECT * FROM templates WHERE user_id = ? ORDER BY name COLLATE NOCASE", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [Template(row["id"], row["user_id"], row["name"], row["text"]) for row in rows]

    async def template(self, template_id: int) -> Template | None:
        async with self._conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)) as cur:
            row = await cur.fetchone()
        return Template(row["id"], row["user_id"], row["name"], row["text"]) if row else None

    async def add_template(self, user_id: int, name: str, text: str) -> Template:
        await self._conn.execute(
            "DELETE FROM templates WHERE user_id = ? AND LOWER(name) = LOWER(?)", (user_id, name)
        )
        cur = await self._conn.execute(
            "INSERT INTO templates (user_id, name, text, created_at) VALUES (?, ?, ?, ?)", (user_id, name, text, _now())
        )
        await self._conn.commit()
        return Template(int(cur.lastrowid), user_id, name, text)

    async def delete_template(self, user_id: int, template_id: int) -> bool:
        cur = await self._conn.execute("DELETE FROM templates WHERE id = ? AND user_id = ?", (template_id, user_id))
        await self._conn.commit()
        return cur.rowcount > 0

    # --- byudjet ogohlantirishlari ---
    async def alert_sent(self, cycle: str, level: int) -> bool:
        async with self._conn.execute(
            "SELECT 1 FROM budget_alerts WHERE cycle = ? AND level = ?", (cycle, level)
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_alert(self, cycle: str, level: int) -> None:
        await self._conn.execute("INSERT OR IGNORE INTO budget_alerts (cycle, level) VALUES (?, ?)", (cycle, level))
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

    async def usage_rows(self, user_id: int | None, since: datetime) -> list[tuple[str, float, int]]:
        """(created_at, cost, searches) — kunlik grafik uchun; user_id=None — barcha foydalanuvchilar."""
        if user_id is None:
            query, params = "SELECT created_at, cost, searches FROM usage_log WHERE created_at >= ? ORDER BY id", (_iso(since),)
        else:
            query, params = (
                "SELECT created_at, cost, searches FROM usage_log WHERE user_id = ? AND created_at >= ? ORDER BY id",
                (user_id, _iso(since)),
            )
        async with self._conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [(row[0], float(row[1]), int(row[2])) for row in rows]

    # --- yaratilgan fayllar ---
    async def add_file(
        self, user_id: int, conv_id: int, name: str, kind: str, size: int, file_id: str | None, path: str | None
    ) -> int:
        cur = await self._conn.execute(
            "INSERT INTO files (user_id, conv_id, name, kind, size, file_id, path, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, conv_id, name, kind, size, file_id, path, _now()),
        )
        await self._conn.commit()
        return int(cur.lastrowid)

    async def set_file_path(self, file_pk: int, path: str | None) -> None:
        await self._conn.execute("UPDATE files SET path = ? WHERE id = ?", (path, file_pk))
        await self._conn.commit()

    async def file(self, file_pk: int) -> StoredFile | None:
        async with self._conn.execute("SELECT * FROM files WHERE id = ?", (file_pk,)) as cur:
            row = await cur.fetchone()
        return self._file(row) if row else None

    async def files(self, user_id: int, limit: int = 50, offset: int = 0) -> tuple[list[StoredFile], int]:
        async with self._conn.execute(
            "SELECT * FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, offset)
        ) as cur:
            rows = await cur.fetchall()
        async with self._conn.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user_id,)) as cur:
            total = (await cur.fetchone())[0]
        return [self._file(row) for row in rows], int(total)

    async def files_on_disk(self, user_id: int) -> list[StoredFile]:
        """Diskda nusxasi bor fayllar, eng eskisi birinchi (joy bo'shatish uchun)."""
        async with self._conn.execute(
            "SELECT * FROM files WHERE user_id = ? AND path IS NOT NULL ORDER BY id", (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [self._file(row) for row in rows]

    async def delete_file(self, file_pk: int) -> None:
        await self._conn.execute("DELETE FROM files WHERE id = ?", (file_pk,))
        await self._conn.commit()

    @staticmethod
    def _file(row: Any) -> StoredFile:
        return StoredFile(
            id=row["id"], user_id=row["user_id"], conv_id=row["conv_id"], name=row["name"], kind=row["kind"],
            size=row["size"], file_id=row["file_id"], path=row["path"], created_at=row["created_at"],
        )

    async def spend_by_model(self, user_id: int, since: datetime) -> list[tuple[str, float, int]]:
        async with self._conn.execute(
            "SELECT model, SUM(cost), COUNT(*) FROM usage_log WHERE user_id = ? AND created_at >= ?"
            " GROUP BY model ORDER BY SUM(cost) DESC",
            (user_id, _iso(since)),
        ) as cur:
            rows = await cur.fetchall()
        return [(row[0], float(row[1]), int(row[2])) for row in rows]

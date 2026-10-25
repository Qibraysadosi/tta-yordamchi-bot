"""SQLite: foydalanuvchi sozlamalari, suhbat tarixi va xarajatlar jurnali."""
from __future__ import annotations

import asyncio
import json
import sqlite3
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

-- Hisobotlar (v1.7): kuzatiladigan guruhlar, odamlar, statuslar, lidlar va ular bo'yicha harakatlar
CREATE TABLE IF NOT EXISTS rp_groups (
    chat_id   INTEGER PRIMARY KEY,
    title     TEXT    NOT NULL DEFAULT '',
    roles     TEXT    NOT NULL DEFAULT '',
    active    INTEGER NOT NULL DEFAULT 1,
    is_admin  INTEGER NOT NULL DEFAULT 0,
    added_by  INTEGER,
    added_at  TEXT    NOT NULL,
    asked     INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS rp_seen (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (chat_id, message_id)
) WITHOUT ROWID;  -- lidlar guruhida bot ko'rgan, lekin lid ham, izoh ham bo'lmagan xabarlar (e'lon, savol)
CREATE TABLE IF NOT EXISTS rp_people (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL,
    username   TEXT,
    user_id    INTEGER,
    name       TEXT    NOT NULL DEFAULT '',
    dm_ok      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rp_people_user ON rp_people (user_id);
CREATE TABLE IF NOT EXISTS rp_users (
    user_id   INTEGER PRIMARY KEY,
    username  TEXT,
    name      TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rp_statuses (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    emoji    TEXT    NOT NULL UNIQUE,
    label    TEXT    NOT NULL,
    positive INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rp_leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'post',
    created_at TEXT    NOT NULL DEFAULT '',
    name       TEXT    NOT NULL DEFAULT '',
    phones     TEXT    NOT NULL DEFAULT '',
    age        TEXT    NOT NULL DEFAULT '',
    district   TEXT    NOT NULL DEFAULT '',
    course     TEXT    NOT NULL DEFAULT '',
    text       TEXT    NOT NULL DEFAULT '',
    reminded   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_leads_time ON rp_leads (created_at);
CREATE TABLE IF NOT EXISTS rp_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER,
    user_id    INTEGER NOT NULL,
    kind       TEXT    NOT NULL,
    emoji      TEXT,
    status     TEXT,
    text       TEXT,
    analyzed   INTEGER NOT NULL DEFAULT 0,
    attempts   INTEGER NOT NULL DEFAULT 0,
    is_call    INTEGER NOT NULL DEFAULT 1,
    reason     TEXT,
    callback   TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rp_events_time ON rp_events (created_at);
CREATE INDEX IF NOT EXISTS idx_rp_events_lead ON rp_events (lead_id, user_id);
CREATE INDEX IF NOT EXISTS idx_rp_events_msg ON rp_events (chat_id, message_id);
CREATE TABLE IF NOT EXISTS rp_reports (
    period_end   TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    sent_at      TEXT NOT NULL,
    text         TEXT NOT NULL,
    insights     TEXT
);
CREATE TABLE IF NOT EXISTS rp_tasks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_chat       INTEGER,                    -- vazifa berilgan chat (guruh yoki egasining lichkasi)
    origin_msg        INTEGER,                    -- egasining xabari (✍️ reaksiya)
    poll_chat         INTEGER,                    -- «Bajardingizmi?» chiqadigan guruh (NULL — so'rovda aniqlanadi)
    poll_thread       INTEGER,                    -- forum guruhida vazifa berilgan mavzu (so'rov o'sha yerga)
    creator_id        INTEGER NOT NULL,
    assignee_id       INTEGER,
    assignee_username TEXT,
    assignee_name     TEXT    NOT NULL DEFAULT '',
    text              TEXT    NOT NULL,
    due_at            TEXT,                       -- UTC
    due_set           INTEGER NOT NULL DEFAULT 1, -- 0 — muddat aytilmagan, standart qo'yilgan
    repeat            TEXT,                       -- daily | weekly:N | monthly:D
    status            TEXT    NOT NULL DEFAULT 'open',  -- open | done | cancelled
    asks              INTEGER NOT NULL DEFAULT 0, -- oxirgi javobdan beri chiqqan so'rovlar
    no_count          INTEGER NOT NULL DEFAULT 0, -- «Yo'q» javoblari
    next_ask_at       TEXT,
    ask_chat          INTEGER,
    ask_msg           INTEGER,
    escalated         INTEGER NOT NULL DEFAULT 0,
    spawned           INTEGER NOT NULL DEFAULT 0, -- takroriy vazifaning keyingi nusxasi yaratilganmi
    parent_id         INTEGER,
    created_at        TEXT    NOT NULL,
    done_at           TEXT,
    done_by           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rp_tasks_ask ON rp_tasks (status, next_ask_at);
CREATE TABLE IF NOT EXISTS rp_task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    kind       TEXT    NOT NULL,   -- created | asked | yes | no | escalated | due | cancelled | reopened | nogroup
    user_id    INTEGER,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rp_task_events ON rp_task_events (task_id);
CREATE TABLE IF NOT EXISTS rp_journal (
    day     TEXT PRIMARY KEY,          -- mahalliy sana YYYY-MM-DD (kundalik egasiga yuborilgan kun)
    sent_at TEXT NOT NULL,             -- UTC
    text    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rp_exports (
    key       TEXT PRIMARY KEY,        -- week:YYYY-MM-DD (dushanba) | month:YYYY-MM
    kind      TEXT NOT NULL,           -- week | month
    first_day TEXT NOT NULL,           -- mahalliy sana
    last_day  TEXT NOT NULL,
    sent_at   TEXT,                    -- UTC; hamma oluvchiga yetgan vaqt (NULL — hali to'liq yuborilmagan)
    summary   TEXT NOT NULL DEFAULT '{}',
    sent_to   TEXT NOT NULL DEFAULT '' -- avtomatik nusxa yetgan chatlar (vergul bilan): qayta yuborilmaydi
);
"""

# Eski bazalarga qo'shiladigan ustunlar (jadval: ustun -> ta'rif)
MIGRATIONS = {
    "settings": {"mode": "TEXT"},
    "rp_tasks": {"poll_thread": "INTEGER"},
    "rp_exports": {"sent_to": "TEXT NOT NULL DEFAULT ''"},
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


def _fold(value: Any) -> Any:
    """Qidiruv uchun Unicode kichik harf (SQLite LOWER faqat ASCII'ni o'zgartiradi)."""
    return value.casefold() if isinstance(value, str) else value


def _search_text(content: Any) -> str:
    """Xabar JSON'idan qidiriladigan matn: matn bloklari va hujjat nomi/matni (base64 va kalitlarsiz)."""
    try:
        blocks = json.loads(content)
    except (TypeError, ValueError, RecursionError):
        return ""
    parts: list[Any] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text"))
        elif block.get("type") == "document":
            parts.append(block.get("title"))
            source = block.get("source")
            if isinstance(source, dict) and source.get("type") == "text":
                parts.append(source.get("data"))
    return "\n".join(p for p in parts if isinstance(p, str)).casefold()


def _vacuum_copy(source: Path, target: Path) -> None:
    conn = sqlite3.connect(source, timeout=60)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()


class Database:
    _SETTINGS_FIELDS = frozenset(
        {"model", "effort", "web_search", "show_cost", "conv_id", "history_start_id", "model_override", "mode"}
    )

    def __init__(self, conn: aiosqlite.Connection, path: Path | None = None) -> None:
        self._conn = conn
        self.path = path  # zaxira nusxa alohida ulanishda olinadi

    @property
    def conn(self) -> aiosqlite.Connection:
        """Boshqa modullardagi jadvallar (masalan, bot/reports/store.py) shu ulanishdan foydalanadi."""
        return self._conn

    @classmethod
    async def open(cls, path: Path) -> "Database":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.create_function("ufold", 1, _fold, deterministic=True)
            await conn.create_function("search_text", 1, _search_text, deterministic=True)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.executescript(SCHEMA)
            await conn.commit()
            db = cls(conn, path)
            await db._migrate()
        except BaseException:
            # yopilmagan ulanish oqimi jarayonni to'xtatmay qo'yadi (launcher orqaga qaytara olmaydi)
            await conn.close()
            raise
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
        """Bazaning izchil nusxasini alohida faylga yozadi (zaxira uchun). Alohida ulanishda: asosiy ulanishdagi ochiq
        tranzaksiya (masalan, izohlar tahlili yozayotgan payt) «cannot VACUUM from within a transaction» bermasin."""
        if self.path is None:
            await self._conn.execute("VACUUM INTO ?", (str(target),))
            return
        await asyncio.to_thread(_vacuum_copy, self.path, target)

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
        # Yangi raqam — shu foydalanuvchining eng katta suhbat raqamidan keyingisi (tarixdan qaytilgan bo'lsa ham).
        # Joriy raqam ham hisobga olinadi: birinchi javob hali yozilayotganda /new bosilsa, o'sha suhbat hali bazada
        # yo'q — yangi suhbat baribir yangi raqam olsin (eski savol-javob kontekstga kirmasin)
        await self._conn.execute(
            "UPDATE settings SET conv_id = MAX((SELECT COALESCE(MAX(conv_id), 0) FROM messages WHERE user_id = ?),"
            " conv_id) + 1, history_start_id = 0, model_override = NULL WHERE user_id = ?",
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
        """Sarlavha yoki xabarlar matnida qidiradi (katta-kichik harf farqsiz, % va _ oddiy belgi)."""
        escaped = query.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        async with self._conn.execute(
            "SELECT c.* FROM conversations c WHERE c.user_id = ? AND (ufold(c.title) LIKE ? ESCAPE '\\'"
            " OR c.conv_id IN (SELECT conv_id FROM messages WHERE user_id = ? AND conv_id > 0"
            " AND search_text(content) LIKE ? ESCAPE '\\'))"
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

    async def last_turn(self, user_id: int, conv_id: int, answer_id: int) -> tuple[int, list[dict]] | None:
        """Oxirgi savol-javob (o'chirmaydi): (savol yozuvi id'si, savol bloklari).
        answer_id suhbatning oxirgi javobi bo'lmasa — None."""
        async with self._conn.execute(
            "SELECT id, role, content FROM messages WHERE user_id = ? AND conv_id = ? ORDER BY id DESC LIMIT 2",
            (user_id, conv_id),
        ) as cur:
            rows = await cur.fetchall()
        if len(rows) != 2 or rows[0]["id"] != answer_id or rows[0]["role"] != "assistant" or rows[1]["role"] != "user":
            return None
        return int(rows[1]["id"]), json.loads(rows[1]["content"])

    async def pop_last_turn(self, user_id: int, conv_id: int, answer_id: int) -> list[dict] | None:
        """Qayta javob saqlanganda: oxirgi savol-javobni o'chirib, savol bloklarini qaytaradi.
        answer_id suhbatning oxirgi javobi bo'lmasa — None."""
        turn = await self.last_turn(user_id, conv_id, answer_id)
        if turn is None:
            return None
        await self._conn.execute("DELETE FROM messages WHERE id IN (?, ?)", (answer_id, turn[0]))
        await self._conn.execute(
            "UPDATE conversations SET turns = MAX(turns - 1, 0) WHERE user_id = ? AND conv_id = ?", (user_id, conv_id)
        )
        await self._conn.commit()
        return turn[1]

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

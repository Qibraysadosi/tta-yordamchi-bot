"""Hisobot ma'lumotlari: guruhlar, odamlar (operator / istisno / rahbar), statuslar, lidlar va harakatlar.

Jadvallar `bot/db.py` dagi SCHEMA'da (rp_*); vaqtlar UTC, «YYYY-MM-DDTHH:MM:SS»."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ..db import Database

ROLES = ("leads", "leaders", "tasks")
PEOPLE_KINDS = ("operator", "excluded", "leader")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
DEFAULT_STATUSES = (  # (emoji, ma'nosi, asosiy natija)
    ("👍", "keladi", 1),
    ("👎", "kelmaydi", 0),
    ("🆒", "ko'tarmadi", 0),
    ("🤔", "o'ylayapti", 0),
    ("🤝", "Telegram'dan yozadi", 0),
    ("💔", "umuman yo'q", 0),
)
SEEDED_KEY = "report:statuses_seeded"
TEXT_LIMIT = 1500


def _not_excluded(alias: str) -> str:
    """SQL sharti: harakat istisnolar ro'yxatidagi odamniki emas (istisnolar hech qayerda sanalmaydi)."""
    return f"NOT EXISTS (SELECT 1 FROM rp_people x WHERE x.kind = 'excluded' AND x.user_id = {alias}.user_id)"


def _marked(lead: str) -> str:
    """SQL sharti: lidni kimdir belgilagan — qo'ng'iroq haqida izoh yozgan (tahlil kutilayotgani ham) yoki hozir
    status reaksiyasi turibdi (olib tashlanmagan). Ko'rsatma yoki savol izohi (AI: call=false) belgilamaydi."""
    return (
        f"(EXISTS (SELECT 1 FROM rp_events c WHERE c.lead_id = {lead}.id AND c.kind = 'comment' AND c.is_call = 1"
        f" AND {_not_excluded('c')})"
        f" OR EXISTS (SELECT 1 FROM rp_events r WHERE r.lead_id = {lead}.id AND r.kind = 'reaction'"
        f" AND r.status IS NOT NULL AND {_not_excluded('r')} AND r.id = (SELECT MAX(r2.id) FROM rp_events r2"
        f" WHERE r2.lead_id = r.lead_id AND r2.user_id = r.user_id AND r2.kind = 'reaction')))"
    )


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def now_iso() -> str:
    return iso(datetime.now(timezone.utc))


def clean_username(raw: str | None) -> str | None:
    value = (raw or "").strip().lstrip("@").strip()
    if value.startswith("https://t.me/") or value.startswith("t.me/"):
        value = value.rsplit("/", 1)[-1]
    return value.lower() if USERNAME_RE.fullmatch(value) else None


@dataclass
class Group:
    chat_id: int
    title: str
    roles: tuple[str, ...]
    active: bool
    is_admin: bool
    added_by: int | None
    added_at: str
    asked: bool
    last_seen: str | None

    def has(self, role: str) -> bool:
        return self.active and role in self.roles


@dataclass
class Person:
    id: int
    kind: str
    username: str | None
    user_id: int | None
    name: str
    dm_ok: bool

    @property
    def label(self) -> str:
        return self.name or (f"@{self.username}" if self.username else f"ID {self.user_id}")


@dataclass
class Status:
    id: int
    emoji: str
    label: str
    positive: bool
    position: int


@dataclass
class Lead:
    id: int
    chat_id: int
    message_id: int
    source: str
    created_at: str
    name: str
    phones: str
    age: str
    district: str
    course: str
    reminded: bool


@dataclass
class Event:
    id: int
    lead_id: int
    chat_id: int
    message_id: int | None
    user_id: int
    kind: str  # reaction | comment
    emoji: str | None
    status: str | None
    text: str | None
    analyzed: int
    is_call: bool
    reason: str | None
    callback: str | None
    created_at: str


def _group(row: Any) -> Group:
    return Group(
        chat_id=row["chat_id"], title=row["title"], roles=tuple(r for r in row["roles"].split(",") if r),
        active=bool(row["active"]), is_admin=bool(row["is_admin"]), added_by=row["added_by"],
        added_at=row["added_at"], asked=bool(row["asked"]), last_seen=row["last_seen"],
    )


def _person(row: Any) -> Person:
    return Person(id=row["id"], kind=row["kind"], username=row["username"], user_id=row["user_id"],
                  name=row["name"], dm_ok=bool(row["dm_ok"]))


def _lead(row: Any) -> Lead:
    return Lead(
        id=row["id"], chat_id=row["chat_id"], message_id=row["message_id"], source=row["source"],
        created_at=row["created_at"], name=row["name"], phones=row["phones"], age=row["age"],
        district=row["district"], course=row["course"], reminded=bool(row["reminded"]),
    )


def _event(row: Any) -> Event:
    return Event(
        id=row["id"], lead_id=row["lead_id"], chat_id=row["chat_id"], message_id=row["message_id"],
        user_id=row["user_id"], kind=row["kind"], emoji=row["emoji"], status=row["status"], text=row["text"],
        analyzed=row["analyzed"], is_call=bool(row["is_call"]), reason=row["reason"], callback=row["callback"],
        created_at=row["created_at"],
    )


class ReportStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def conn(self):
        return self.db.conn

    async def _all(self, query: str, params: Iterable[Any] = ()) -> list[Any]:
        async with self.conn.execute(query, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def _one(self, query: str, params: Iterable[Any] = ()) -> Any:
        async with self.conn.execute(query, tuple(params)) as cur:
            return await cur.fetchone()

    async def _run(self, query: str, params: Iterable[Any] = ()) -> int:
        cur = await self.conn.execute(query, tuple(params))
        await self.conn.commit()
        return cur.lastrowid or 0

    # ------------------------------------------------------------------ guruhlar
    async def group(self, chat_id: int) -> Group | None:
        row = await self._one("SELECT * FROM rp_groups WHERE chat_id = ?", (chat_id,))
        return _group(row) if row else None

    async def groups(self, *, active_only: bool = False) -> list[Group]:
        query = "SELECT * FROM rp_groups" + (" WHERE active = 1" if active_only else "") + " ORDER BY added_at"
        return [_group(r) for r in await self._all(query)]

    async def groups_with(self, role: str) -> list[Group]:
        return [g for g in await self.groups(active_only=True) if role in g.roles]

    async def upsert_group(self, chat_id: int, title: str, *, added_by: int | None = None,
                           is_admin: bool | None = None, active: bool | None = None) -> Group:
        cur = await self.conn.execute(  # bir vaqtda kelgan yangilanishlar ikkinchi marta qo'shmasin
            "INSERT INTO rp_groups (chat_id, title, roles, active, is_admin, added_by, added_at, last_seen)"
            " VALUES (?, ?, '', 1, ?, ?, ?, ?) ON CONFLICT(chat_id) DO NOTHING",
            (chat_id, title or "", int(bool(is_admin)), added_by, now_iso(), now_iso()),
        )
        await self.conn.commit()
        existing = None if cur.rowcount else await self.group(chat_id)
        if existing is not None:
            fields = {"title": title or existing.title, "last_seen": now_iso()}
            if is_admin is not None:
                fields["is_admin"] = int(is_admin)
            if added_by is not None:
                fields["added_by"] = added_by
            if active is not None:
                fields["active"] = int(active)
            sets = ", ".join(f"{k} = ?" for k in fields)
            await self._run(f"UPDATE rp_groups SET {sets} WHERE chat_id = ?", (*fields.values(), chat_id))
        group = await self.group(chat_id)
        assert group is not None
        return group

    async def set_group_roles(self, chat_id: int, roles: Iterable[str]) -> None:
        value = ",".join(r for r in ROLES if r in set(roles))
        await self._run("UPDATE rp_groups SET roles = ?, asked = 1 WHERE chat_id = ?", (value, chat_id))

    async def set_group_active(self, chat_id: int, active: bool) -> None:
        await self._run("UPDATE rp_groups SET active = ?, asked = 1 WHERE chat_id = ?", (int(active), chat_id))

    async def set_group_admin(self, chat_id: int, is_admin: bool) -> None:
        await self._run("UPDATE rp_groups SET is_admin = ? WHERE chat_id = ?", (int(is_admin), chat_id))

    async def mark_group_asked(self, chat_id: int) -> None:
        await self._run("UPDATE rp_groups SET asked = 1 WHERE chat_id = ?", (chat_id,))

    async def claim_ask(self, chat_id: int) -> bool:
        """Guruh turi egasidan bir marta so'raladi: birinchi chaqiruv True, qolganlari False."""
        cur = await self.conn.execute("UPDATE rp_groups SET asked = 1 WHERE chat_id = ? AND asked = 0", (chat_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def migrate_group(self, old_id: int, new_id: int) -> None:
        """Oddiy guruh superguruhga aylanganda chat_id o'zgaradi: guruh sozlamalari yangi ID ga o'tadi.

        Eski lid va harakatlar eski ID da qoladi (superguruhda xabar raqamlari qaytadan boshlanadi)."""
        old = await self.group(old_id)
        if old is None:
            return
        new = await self.group(new_id)
        if new is None:
            await self._run("UPDATE rp_groups SET chat_id = ? WHERE chat_id = ?", (new_id, old_id))
            return
        roles = new.roles if new.roles else old.roles  # yangi guruh xabari oldin kelib, qayta yaratilgan bo'lsa
        await self.conn.execute(
            "UPDATE rp_groups SET roles = ?, active = ?, asked = 1, added_by = COALESCE(added_by, ?) WHERE chat_id = ?",
            (",".join(roles), int(old.active or new.active), old.added_by, new_id),
        )
        await self.conn.execute("DELETE FROM rp_groups WHERE chat_id = ?", (old_id,))
        await self.conn.commit()

    async def mark_seen(self, chat_id: int, message_id: int) -> None:
        """Bot ko'rgan, lekin lid ham, izoh ham bo'lmagan xabar (e'lon, savol): unga reaksiya — qo'ng'iroq emas."""
        await self._run("INSERT OR IGNORE INTO rp_seen (chat_id, message_id) VALUES (?, ?)", (chat_id, message_id))

    async def was_seen(self, chat_id: int, message_id: int) -> bool:
        row = await self._one("SELECT 1 FROM rp_seen WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
        return row is not None

    # ------------------------------------------------------------------ odamlar
    async def people(self, kind: str | None = None) -> list[Person]:
        if kind:
            rows = await self._all("SELECT * FROM rp_people WHERE kind = ? ORDER BY id", (kind,))
        else:
            rows = await self._all("SELECT * FROM rp_people ORDER BY kind, id")
        return [_person(r) for r in rows]

    async def person(self, person_id: int) -> Person | None:
        row = await self._one("SELECT * FROM rp_people WHERE id = ?", (person_id,))
        return _person(row) if row else None

    async def add_person(self, kind: str, *, username: str | None, user_id: int | None, name: str = "") -> Person:
        if kind not in PEOPLE_KINDS:
            raise ValueError(kind)
        if username is None and user_id is None:
            raise ValueError("username yoki ID kerak")
        for person in await self.people(kind):
            if (username and person.username == username) or (user_id and person.user_id == user_id):
                if name and name != person.name:
                    await self._run("UPDATE rp_people SET name = ? WHERE id = ?", (name, person.id))
                    person.name = name
                return person
        if user_id is None and username:
            known = await self._one(
                "SELECT user_id FROM rp_users WHERE username = ? ORDER BY last_seen DESC LIMIT 1", (username,)
            )
            user_id = known["user_id"] if known else None
            same = next((p for p in await self.people(kind) if user_id and p.user_id == user_id), None)
            if same is not None:  # shu odam ro'yxatda ID bilan bor — ikkinchi yozuv ochilmaydi
                await self._run("UPDATE rp_people SET username = ? WHERE id = ?", (username, same.id))
                if name and name != same.name:
                    await self._run("UPDATE rp_people SET name = ? WHERE id = ?", (name, same.id))
                return await self.person(same.id)  # type: ignore[return-value]
        new_id = await self._run(
            "INSERT INTO rp_people (kind, username, user_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, username, user_id, name.strip()[:40], now_iso()),
        )
        person = await self.person(new_id)
        assert person is not None
        return person

    async def update_person(self, person_id: int, *, name: str | None = None, kind: str | None = None) -> None:
        if name is not None:
            await self._run("UPDATE rp_people SET name = ? WHERE id = ?", (name.strip()[:40], person_id))
        if kind is not None and kind in PEOPLE_KINDS:
            await self._run("UPDATE rp_people SET kind = ? WHERE id = ?", (kind, person_id))

    async def delete_person(self, person_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM rp_people WHERE id = ?", (person_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def roles_of(self, user_id: int, username: str | None) -> set[str]:
        """Odamning hisobotdagi turlari (operator / excluded / leader).

        ID bilan bog'langan yozuv faqat ID bo'yicha topiladi: username bo'shatilib, boshqa odam olgan bo'lsa ham
        uning huquqlari o'tmaydi. Username faqat hali bog'lanmagan (ID'siz) yozuvlarda ishlatiladi."""
        name = (username or "").lower() or None
        rows = await self._all(
            "SELECT kind FROM rp_people WHERE user_id = ? OR (user_id IS NULL AND username IS NOT NULL AND username = ?)",
            (user_id, name),
        )
        return {r["kind"] for r in rows}

    async def bind_user(self, user_id: int, username: str | None) -> None:
        """@username bilan qo'shilgan odamni Telegram ID'si bilan bog'laydi (username o'zgarsa ham taniladi)."""
        name = (username or "").lower()
        if name:
            await self.conn.execute("UPDATE rp_people SET user_id = ? WHERE user_id IS NULL AND username = ?", (user_id, name))
            # username almashgan bo'lsa — ro'yxatda yangisi ko'rinadi (eski nom boshqa odamga huquq bermaydi)
            await self.conn.execute(
                "UPDATE rp_people SET username = ? WHERE user_id = ? AND (username IS NULL OR username != ?)",
                (name, user_id, name),
            )
            await self._dedupe(user_id)
            await self.conn.commit()

    async def _dedupe(self, user_id: int) -> None:
        """Bir odam bir ro'yxatda ikki marta (username va ID bilan) qolmasin — eskisi saqlanadi."""
        await self.conn.execute(
            "DELETE FROM rp_people WHERE user_id = ? AND EXISTS (SELECT 1 FROM rp_people q"
            " WHERE q.user_id = rp_people.user_id AND q.kind = rp_people.kind AND q.id < rp_people.id)",
            (user_id,),
        )

    async def set_dm_ok(self, user_id: int) -> None:
        await self._run("UPDATE rp_people SET dm_ok = 1 WHERE user_id = ?", (user_id,))

    # ------------------------------------------------------------------ ko'ringan foydalanuvchilar
    async def remember_user(self, user_id: int, username: str | None, name: str) -> None:
        uname = (username or "").lower() or None
        await self.conn.execute(
            "INSERT INTO rp_users (user_id, username, name, last_seen) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, name = excluded.name,"
            " last_seen = excluded.last_seen",
            (user_id, uname, name[:60], now_iso()),
        )
        if uname:
            # Username bir vaqtda bitta odamda: eski egasining yozuvidan olib tashlanadi
            await self.conn.execute("UPDATE rp_users SET username = NULL WHERE username = ? AND user_id != ?", (uname, user_id))
            # Ro'yxatga @username bilan qo'shilganlar birinchi ko'ringanda ID bilan bog'lanadi
            await self.conn.execute(
                "UPDATE rp_people SET user_id = ? WHERE user_id IS NULL AND username = ?", (user_id, uname)
            )
            # Bog'langan odam username'ini almashtirsa — ro'yxatda yangisi ko'rinadi
            await self.conn.execute(
                "UPDATE rp_people SET username = ? WHERE user_id = ? AND (username IS NULL OR username != ?)",
                (uname, user_id, uname),
            )
            await self._dedupe(user_id)
        await self.conn.commit()

    async def users(self) -> dict[int, tuple[str | None, str]]:
        rows = await self._all("SELECT user_id, username, name FROM rp_users")
        return {r["user_id"]: (r["username"], r["name"]) for r in rows}

    # ------------------------------------------------------------------ statuslar
    async def seed_statuses(self) -> None:
        if await self.db.get_config(SEEDED_KEY):
            return
        if not await self._all("SELECT id FROM rp_statuses LIMIT 1"):
            for position, (emoji, label, positive) in enumerate(DEFAULT_STATUSES):
                await self.conn.execute(
                    "INSERT OR IGNORE INTO rp_statuses (emoji, label, positive, position) VALUES (?, ?, ?, ?)",
                    (emoji, label, positive, position),
                )
            await self.conn.commit()
        await self.db.set_config(SEEDED_KEY, "1")

    async def statuses(self) -> list[Status]:
        rows = await self._all("SELECT * FROM rp_statuses ORDER BY position, id")
        return [Status(id=r["id"], emoji=r["emoji"], label=r["label"], positive=bool(r["positive"]),
                       position=r["position"]) for r in rows]

    async def add_status(self, emoji: str, label: str, positive: bool) -> Status:
        last = await self._one("SELECT COALESCE(MAX(position), -1) AS p FROM rp_statuses")
        new_id = await self._run(
            "INSERT INTO rp_statuses (emoji, label, positive, position) VALUES (?, ?, ?, ?)",
            (emoji, label, int(positive), int(last["p"]) + 1),
        )
        return next(s for s in await self.statuses() if s.id == new_id)

    async def update_status(self, status_id: int, *, label: str | None = None, positive: bool | None = None,
                            position: int | None = None) -> bool:
        fields: dict[str, Any] = {}
        if label is not None:
            fields["label"] = label
        if positive is not None:
            fields["positive"] = int(positive)
        if position is not None:
            fields["position"] = position
        if not fields:
            return False
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = await self.conn.execute(f"UPDATE rp_statuses SET {sets} WHERE id = ?", (*fields.values(), status_id))
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_status(self, status_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM rp_statuses WHERE id = ?", (status_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ lidlar
    async def lead_by_message(self, chat_id: int, message_id: int) -> Lead | None:
        row = await self._one("SELECT * FROM rp_leads WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
        return _lead(row) if row else None

    async def lead(self, lead_id: int) -> Lead | None:
        row = await self._one("SELECT * FROM rp_leads WHERE id = ?", (lead_id,))
        return _lead(row) if row else None

    async def save_lead(self, chat_id: int, message_id: int, *, source: str, created_at: str = "",
                        name: str = "", phones: Iterable[str] = (), age: str = "", district: str = "",
                        course: str = "", text: str = "") -> Lead:
        """Yangi lid yoki mavjudini to'ldirish («stub» — faqat reaksiya ko'rilgan, mazmuni noma'lum)."""
        phone_text = ",".join(phones)
        cur = await self.conn.execute(  # bir vaqtdagi ikki yangilanish (reaksiya + izoh) — bitta lid
            "INSERT INTO rp_leads (chat_id, message_id, source, created_at, name, phones, age, district, course, text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, message_id) DO NOTHING",
            (chat_id, message_id, source, created_at, name, phone_text, age, district, course, text[:TEXT_LIMIT]),
        )
        await self.conn.commit()
        existing = None if cur.rowcount else await self.lead_by_message(chat_id, message_id)
        if existing is not None and source != "stub":
            await self._run(
                "UPDATE rp_leads SET source = CASE WHEN source = 'stub' THEN ? ELSE source END,"
                " created_at = CASE WHEN created_at = '' THEN ? ELSE created_at END,"
                " name = ?, phones = ?, age = ?, district = ?, course = ?, text = ? WHERE id = ?",
                (source, created_at, name or existing.name, phone_text or existing.phones, age or existing.age,
                 district or existing.district, course or existing.course, text[:TEXT_LIMIT], existing.id),
            )
        lead = await self.lead_by_message(chat_id, message_id)
        assert lead is not None
        return lead

    async def leads_between(self, start: str, end: str) -> list[Lead]:
        """Shu davrda guruhga tushgan (mazmuni ma'lum) lidlar."""
        rows = await self._all(
            "SELECT * FROM rp_leads WHERE source != 'stub' AND created_at >= ? AND created_at < ? ORDER BY created_at",
            (start, end),
        )
        return [_lead(r) for r in rows]

    async def leads_by_ids(self, ids: Iterable[int]) -> dict[int, Lead]:
        ids = list(set(ids))
        result: dict[int, Lead] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for row in await self._all(f"SELECT * FROM rp_leads WHERE id IN ({marks})", chunk):
                result[row["id"]] = _lead(row)
        return result

    async def touched_lead_ids(self, lead_ids: Iterable[int]) -> set[int]:
        """Kimdir belgilagan lidlar: izoh yoki hozir turgan status reaksiyasi (istisnolarniki sanalmaydi)."""
        ids = list(set(lead_ids))
        touched: set[int] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = await self._all(f"SELECT l.id FROM rp_leads l WHERE l.id IN ({marks}) AND {_marked('l')}", chunk)
            touched.update(r["id"] for r in rows)
        return touched

    async def unmarked_leads(self, created_after: str, created_before: str) -> list[Lead]:
        """Eslatma uchun: hech kim belgilamagan va hali eslatilmagan lidlar (belgilash qoidasi — touched_lead_ids)."""
        rows = await self._all(
            "SELECT * FROM rp_leads l WHERE source != 'stub' AND reminded = 0 AND created_at >= ? AND created_at < ?"
            f" AND NOT {_marked('l')} ORDER BY created_at",
            (created_after, created_before),
        )
        return [_lead(r) for r in rows]

    async def mark_reminded(self, lead_ids: Iterable[int]) -> None:
        for lead_id in set(lead_ids):
            await self.conn.execute("UPDATE rp_leads SET reminded = 1 WHERE id = ?", (lead_id,))
        await self.conn.commit()

    # ------------------------------------------------------------------ harakatlar
    async def is_comment_message(self, chat_id: int, message_id: int) -> bool:
        row = await self._one(
            "SELECT 1 FROM rp_events WHERE chat_id = ? AND message_id = ? AND kind = 'comment' LIMIT 1",
            (chat_id, message_id),
        )
        return row is not None

    async def add_reaction(self, lead: Lead, user_id: int, emoji: str | None, status: str | None, created_at: str) -> int:
        return await self._run(
            "INSERT INTO rp_events (lead_id, chat_id, user_id, kind, emoji, status, analyzed, created_at)"
            " VALUES (?, ?, ?, 'reaction', ?, ?, 1, ?)",
            (lead.id, lead.chat_id, user_id, emoji, status, created_at),
        )

    async def add_comment(self, lead: Lead, message_id: int, user_id: int, text: str, created_at: str,
                          *, analyzable: bool = True) -> int:
        return await self._run(
            "INSERT INTO rp_events (lead_id, chat_id, message_id, user_id, kind, text, analyzed, created_at)"
            " VALUES (?, ?, ?, ?, 'comment', ?, ?, ?)",
            (lead.id, lead.chat_id, message_id, user_id, text[:TEXT_LIMIT], 0 if analyzable else 1, created_at),
        )

    async def edit_comment(self, chat_id: int, message_id: int, text: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE rp_events SET text = ?, analyzed = 0, attempts = 0, status = NULL, reason = NULL, callback = NULL,"
            " is_call = 1 WHERE chat_id = ? AND message_id = ? AND kind = 'comment'",
            (text[:TEXT_LIMIT], chat_id, message_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def pending_comments(self, limit: int) -> list[Event]:
        rows = await self._all(
            f"SELECT * FROM rp_events e WHERE kind = 'comment' AND analyzed = 0 AND {_not_excluded('e')}"
            " ORDER BY id LIMIT ?", (limit,)
        )
        return [_event(r) for r in rows]

    async def pending_count(self, start: str | None = None, end: str | None = None) -> int:
        query = f"SELECT COUNT(*) AS n FROM rp_events e WHERE kind = 'comment' AND analyzed = 0 AND {_not_excluded('e')}"
        params: list[str] = []
        if start and end:
            query += " AND created_at >= ? AND created_at < ?"
            params = [start, end]
        row = await self._one(query, params)
        return int(row["n"])

    async def save_analysis(self, event_id: int, *, is_call: bool, status: str | None, reason: str | None,
                            callback: str | None) -> None:
        await self.conn.execute(
            "UPDATE rp_events SET analyzed = 1, is_call = ?, status = ?, reason = ?, callback = ? WHERE id = ?",
            (int(is_call), status, reason, callback, event_id),
        )

    async def analysis_failed(self, event_ids: Iterable[int], max_attempts: int) -> None:
        for event_id in event_ids:
            await self.conn.execute(
                "UPDATE rp_events SET attempts = attempts + 1,"
                " analyzed = CASE WHEN attempts + 1 >= ? THEN 2 ELSE analyzed END WHERE id = ?",
                (max_attempts, event_id),
            )
        await self.conn.commit()

    async def commit(self) -> None:
        await self.conn.commit()

    async def current_reaction(self, lead_id: int, user_id: int) -> str | None:
        row = await self._one(
            "SELECT status FROM rp_events WHERE lead_id = ? AND user_id = ? AND kind = 'reaction' ORDER BY id DESC LIMIT 1",
            (lead_id, user_id),
        )
        return row["status"] if row else None

    async def events_between(self, start: str, end: str) -> list[Event]:
        rows = await self._all(
            "SELECT * FROM rp_events WHERE created_at >= ? AND created_at < ? ORDER BY created_at, id", (start, end)
        )
        return [_event(r) for r in rows]

    async def reactions_before(self, start: str, pairs: Iterable[tuple[int, int]]) -> dict[tuple[int, int], str | None]:
        """Davr boshigacha bo'lgan oxirgi reaksiya (lid, odam juftligi uchun) — izohda status bo'lmasa ishlatiladi."""
        result: dict[tuple[int, int], str | None] = {}
        for lead_id, user_id in set(pairs):
            row = await self._one(
                "SELECT status FROM rp_events WHERE lead_id = ? AND user_id = ? AND kind = 'reaction' AND created_at < ?"
                " ORDER BY created_at DESC, id DESC LIMIT 1",
                (lead_id, user_id, start),
            )
            if row is not None:
                result[(lead_id, user_id)] = row["status"]
        return result

    async def counts_since(self, since: str) -> dict[str, int]:
        """Tashxis uchun: oxirgi davrdagi lidlar, reaksiyalar va izohlar soni."""
        leads = await self._one("SELECT COUNT(*) AS n FROM rp_leads WHERE created_at >= ?", (since,))
        rows = await self._all(
            "SELECT kind, COUNT(*) AS n FROM rp_events WHERE created_at >= ? GROUP BY kind", (since,)
        )
        counts = {r["kind"]: int(r["n"]) for r in rows}
        return {"leads": int(leads["n"]), "reactions": counts.get("reaction", 0), "comments": counts.get("comment", 0)}

    # ------------------------------------------------------------------ yuborilgan hisobotlar
    async def report_for_day(self, day: str) -> dict | None:
        """Shu kun («YYYY-MM-DD») uchun yuborilgan kunlik hisobot: vaqt o'zgartirilsa ham bir kunga bitta."""
        row = await self._one(
            "SELECT * FROM rp_reports WHERE substr(period_end, 1, 10) = ? ORDER BY sent_at DESC LIMIT 1", (day,)
        )
        return dict(row) if row else None

    async def save_report(self, period_end: str, period_start: str, text: str, insights: str | None) -> None:
        await self._run(
            "INSERT INTO rp_reports (period_end, period_start, sent_at, text, insights) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(period_end) DO UPDATE SET sent_at = excluded.sent_at, text = excluded.text,"
            " insights = excluded.insights",
            (period_end, period_start, now_iso(), text, insights),
        )

    async def last_report(self) -> dict | None:
        row = await self._one("SELECT period_end, sent_at FROM rp_reports ORDER BY sent_at DESC LIMIT 1")
        return dict(row) if row else None

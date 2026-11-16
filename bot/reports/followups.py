"""Qayta qo'ng'iroq navbati (v1.17): operator «ertaga qayta qilaman» deb yozsa yoki lidga 🆒 / 🤔 qo'ysa — bot vaqti
kelganda o'sha operatorga lichkada eslatadi; lidga yangi belgi (reaksiya yoki izoh) qo'yilsa — bajarildi.

Manba — rp_events (reaksiyalar va tahlil qilingan izohlar), yozilish tartibida (kursor bot_config'da). Har yangi
qo'ng'iroq shu lidning ochiq navbatini yopadi va kerak bo'lsa yangisini ochadi:
- izohda vaqt aytilgan bo'lsa («ertaga 15:00», «dushanba», «kechqurun», «2 soatdan keyin») — o'sha vaqtga (callback);
- aks holda statusning «qayta qo'ng'iroq» sozlamasi bo'yicha (retry: standart 🆒 — 3 soatdan keyin, 🤔 — ertasi kuni;
  Mini App → Sozlamalar → Statuslar);
- asosiy natija (👍 keladi) — navbat ochilmaydi.
Vaqt ish vaqtiga suriladi (ish kunlari, work_start–work_end). Bir odamning 15 daqiqa ichidagi keyingi belgisi (avval
reaksiya, keyin izoh) oldingisini almashtiradi — «bajarildi» sanalmaydi. O'rnatilgunga qadar qo'yilgan belgilar
navbatga tushmaydi. Istisnolar sanalmaydi.
Ko'rinadi: operatorga eslatma (ish vaqtida, 5 daqiqada bir tekshiradi; Schedule.followups), kunlik hisobot va
kundalikda «🔁 Qayta qo'ng'iroq: 9/12», /qayta (egasi), AI suhbati (report_stats → callbacks), kunlik tavsiya."""
from __future__ import annotations

import html
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from aiogram.enums import ParseMode
from aiogram.exceptions import AiogramError

from ..translit import to_latin
from .leads import message_link
from .stats import Schedule, in_work_hours
from .store import Event, ReportStore, Status, iso
from .tasks import from_iso

if TYPE_CHECKING:
    from .service import ReportService

log = logging.getLogger(__name__)

CURSOR_KEY = "report:followups_cursor"
REPLACE_WINDOW = timedelta(minutes=15)
MAX_AHEAD = timedelta(days=14)
CHECK_EVERY = timedelta(minutes=5)
SYNC_BATCH = 500
SHOWN = 15
RETRY_CHOICES = (0, 60, 120, 180, 1440, 2880)  # Mini App'dagi tanlovlar (daqiqa)
RETRY_MAX = 7 * 24 * 60
MORNING, AFTERNOON, EVENING, DAY_TIME = time(10, 0), time(14, 0), time(17, 0), time(10, 0)
WEEKDAY_WORDS = (  # uzunlari oldin: «dushanba» ichida «shanba» bor
    ("chorshanba", 2), ("payshanba", 3), ("yakshanba", 6), ("dushanba", 0), ("seshanba", 1), ("shanba", 5),
    ("juma", 4), ("ponedelnik", 0), ("vtornik", 1), ("sred", 2), ("chetverg", 3), ("pyatnits", 4), ("subbot", 5),
    ("voskresen", 6),
)
NUMBER_WORDS = {"bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "tort": 4, "besh": 5, "olti": 6, "yarim": 0.5, "odin": 1,
                "dva": 2, "tri": 3}
REL_RE = re.compile(r"(\d+(?:[.,]\d+)?|bir|ikki|uch|to'rt|tort|besh|olti|yarim|odin|dva|tri)\s*"
                    r"(soat|chas|daqiqa|minut|kun|den|dn)")
CLOCK_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")
DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b")  # 22.10.2026 — sana (soat emas)
DAY_MONTH_RE = re.compile(r"\b(\d{1,2})\s*-?\s*(yanv|fev|mart|apr|may|iyun|iyn|iyul|iyl|avg|sen|okt|noy|dek)[a-z']*")
MONTHS = {"yanv": 1, "fev": 2, "mart": 3, "apr": 4, "may": 5, "iyun": 6, "iyn": 6, "iyul": 7, "iyl": 7, "avg": 8,
          "sen": 9, "okt": 10, "noy": 11, "dek": 12}
HOUR_RE = re.compile(r"\bsoat\s*([01]?\d|2[0-3])\b|\bv\s*([01]?\d|2[0-3])\b")


def esc(text: str) -> str:
    """Telegram HTML uchun (o'zbekcha apostrof o'zgarmaydi)."""
    return html.escape(text, quote=False)


def _hm(value: str) -> time:
    hours, minutes = (int(x) for x in value.split(":"))
    return time(hours, minutes)


def _norm(text: str) -> str:
    return " ".join(to_latin(text or "").lower().translate(str.maketrans({c: "'" for c in "ʻʼ‘’`´"})).split())


def parse_callback(text: str | None, at: datetime) -> datetime | None:
    """Izohdagi qayta qo'ng'iroq vaqti (AI qisqa yozgan: «ertaga 15:00», «dushanba», «kechqurun», «2 soatdan keyin»)
    → aniq vaqt (at vaqt zonasida). Tushunarsiz yoki 14 kundan uzoq bo'lsa — None (status sozlamasi ishlaydi)."""
    raw = _norm(text or "")
    if not raw:
        return None
    tokens = re.findall(r"[a-z']+", raw)
    relative = REL_RE.search(raw)
    if relative:
        amount_text, unit = relative.group(1), relative.group(2)
        amount = NUMBER_WORDS.get(amount_text) or float(amount_text.replace(",", "."))
        if unit in ("soat", "chas"):
            due = at + timedelta(hours=amount)
        elif unit in ("daqiqa", "minut"):
            due = at + timedelta(minutes=amount)
        else:
            due = datetime.combine(at.date() + timedelta(days=max(1, round(amount))), DAY_TIME, tzinfo=at.tzinfo)
        return due if at < due <= at + MAX_AHEAD else None
    day: date | None = None
    exact = DATE_RE.search(raw)
    named = DAY_MONTH_RE.search(raw) if exact is None else None
    try:
        if exact is not None:
            year = int(exact.group(3))
            day = date(year + 2000 if year < 100 else year, int(exact.group(2)), int(exact.group(1)))
            raw = raw.replace(exact.group(0), " ")
        elif named is not None:
            day = date(at.year, MONTHS[named.group(2)], int(named.group(1)))
            if day < at.date() - timedelta(days=1):  # «5-yanvar» dekabrda aytilgan — keyingi yil
                day = day.replace(year=at.year + 1)
            raw = raw.replace(named.group(0), " ")
    except ValueError:  # 31-fevral kabi
        day = None
    if day is None:
        if any(t.startswith(("ertaga", "ertasi", "zavtra")) for t in tokens):
            day = at.date() + timedelta(days=1)
        elif any(t.startswith(("indin", "poslezavtra")) for t in tokens):
            day = at.date() + timedelta(days=2)
        elif any(t in ("bugun", "segodnya") or t.startswith("bugun") for t in tokens):
            day = at.date()
        elif ("keyingi" in tokens or "kelasi" in tokens or any(t.startswith("sleduyushch") for t in tokens)) \
                and any(t.startswith(("hafta", "nedel")) for t in tokens):
            day = at.date() + timedelta(days=7 - at.weekday())
        else:
            for word, weekday in WEEKDAY_WORDS:
                if any(t.startswith(word) for t in tokens):
                    ahead = (weekday - at.weekday()) % 7 or 7
                    day = at.date() + timedelta(days=ahead)
                    break
    clock: time | None = None
    found = CLOCK_RE.search(raw)
    if found:
        clock = time(int(found.group(1)), int(found.group(2)))
    else:
        hour_match = HOUR_RE.search(raw)
        if hour_match:
            hour = int(hour_match.group(1) or hour_match.group(2))
            clock = time(hour + 12 if hour < 8 else hour, 0)  # «soat 3 da» — kunduzi 15:00
        elif any(t.startswith(("ertalab", "tong", "utr")) for t in tokens):
            clock = MORNING
        elif any(t.startswith(("tush", "obed", "abed", "abet", "obet")) for t in tokens):
            clock = AFTERNOON
        elif any(t.startswith(("kech", "vecher")) for t in tokens):
            clock = EVENING
    if day is None:  # faqat vaqt: bugun o'tib ketgan bo'lsa — ertaga
        if clock is None:
            return None
        day = at.date() if datetime.combine(at.date(), clock, tzinfo=at.tzinfo) > at else at.date() + timedelta(days=1)
    due = datetime.combine(day, clock or DAY_TIME, tzinfo=at.tzinfo)
    if due <= at and day == at.date():  # «bugun kechqurun» kechqurun aytilgan — bir soatdan keyin
        due = at + timedelta(hours=1)
    return due if at < due <= at + MAX_AHEAD else None


def into_work(moment: datetime, s: Schedule) -> datetime:
    """Vaqtni ish vaqtiga suradi: ish kuni, work_start–work_end (tashqarida bo'lsa — keyingi ish boshlanishi)."""
    start_t, end_t = _hm(s.work_start), _hm(s.work_end)
    for _ in range(15):
        if moment.weekday() in s.days:
            day_start = datetime.combine(moment.date(), start_t, tzinfo=moment.tzinfo)
            day_end = datetime.combine(moment.date(), end_t, tzinfo=moment.tzinfo)
            if moment < day_start:
                return day_start
            if moment < day_end:
                return moment
        moment = datetime.combine(moment.date() + timedelta(days=1), start_t, tzinfo=moment.tzinfo)
    return moment


def retry_label(minutes: int) -> str:
    if minutes <= 0:
        return "yo'q"
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "ertasi kuni" if days == 1 else f"{days} kundan keyin"
    if minutes % 60 == 0:
        return f"{minutes // 60} soatdan keyin"
    return f"{minutes} daqiqadan keyin"


@dataclass
class Followup:
    id: int
    lead_id: int
    user_id: int
    event_id: int | None
    kind: str
    note: str
    created_at: str
    due_at: str
    reminded_at: str | None
    notified: bool
    done_at: str | None
    done_by: int | None
    cancelled: bool


def _followup(row: Any) -> Followup:
    return Followup(id=row["id"], lead_id=row["lead_id"], user_id=row["user_id"], event_id=row["event_id"],
                    kind=row["kind"], note=row["note"], created_at=row["created_at"], due_at=row["due_at"],
                    reminded_at=row["reminded_at"], notified=bool(row["notified"]), done_at=row["done_at"],
                    done_by=row["done_by"], cancelled=bool(row["cancelled"]))


class FollowupStore(ReportStore):
    async def add(self, *, lead_id: int, user_id: int, event_id: int | None, kind: str, note: str, created_at: str,
                  due_at: str) -> int:
        return await self._run(
            "INSERT INTO rp_followups (lead_id, user_id, event_id, kind, note, created_at, due_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)", (lead_id, user_id, event_id, kind, note[:80], created_at, due_at))

    async def open_for_lead(self, lead_id: int) -> list[Followup]:
        rows = await self._all("SELECT * FROM rp_followups WHERE lead_id = ? AND done_at IS NULL AND cancelled = 0",
                               (lead_id,))
        return [_followup(r) for r in rows]

    async def close(self, followup_id: int, *, done_at: str, done_by: int) -> None:
        await self._run("UPDATE rp_followups SET done_at = ?, done_by = ? WHERE id = ?", (done_at, done_by, followup_id))

    async def cancel(self, followup_id: int) -> None:
        await self._run("UPDATE rp_followups SET cancelled = 1 WHERE id = ?", (followup_id,))

    async def due(self, now: str) -> list[Followup]:
        rows = await self._all(
            "SELECT * FROM rp_followups WHERE done_at IS NULL AND cancelled = 0 AND reminded_at IS NULL AND due_at <= ?"
            " ORDER BY due_at, id", (now,))
        return [_followup(r) for r in rows]

    async def mark_sent(self, ids: list[int], at: str, notified: bool) -> None:
        for followup_id in ids:
            await self.conn.execute("UPDATE rp_followups SET reminded_at = ?, notified = ? WHERE id = ?",
                                    (at, int(notified), followup_id))
        await self.conn.commit()

    async def between(self, start: str, end: str) -> list[Followup]:
        """Vaqti shu davrga to'g'ri kelganlar (almashtirilganlarsiz)."""
        rows = await self._all("SELECT * FROM rp_followups WHERE cancelled = 0 AND due_at >= ? AND due_at < ?"
                               " ORDER BY due_at, id", (start, end))
        return [_followup(r) for r in rows]

    async def waiting(self) -> list[Followup]:
        rows = await self._all("SELECT * FROM rp_followups WHERE done_at IS NULL AND cancelled = 0 ORDER BY due_at, id")
        return [_followup(r) for r in rows]


class FollowupService:
    def __init__(self, reports: "ReportService") -> None:
        self.reports = reports
        self.store = FollowupStore(reports.ctx.db)
        self._next: datetime | None = None

    # ------------------------------------------------------------------ navbatni yangilash
    async def sync(self) -> int:
        """Yangi belgilardan navbatni yangilaydi; nechta qo'ng'iroq ko'rilganini qaytaradi."""
        reports, db = self.reports, self.reports.ctx.db
        store = reports.store
        raw = await db.get_config(CURSOR_KEY)
        if not raw:  # birinchi ishga tushish: eski belgilar navbatga tushmaydi
            await db.set_config(CURSOR_KEY, str(await store.max_event_id()))
            return 0
        cursor = int(raw)
        events = await store.events_after(cursor, SYNC_BATCH)
        if not events:
            return 0
        excluded = await store.excluded_ids()
        statuses = {st.emoji: st for st in await store.statuses()}
        s = await reports.schedule()
        handled = 0
        for event in events:
            if event.user_id in excluded:  # istisnolar hech qayerda sanalmaydi (izohlari tahlil ham qilinmaydi)
                cursor = event.id
                continue
            if event.kind == "comment" and event.analyzed == 0:
                break  # izoh tahlilini kutamiz — belgilar tartibi buzilmasin
            cursor = event.id
            if not (event.status if event.kind == "reaction" else event.is_call):
                continue  # reaksiya olib tashlandi yoki izoh qo'ng'iroq haqida emas
            await self._on_call(event, statuses, s)
            handled += 1
        await db.set_config(CURSOR_KEY, str(cursor))
        return handled

    async def _on_call(self, event: Event, statuses: dict[str, Status], s: Schedule) -> None:
        tz = self.reports.tz
        at = from_iso(event.created_at, tz)
        if at is None:
            return
        for item in await self.store.open_for_lead(event.lead_id):
            created = from_iso(item.created_at, tz)
            if item.user_id == event.user_id and created is not None and at - created <= REPLACE_WINDOW:
                await self.store.cancel(item.id)  # o'sha odamning aniqlashtirishi (reaksiya → izoh)
            else:
                await self.store.close(item.id, done_at=event.created_at, done_by=event.user_id)
        emoji = event.status
        if emoji is None and event.kind == "comment":
            emoji = await self.reports.store.current_reaction(event.lead_id, event.user_id)
        status = statuses.get(emoji) if emoji else None
        if status is not None and status.positive:
            return  # asosiy natija (masalan, keladi) — qayta qo'ng'iroq kerak emas
        due, kind, note = None, "", ""
        if event.kind == "comment" and event.callback:
            due = parse_callback(event.callback, at)
            kind, note = "callback", event.callback
        if due is None and status is not None and status.retry_minutes > 0:
            due = at + timedelta(minutes=status.retry_minutes)
            kind, note = "retry", f"{status.emoji} {status.label}"
        if due is None:
            return
        await self.store.add(lead_id=event.lead_id, user_id=event.user_id, event_id=event.id, kind=kind, note=note,
                             created_at=event.created_at, due_at=iso(into_work(due, s)))

    # ------------------------------------------------------------------ eslatma
    async def check(self, bot: Any, now: datetime) -> int:
        """30 soniyalik sikldan: navbatni yangilaydi; ish vaqtida (5 daqiqada bir) vaqti kelganlarni eslatadi."""
        await self.sync()
        if self._next is not None and now < self._next:
            return 0
        self._next = now + CHECK_EVERY
        s = await self.reports.schedule()
        if not s.followups or not in_work_hours(now, s):
            return 0
        due = await self.store.due(iso(now))
        if not due:
            return 0
        leads = await self.reports.store.leads_by_ids(f.lead_id for f in due)
        by_user: dict[int, list[Followup]] = defaultdict(list)
        for item in due:
            by_user[item.user_id].append(item)
        sent = 0
        for user_id, items in by_user.items():
            try:
                await bot.send_message(user_id, self._reminder(items, leads, now), parse_mode=ParseMode.HTML)
                notified = True
                sent += 1
            except AiogramError as exc:
                log.info("Qayta qo'ng'iroq eslatmasi yuborilmadi (%s): %s", user_id, exc)
                notified = False
            await self.store.mark_sent([f.id for f in items], iso(now), notified)
        return sent

    def _when(self, value: str, now: datetime) -> str:
        moment = from_iso(value, self.reports.tz)
        if moment is None:
            return ""
        if moment.date() == now.date():
            return f"bugun {moment:%H:%M}"
        if moment.date() == now.date() - timedelta(days=1):
            return f"kecha {moment:%H:%M}"
        return f"{moment:%d.%m %H:%M}"

    def _item(self, item: Followup, leads: dict, now: datetime, *, own: bool = True) -> str:
        lead = leads.get(item.lead_id)
        what = " — ".join(x for x in ((lead.name if lead else "") or "Ism ko'rsatilmagan", lead.course if lead else "") if x)
        said = "deb yozgansiz" if own else "deb yozgan"
        why = (f"«{item.note}» {said}" if item.kind == "callback" else item.note) + f" ({self._when(item.created_at, now)})"
        link = message_link(lead.chat_id, lead.message_id) if lead else None
        return f"• {esc(what)} · {esc(why)}" + (f' · <a href="{link}">ochish</a>' if link else "")

    def _reminder(self, items: list[Followup], leads: dict, now: datetime) -> str:
        lines = [f"🔁 <b>Qayta qo'ng'iroq vaqti keldi</b> — {len(items)} ta lid", ""]
        lines += [self._item(item, leads, now) for item in items[:SHOWN]]
        if len(items) > SHOWN:
            lines.append(f"… va yana {len(items) - SHOWN} ta")
        lines += ["", "Qo'ng'iroqdan keyin lid postiga reaksiya yoki izoh qo'ying — navbatdan chiqadi."]
        return "\n".join(lines)

    # ------------------------------------------------------------------ hisob
    async def stats(self, start: datetime, end: datetime, now: datetime, *, hide: Any = str) -> dict:
        """Vaqti davrga to'g'ri kelgan va'dalar: jami, bajarilgan, o'tib ketgan, kutilayotgan — odamlar bo'yicha."""
        items = await self.store.between(iso(start), iso(end))
        names = await self.reports.archive._names()
        moment = iso(now)
        per: dict[str, dict[str, int]] = defaultdict(lambda: {"due": 0, "done": 0, "missed": 0, "waiting": 0})
        for item in items:
            row = per[hide(names(item.user_id))]
            row["due"] += 1
            key = "done" if item.done_at else ("missed" if item.due_at <= moment else "waiting")
            row[key] += 1
        totals = {key: sum(row[key] for row in per.values()) for key in ("due", "done", "missed", "waiting")}
        return {**totals, "not_delivered": sum(1 for i in items if i.reminded_at and not i.notified and not i.done_at),
                "by_person": [{"name": name, **row} for name, row in sorted(per.items(), key=lambda kv: -kv[1]["due"])]}

    @staticmethod
    def _missed_text(data: dict) -> str:
        missed = [(row["name"], row["missed"]) for row in data["by_person"] if row["missed"]]
        return ", ".join(f"{esc(name)} {count}" for name, count in missed[:5]) + (" …" if len(missed) > 5 else "")

    async def report_lines(self, start: datetime, end: datetime, now: datetime) -> list[str]:
        """Kunlik hisobot (rahbarlar guruhi) oxiridagi qator — davrda vaqti kelgan va'dalar bo'lsa."""
        data = await self.stats(start, min(end, now), now)
        if not data["due"]:
            return []
        line = f"🔁 Qayta qo'ng'iroq: <b>{data['done']}</b>/{data['due']} bajarildi"
        if data["missed"]:
            line += f" · qolgan: {self._missed_text(data)}"
        return [line]

    async def journal_lines(self, day: date, now: datetime) -> list[str]:
        tz = self.reports.tz
        start = datetime.combine(day, time(0), tzinfo=tz)
        data = await self.stats(start, start + timedelta(days=1), now)
        tomorrow = await self.store.between(iso(start + timedelta(days=1)), iso(start + timedelta(days=2)))
        if not data["due"] and not tomorrow:
            return []
        lines = [f"🔁 <b>Qayta qo'ng'iroq</b>: bugun {data['due']} ta vaqti keldi — {data['done']} tasi qilindi"
                 + (f", {data['missed']} tasi qoldi ({self._missed_text(data)})" if data["missed"] else "")
                 + (f", {data['waiting']} tasi kutilmoqda" if data["waiting"] else "")]
        if data["not_delivered"]:
            lines.append(f"⚠️ {data['not_delivered']} ta eslatma yetmadi — operator botni ochmagan (/start bosishi kerak)")
        if tomorrow:
            lines.append(f"📅 Ertaga rejada: {len(tomorrow)} ta")
        return lines

    async def queue_text(self, now: datetime) -> str:
        """/qayta: o'tib ketgan va bugungi qayta qo'ng'iroqlar (odamlar bo'yicha), so'nggi 7 kun natijasi."""
        reports = self.reports
        s = await reports.schedule()
        names = await reports.archive._names()
        waiting = await self.store.waiting()
        leads = await reports.store.leads_by_ids(f.lead_id for f in waiting)
        moment = iso(now)
        end_of_day = iso(datetime.combine(now.date() + timedelta(days=1), time(0), tzinfo=now.tzinfo))
        overdue = [f for f in waiting if f.due_at <= moment]
        today = [f for f in waiting if moment < f.due_at < end_of_day]
        later = [f for f in waiting if f.due_at >= end_of_day]
        lines = ["🔁 <b>Qayta qo'ng'iroq navbati</b>", ""]
        if not waiting:
            lines.append("Navbat bo'sh.")
        for title, items in (("⏰ Vaqti o'tgan", overdue), ("📅 Bugun", today)):
            if not items:
                continue
            lines.append(f"<b>{title}: {len(items)}</b>")
            groups: dict[int, list[Followup]] = defaultdict(list)
            for item in items:
                groups[item.user_id].append(item)
            for user_id, mine in sorted(groups.items(), key=lambda kv: -len(kv[1])):
                lines.append(f"{esc(names(user_id))} — {len(mine)} ta")
                lines += [self._item(item, leads, now, own=False) for item in mine[:5]]
                if len(mine) > 5:
                    lines.append(f"… va yana {len(mine) - 5} ta")
            lines.append("")
        if later:
            lines.append(f"Keyingi kunlarga: {len(later)} ta")
        week = await self.stats(now - timedelta(days=7), now, now)
        if week["due"]:
            lines.append(f"So'nggi 7 kun: {week['due']} tadan {week['done']} tasi bajarildi"
                         + (f" · bajarilmagan: {self._missed_text(week)}" if week["missed"] else ""))
        state = "yoqiq" if s.followups else "o'chiq"
        lines += ["", f"<i>Operatorlarga eslatma: {state} (Mini App → Sozlamalar). Status bo'yicha muddatlar — "
                      "Sozlamalar → Statuslar.</i>"]
        return "\n".join(lines).replace("\n\n\n", "\n\n")

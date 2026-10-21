"""Kundalik va haftalik / oylik Excel hisobot (v1.9).

Qoidalar (kelishilgan):
- Kundalik — faqat egasiga lichkada, ish kunlari journal_time (20:00) da: call center raqamlari (bugungi hisobot
  davri) va vazifalar (bugun bajarilgan va berilgan, muddati o'tgan, javobsiz, yaqin muddatlar). Bir kunga bitta;
  bot o'chiq bo'lib 4 soatdan ko'p kechiksa — o'sha kun yuborilmaydi.
- Excel — dushanba (o'tgan hafta) va oyning 1-sanasi (o'tgan oy) excel_time (09:00) da: rahbarlar guruhiga
  telefonlari yashirilgan nusxa, egasiga to'liq nusxa. Bot o'chiq bo'lsa — hafta (oy) ichida yetkaziladi.
- Davr — kunlik hisobotlar davrlarining yig'indisi (raqamlar kunlik hisobotlar bilan bir xil qoidada).
- v1.9 o'rnatilishidan oldingi davrlar avtomatik yuborilmaydi (ARCHIVE_SINCE_KEY); qo'lda — /excel yoki Mini App."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from ..prompt import MONTHS, WEEKDAYS
from .excel import DayLine, ExcelReport, LeadLine, OpLine, TaskLine, build_workbook
from .leads import message_link
from .render import split_message
from .stats import (
    OTHER,
    Overview,
    Schedule,
    build_overview,
    current_day,
    month_of,
    overview_between,
    period_bounds,
    report_days,
    span_bounds,
    week_of,
)
from .store import ReportStore, _not_excluded, iso, now_iso
from .tasks import CANCELLED, DONE, OPEN, Task, due_label, from_iso, next_work_day, repeat_label

if TYPE_CHECKING:
    from .service import ReportService

log = logging.getLogger(__name__)

ARCHIVE_SINCE_KEY = "report:archive_since"  # v1.9 ishga tushgan vaqt: undan oldingi davrlar avtomatik yuborilmaydi
WEEK, MONTH = "week", "month"
JOURNAL_LATE = timedelta(hours=4)  # bot o'chiq bo'lsa — kundalik shuncha kechikib ham yuboriladi
SEND_RETRY = timedelta(minutes=10)  # hech qayerga yetmasa — qayta urinish
FLUSH_BATCHES = 5
LIST_LIMIT = {WEEK: 26, MONTH: 12}
SHOWN = 5  # kundalikdagi ro'yxatlar
TASK_TEXT = 70
STATE_TEXT = {
    "waiting": "⏳ kutilmoqda", "overdue": "🔴 muddati o'tdi", "asked": "🔔 so'raldi", "no": "❌ «Yo'q» dedi",
    "silent": "⚠️ javob yo'q", "done": "✅ bajarildi", "cancelled": "🗑 bekor qilingan",
}
KEY_RE = re.compile(r"^(week|month|journal):([0-9-]{7,10})$")


# ------------------------------------------------------------------ davrlar
def period_key(kind: str, first: date) -> str:
    return f"{WEEK}:{first:%Y-%m-%d}" if kind == WEEK else f"{MONTH}:{first:%Y-%m}"


def parse_key(key: str) -> tuple[str, date, date] | None:
    """«week:2026-10-12» (dushanba) yoki «month:2026-10» → (tur, birinchi kun, oxirgi kun)."""
    match = KEY_RE.match(key or "")
    if match is None:
        return None
    kind, raw = match.groups()
    try:
        if kind == WEEK:
            first = datetime.strptime(raw, "%Y-%m-%d").date()
            if first.weekday() != 0:
                return None
            return (WEEK, *week_of(first))
        if kind == MONTH:
            return (MONTH, *month_of(datetime.strptime(raw, "%Y-%m").date()))
    except ValueError:
        return None
    return None


def period_label(kind: str, first: date, last: date) -> str:
    if kind == MONTH:
        return f"{MONTHS[first.month - 1].capitalize()} {first.year}"
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}–{last.day} {MONTHS[first.month - 1]} {first.year}"
    if first.year == last.year:
        return f"{first.day} {MONTHS[first.month - 1]} – {last.day} {MONTHS[last.month - 1]} {first.year}"
    return f"{first.day} {MONTHS[first.month - 1]} {first.year} – {last.day} {MONTHS[last.month - 1]} {last.year}"


def current_period(kind: str, today: date) -> tuple[date, date]:
    return week_of(today) if kind == WEEK else month_of(today)


def previous_period(kind: str, first: date) -> tuple[date, date]:
    return current_period(kind, first - timedelta(days=1))


def due_period(kind: str, today: date) -> tuple[date, date, date]:
    """Yuboriladigan davr: o'tgan hafta (oy) va uni yuborish kuni (dushanba / oyning 1-sanasi)."""
    first, _ = current_period(kind, today)
    previous = previous_period(kind, first)
    return previous[0], previous[1], first


def _at(day: date, hhmm: str, tz: Any) -> datetime:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=tz)


def _naive(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=None) if moment is not None else None


@dataclass
class Period:
    kind: str
    first: date
    last: date
    start: datetime | None  # hisobot davri: birinchi hisobot kunining boshidan oxirgisining oxirigacha
    end: datetime | None
    live: bool  # hali tugamagan (joriy hafta yoki oy)

    @property
    def key(self) -> str:
        return period_key(self.kind, self.first)

    @property
    def label(self) -> str:
        return period_label(self.kind, self.first, self.last)

    @property
    def title(self) -> str:
        return ("Haftalik" if self.kind == WEEK else "Oylik") + " hisobot"

    @property
    def filename(self) -> str:
        if self.kind == WEEK:
            name = f"Haftalik_hisobot_{self.first:%Y-%m-%d}_{self.last:%Y-%m-%d}"
        else:
            name = f"Oylik_hisobot_{self.first:%Y-%m}"
        return name + ("_oraliq" if self.live else "") + ".xlsx"


@dataclass
class Export:
    period: Period
    report: ExcelReport
    summary: dict


# ------------------------------------------------------------------ ma'lumotlar
class ArchiveStore(ReportStore):
    """rp_journal (kundaliklar) va rp_exports (haftalik / oylik Excel) jadvallari."""

    async def journal(self, day: str) -> dict | None:
        row = await self._one("SELECT * FROM rp_journal WHERE day = ?", (day,))
        return dict(row) if row else None

    async def save_journal(self, day: str, text: str) -> None:
        await self._run(
            "INSERT INTO rp_journal (day, sent_at, text) VALUES (?, ?, ?)"
            " ON CONFLICT(day) DO UPDATE SET sent_at = excluded.sent_at, text = excluded.text",
            (day, now_iso(), text),
        )

    async def journals(self, limit: int = 60) -> list[dict]:
        rows = await self._all("SELECT day, sent_at FROM rp_journal ORDER BY day DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    async def export(self, key: str) -> dict | None:
        row = await self._one("SELECT * FROM rp_exports WHERE key = ?", (key,))
        return dict(row) if row else None

    async def exports(self, kind: str) -> dict[str, dict]:
        rows = await self._all("SELECT * FROM rp_exports WHERE kind = ?", (kind,))
        return {r["key"]: dict(r) for r in rows}

    async def save_export(self, period: Period, summary: dict, *, sent: bool) -> None:
        """Davr ko'rsatkichlari (Arxiv ro'yxati uchun); sent=True — avtomatik yuborildi deb belgilanadi."""
        await self._run(
            "INSERT INTO rp_exports (key, kind, first_day, last_day, sent_at, summary) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET summary = excluded.summary,"
            " sent_at = COALESCE(excluded.sent_at, rp_exports.sent_at)",
            (period.key, period.kind, period.first.isoformat(), period.last.isoformat(), now_iso() if sent else None,
             json.dumps(summary, ensure_ascii=False)),
        )

    async def daily_reports(self, limit: int = 60) -> list[dict]:
        rows = await self._all(
            "SELECT period_end, period_start, sent_at FROM rp_reports ORDER BY period_end DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    async def lead_notes(self, lead_ids: Iterable[int]) -> dict[int, dict]:
        """Lid bo'yicha so'nggi qo'ng'iroq izohi (matn, sabab, qayta qo'ng'iroq) — istisnolarniki hisobga olinmaydi."""
        ids = list(set(lead_ids))
        notes: dict[int, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = await self._all(
                f"SELECT lead_id, text, reason, callback FROM rp_events e WHERE kind = 'comment' AND is_call = 1"
                f" AND lead_id IN ({marks}) AND {_not_excluded('e')} ORDER BY created_at, id",
                chunk,
            )
            for row in rows:
                notes[row["lead_id"]] = dict(row)
        return notes


# ------------------------------------------------------------------ xizmat
class ArchiveService:
    def __init__(self, reports: "ReportService") -> None:
        self.reports = reports
        self.ctx = reports.ctx
        self.store = ArchiveStore(reports.ctx.db)
        self._journal_retry: datetime | None = None
        self._export_retry: dict[str, datetime] = {}

    @property
    def tz(self):
        return self.reports.tz

    async def archive_since(self) -> datetime:
        raw = await self.ctx.db.get_config(ARCHIVE_SINCE_KEY)
        try:
            return datetime.strptime(raw or "", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:  # yozuv buzilgan bo'lsa — cheklov yo'q (aks holda hech narsa yuborilmay qolardi)
            return datetime(2000, 1, 1, tzinfo=timezone.utc)

    def period(self, kind: str, first: date, last: date, s: Schedule, now: datetime) -> Period:
        span = span_bounds(first, last, s, self.tz)
        start, end = span if span else (None, None)
        return Period(kind=kind, first=first, last=last, start=start, end=end, live=end is None or now < end)

    def local(self, value: str | None) -> datetime | None:
        return from_iso(value, self.tz)

    # -------------------------------------------------------------- kundalik
    async def journal_text(self, now: datetime, *, interim: bool = False) -> str:
        reports = self.reports
        s = await reports.schedule()
        today = now.date()
        lines = [f"📓 <b>KUNDALIK · {today:%d.%m.%Y} ({WEEKDAYS[today.weekday()]})</b>"]
        if interim:
            lines.append(f"<i>hozirgi holat · {now:%H:%M}</i>")
        if await reports.store.groups_with("leads"):
            await reports.analyze_pending(FLUSH_BATCHES)  # izohlar tahlilsiz qolmasin
            day = today if today.weekday() in s.days else current_day(now, s)
            lines += [""] + self._journal_calls(await build_overview(reports.store, s, day, now))
        lines += [""] + await self._journal_tasks(now, s)
        return "\n".join(lines)

    @staticmethod
    def _journal_calls(ov: Overview) -> list[str]:
        everyone = ov.rows + ov.extras
        calls = sum(r.total for r in everyone)
        positive = sum(ov.positive(r) for r in everyone)
        main = next((st for st in ov.statuses if st.positive), None)
        counts: Counter = Counter()
        for r in everyone:
            counts.update(r.counts)
        period = f"{ov.start:%d.%m %H:%M} → {ov.end:%d.%m %H:%M}" + (" · hozircha" if ov.live else "")
        lines = [f"📞 <b>Call center</b> · <i>{period}</i>"]
        if calls:
            head = f"Qo'ng'iroq: <b>{calls}</b> ta lid"
            if main is not None:
                head += f" · {main.emoji} {html.escape(main.label)} <b>{positive}</b> ({round(positive * 100 / calls)}%)"
            lines.append(head)
            lines.append(" · ".join(f"{c} {counts[c]}" for c in ov.columns() if counts.get(c)))
        else:
            lines.append("Bu davrda qo'ng'iroq qayd etilmadi.")
        lines.append(f"📥 Yangi lidlar: {ov.new_leads} · belgilanmagan: {ov.unmarked}")
        top = sorted((r for r in everyone if r.total), key=lambda r: (-r.total, r.name.lower()))[:3]
        if top:
            mark = main.emoji if main is not None else ""
            lines.append("🏆 " + " · ".join(
                f"{html.escape(r.name)} {r.total}" + (f" ({mark} {ov.positive(r)})" if mark else "") for r in top))
        if ov.pending:
            lines.append(f"⏳ {ov.pending} ta izoh hali tahlil qilinmadi")
        return lines

    def _task_item(self, task: Task, *, with_due: bool = False) -> str:
        text = task.text if len(task.text) <= TASK_TEXT else task.text[: TASK_TEXT - 1] + "…"
        due = self.local(task.due_at)
        tail = f" · {due_label(due)}" if with_due and due else ""
        return f"• <b>{html.escape(task.who)}</b> — {html.escape(text)}{tail}"

    def _task_block(self, title: str, tasks: list[Task], *, with_due: bool = False) -> list[str]:
        lines = [f"{title}: {len(tasks)}"]
        lines += [self._task_item(t, with_due=with_due) for t in tasks[:SHOWN]]
        if len(tasks) > SHOWN:
            lines.append(f"… va yana {len(tasks) - SHOWN} ta")
        return lines

    async def _journal_tasks(self, now: datetime, s: Schedule) -> list[str]:
        store = self.reports.tasks.store
        day_start = datetime.combine(now.date(), time(0), tzinfo=self.tz)
        start, upto = iso(day_start), iso(now)
        done = await store.closed_between(DONE, start, upto)
        given = await store.created_count(start, upto)
        refused = await store.events_count("no", start, upto)
        open_tasks = await store.tasks(OPEN, 1000)
        horizon = datetime.combine(next_work_day(now.date(), s) + timedelta(days=1), time(0), tzinfo=self.tz)
        silent = [t for t in open_tasks if t.escalated]
        overdue = [t for t in open_tasks if not t.escalated and t.due_at and t.due_at < iso(now)]
        upcoming = [t for t in open_tasks if not t.escalated and t.due_at and iso(now) <= t.due_at < iso(horizon)]
        lines = [f"📋 <b>Vazifalar</b> · ochiq: {len(open_tasks)}"]
        if not (open_tasks or done or given):
            return lines + ["Vazifa yo'q. Berish: «#vazifa @username nima qilish kerak, qachongacha»."]
        if done:
            lines += self._task_block("✅ Bugun bajarildi", done)
        if given:
            lines.append(f"🆕 Bugun berildi: {given}")
        if overdue:
            lines += self._task_block("🔴 Muddati o'tgan", overdue, with_due=True)
        if silent:
            lines += self._task_block("⚠️ Javob bermayapti", silent, with_due=True)
        if refused:
            lines.append(f"❌ Bugun «Yo'q» javoblari: {refused}")
        if upcoming:
            last_day = horizon - timedelta(days=1)
            lines += self._task_block(f"⏰ Yaqin muddatlar ({last_day:%d.%m} gacha)", upcoming, with_due=True)
        if len(lines) == 1:
            lines.append("Bugun o'zgarish yo'q.")
        return lines

    def journal_markup(self, day: date) -> InlineKeyboardMarkup | None:
        buttons = [b for b in (self.reports.app_button(f"hisobot_{day:%Y%m%d}", "📊 Hisobot"),
                               self.reports.app_button("vazifalar", "📋 Vazifalar")) if b is not None]
        return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None

    async def send_text(self, bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None) -> None:
        parts = split_message(text)
        for index, part in enumerate(parts):
            await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML,
                                   reply_markup=markup if index == len(parts) - 1 else None)

    async def check_journal(self, bot: Bot, now: datetime) -> bool:
        s = await self.reports.schedule()
        today = now.date()
        if not s.journal_enabled or today.weekday() not in s.days:
            return False
        due = _at(today, s.journal_time, self.tz)
        if now < due or now - due > JOURNAL_LATE:
            return False
        if await self.store.journal(today.isoformat()) is not None:
            return False
        if due < await self.archive_since():  # o'rnatilgan kunning o'tib ketgan vaqti — keyingi kundan
            return False
        if self._journal_retry is not None and now < self._journal_retry:
            return False
        if not await self.reports.store.groups_with("leads") and not await self.reports.tasks.store.has_any():
            return False  # hali hech narsa ulanmagan — bo'sh kundalik yuborilmaydi
        text = await self.journal_text(now)
        await self.reports.refresh_bot_info(bot)
        markup = self.journal_markup(today)
        sent = 0
        for owner_id in self.reports.owners():
            try:
                await self.send_text(bot, owner_id, text, markup)
                sent += 1
            except TelegramAPIError as exc:
                log.warning("Kundalik yuborilmadi (%s): %s", owner_id, exc)
        if not sent:
            self._journal_retry = now + SEND_RETRY
            return False
        await self.store.save_journal(today.isoformat(), text)
        self._journal_retry = None
        return True

    # -------------------------------------------------------------- Excel
    async def summarize(self, period: Period, now: datetime, s: Schedule) -> tuple[Overview | None, list[Task], dict]:
        """Davr ko'rsatkichlari: call center (kunlik hisobot qoidasida) va muddati shu davrdagi vazifalar."""
        store = self.reports.store
        ov = None
        if period.start is not None and period.end is not None and period.start < now:
            ov = await overview_between(store, s, period.start, period.end, now, period.last)
        t_start = iso(datetime.combine(period.first, time(0), tzinfo=self.tz))
        t_end = iso(datetime.combine(period.last + timedelta(days=1), time(0), tzinfo=self.tz))
        tasks = await self.reports.tasks.store.touching(t_start, t_end)
        due = [t for t in tasks if t.due_at and t_start <= t.due_at < t_end and t.status != CANCELLED]
        done = [t for t in due if t.status == DONE]
        everyone = (ov.rows + ov.extras) if ov else []
        calls = sum(r.total for r in everyone)
        positive = sum(ov.positive(r) for r in everyone) if ov else 0
        main = next((st for st in (ov.statuses if ov else []) if st.positive), None)
        top = sorted((r for r in everyone if r.total), key=lambda r: (-r.total, r.name.lower()))[:5]
        summary = {
            "calls": calls, "positive": positive, "positive_pct": round(positive * 100 / calls) if calls else 0,
            "positive_emoji": main.emoji if main else "", "positive_label": main.label if main else "",
            "new_leads": ov.new_leads if ov else 0, "unmarked": ov.unmarked if ov else 0,
            "tasks_due": len(due), "tasks_done": len(done), "tasks_open": sum(1 for t in due if t.status == OPEN),
            "tasks_on_time": sum(1 for t in done if t.done_at and t.due_at and t.done_at <= t.due_at),
            "top": [[r.name, r.total, ov.positive(r)] for r in top] if ov else [],
            "period": (f"{period.start:%d.%m %H:%M} → {period.end:%d.%m %H:%M}"
                       if period.start and period.end else ""),
        }
        return ov, tasks, summary

    async def collect(self, kind: str, first: date, last: date, now: datetime) -> Export:
        reports = self.reports
        store = reports.store
        s = await reports.schedule()
        await reports.analyze_pending(FLUSH_BATCHES)
        period = self.period(kind, first, last, s, now)
        ov, tasks, summary = await self.summarize(period, now, s)
        statuses = await store.statuses()
        labels = {st.emoji: st.label for st in statuses}
        labels[OTHER] = "aniqlanmagan"
        positives = [st for st in statuses if st.positive]
        positive_label = " / ".join(f"{st.emoji} {st.label}" for st in positives) or "Asosiy natija"
        columns = [(c, labels.get(c, "")) for c in (ov.columns() if ov else [st.emoji for st in statuses])]

        operators = []
        if ov is not None:
            for listed, group in ((True, ov.rows), (False, ov.extras)):
                operators += [OpLine(name=row.name, username=row.username, total=row.total, counts=dict(row.counts),
                                     positive=ov.positive(row), listed=listed) for row in group]

        days: list[DayLine] = []
        for day in report_days(first, last, s):
            start, _ = period_bounds(day, s, self.tz)
            if start >= now:
                break
            day_ov = await build_overview(store, s, day, now)
            people = day_ov.rows + day_ov.extras
            counts: Counter = Counter()
            for row in people:
                counts.update(row.counts)
            days.append(DayLine(day=day, weekday=WEEKDAYS[day.weekday()], total=sum(r.total for r in people),
                                counts=dict(counts), positive=sum(day_ov.positive(r) for r in people),
                                new_leads=day_ov.new_leads, unmarked=day_ov.unmarked, live=day_ov.live))

        leads: list[LeadLine] = []
        if ov is not None and period.start is not None:
            new = await store.leads_between(iso(period.start), iso(min(ov.end, now)))
            outcome = ov if ov.live else await overview_between(store, s, period.start, now, now)
            notes = await self.store.lead_notes(lead.id for lead in new)
            names = await self._names()
            for lead in new:
                status = outcome.lead_status.get(lead.id)
                note = notes.get(lead.id) or {}
                leads.append(LeadLine(
                    created=_naive(self.local(lead.created_at)), name=lead.name, phones=lead.phones.split(","),
                    age=lead.age, district=lead.district, course=lead.course,
                    status=f"{status} {labels.get(status, '')}".strip() if status else "belgilanmagan",
                    positive=status in {st.emoji for st in positives},
                    people=", ".join(names(uid) for uid in outcome.lead_people.get(lead.id, [])),
                    note=" ".join(str(note.get("text") or "").split()), reason=note.get("reason") or "",
                    callback=note.get("callback") or "", link=message_link(lead.chat_id, lead.message_id),
                ))

        task_lines = [
            TaskLine(who=t.who, text=t.text, created=_naive(self.local(t.created_at)), due=_naive(self.local(t.due_at)),
                     state=STATE_TEXT.get(self.reports.tasks.state_of(t, now), t.status),
                     closed=_naive(self.local(t.done_at)) if t.status != OPEN else None, no_count=t.no_count,
                     repeat=repeat_label(t.repeat))
            for t in tasks
        ]

        notes_text = []
        since = (await reports.since()).astimezone(self.tz)
        if period.start is not None and period.start < since:
            notes_text.append(f"Kuzatish {since:%d.%m.%Y %H:%M} dan boshlangan — undan oldingi qo'ng'iroqlar yo'q.")
        if ov is not None and ov.pending:
            notes_text.append(f"{ov.pending} ta izoh hali tahlil qilinmagan.")
        if period.live:
            notes_text.append("Davr hali tugamagan — oraliq holat.")
        period_text = "Hisobot davri: —"
        if period.start is not None and period.end is not None:
            period_text = (f"Hisobot davri: {period.start:%d.%m.%Y %H:%M} → {period.end:%d.%m.%Y %H:%M}"
                           f" · {len(report_days(first, last, s))} ish kuni")
            if period.live:
                period_text += f" · {now:%d.%m %H:%M} gacha"
        calls, positive = summary["calls"], summary["positive"]
        due_count, done_count = summary["tasks_due"], summary["tasks_done"]
        rows: list[tuple[str, int, float | None]] = [
            ("Qo'ng'iroq qilingan lidlar", calls, None),
            (f"{positive_label} — asosiy natija", positive, positive / calls if calls else None),
            ("Yangi lidlar", summary["new_leads"], None),
            ("Belgilanmagan yangi lidlar", summary["unmarked"],
             summary["unmarked"] / summary["new_leads"] if summary["new_leads"] else None),
        ]
        if ov is not None:
            rows += [("Qo'ng'iroq haqidagi izohlar", ov.comments, None),
                     ("Qayta qo'ng'iroq so'raganlar", ov.callbacks, None),
                     ("Ziddiyat: reaksiya va izoh mos emas", ov.conflicts, None)]
        rows += [
            ("Vazifalar (muddati shu davrda)", due_count, None),
            ("— bajarildi", done_count, done_count / due_count if due_count else None),
            ("— o'z vaqtida", summary["tasks_on_time"], summary["tasks_on_time"] / due_count if due_count else None),
            ("— hali ochiq", summary["tasks_open"], None),
        ]
        report = ExcelReport(
            title=f"{period.title} · {period.label}" + (" (oraliq)" if period.live else ""),
            period=period_text, generated=f"Tayyorlandi: {now:%d.%m.%Y %H:%M}", filename=period.filename,
            columns=columns, positive_label=positive_label, summary=rows, operators=operators,
            courses=(ov.courses[:15] if ov else []), reasons=(ov.reasons[:20] if ov else []), days=days, leads=leads,
            tasks=task_lines, notes=notes_text,
        )
        return Export(period=period, report=report, summary=summary)

    async def _names(self):
        people = await self.reports.store.people()
        users = await self.reports.store.users()
        listed = {}
        for person in people:  # ro'yxatdagi ism (operator birinchi)
            if person.user_id is not None and person.name and (person.user_id not in listed or person.kind == "operator"):
                listed[person.user_id] = person.name

        def name(uid: int) -> str:
            if uid in listed:
                return listed[uid]
            username, first_name = users.get(uid, (None, ""))
            return first_name or (f"@{username}" if username else f"ID {uid}")
        return name

    def caption(self, export: Export, *, masked: bool) -> str:
        p, s = export.period, export.summary
        lines = [f"📊 <b>{p.title} · {html.escape(p.label)}</b>" + (" <i>(oraliq)</i>" if p.live else "")]
        head = f"📞 {s['calls']} ta lidga qo'ng'iroq"
        if s.get("positive_emoji"):
            head += f" · {s['positive_emoji']} {s['positive']} ({s['positive_pct']}%)"
        lines.append(head)
        lines.append(f"📥 Yangi lidlar: {s['new_leads']} · belgilanmagan: {s['unmarked']}")
        if s["top"]:
            lines.append("🏆 " + " · ".join(f"{html.escape(str(name))} {total}" for name, total, _ in s["top"][:3]))
        if s["tasks_due"]:
            lines.append(f"📋 Vazifalar: {s['tasks_due']} · bajarildi {s['tasks_done']}")
        lines.append("🔒 Telefon raqamlari yashirilgan" if masked else "🔓 To'liq nusxa — telefon raqamlari bilan")
        return "\n".join(lines)[:1000]

    async def files(self, export: Export, *masks: bool) -> list[bytes]:
        def build() -> list[bytes]:
            return [build_workbook(export.report, masked=masked) for masked in masks]
        return await asyncio.to_thread(build)

    async def send_file(self, bot: Bot, chat_id: int, export: Export, data: bytes, *, masked: bool):
        return await bot.send_document(chat_id, BufferedInputFile(data, filename=export.report.filename),
                                       caption=self.caption(export, masked=masked), parse_mode=ParseMode.HTML)

    async def send_to(self, bot: Bot, chat_id: int, kind: str, first: date, last: date, now: datetime,
                      *, masked: bool) -> Export:
        """Qo'lda so'ralgan Excel (/excel, Mini App): faqat so'ragan odamga."""
        export = await self.collect(kind, first, last, now)
        (data,) = await self.files(export, masked)
        await self.send_file(bot, chat_id, export, data, masked=masked)
        if not export.period.live:
            await self.store.save_export(export.period, export.summary, sent=False)
        return export

    async def deliver(self, bot: Bot, kind: str, first: date, last: date, now: datetime) -> int:
        """Avtomatik yuborish: rahbarlar guruhiga yashirilgan nusxa, egasiga to'liq. Yetkazilgan chatlar soni."""
        export = await self.collect(kind, first, last, now)
        groups = await self.reports.store.groups_with("leaders")
        full, masked = await self.files(export, False, True)
        delivered, failed = 0, []
        for group in groups:
            try:
                msg = await self.send_file(bot, group.chat_id, export, masked, masked=True)
                delivered += 1
            except TelegramAPIError as exc:
                log.warning("Excel guruhga yuborilmadi (%s): %s", group.chat_id, exc)
                failed.append(f"«{group.title or group.chat_id}»: {exc}")
                continue
            try:  # guruhdagi faylga reaksiya — qo'ng'iroq emas (eng yaxshi urinish)
                await self.reports.store.mark_seen(group.chat_id, msg.message_id)
            except Exception:  # noqa: BLE001
                log.exception("Excel xabarini belgilab bo'lmadi")
        for owner_id in self.reports.owners():
            try:
                await self.send_file(bot, owner_id, export, full, masked=False)
                delivered += 1
            except TelegramAPIError as exc:
                log.warning("Excel egasiga yuborilmadi (%s): %s", owner_id, exc)
        if delivered:
            await self.store.save_export(export.period, export.summary, sent=True)
            if failed:
                problem = html.escape("\n".join(failed))
                for owner_id in self.reports.owners():
                    try:
                        await bot.send_message(owner_id, f"⚠️ {export.period.title} rahbarlar guruhiga yuborilmadi:\n"
                                                         f"<i>{problem}</i>", parse_mode=ParseMode.HTML)
                    except TelegramAPIError:
                        pass
        return delivered

    async def check_exports(self, bot: Bot, now: datetime) -> int:
        s = await self.reports.schedule()
        done = 0
        for kind, enabled in ((WEEK, s.weekly_enabled), (MONTH, s.monthly_enabled)):
            if not enabled:
                continue
            first, last, send_day = due_period(kind, now.date())
            moment = _at(send_day, s.excel_time, self.tz)
            if now < moment or moment < await self.archive_since():
                continue
            key = period_key(kind, first)
            row = await self.store.export(key)
            if row is not None and row["sent_at"]:
                continue
            retry = self._export_retry.get(key)
            if retry is not None and now < retry:
                continue
            span = span_bounds(first, last, s, self.tz)
            if span is None or span[1] <= await self.reports.since() or not await self.reports.store.groups_with("leads"):
                continue  # kuzatishdan oldingi davr yoki lidlar guruhi yo'q
            try:
                delivered = await self.deliver(bot, kind, first, last, now)
            except Exception:  # noqa: BLE001 — bitta davrdagi xato boshqasini to'xtatmasin, keyinroq qayta
                log.exception("Excel hisobot tayyorlanmadi (%s)", key)
                delivered = 0
            if delivered:
                done += 1
                self._export_retry.pop(key, None)
            else:
                self._export_retry[key] = now + SEND_RETRY
        return done

    # -------------------------------------------------------------- Arxiv (Mini App)
    async def periods(self, kind: str, now: datetime) -> list[dict]:
        s = await self.reports.schedule()
        since = (await self.reports.since()).astimezone(self.tz).date()
        stored = await self.store.exports(kind)
        first, last = current_period(kind, now.date())
        items = []
        for _ in range(LIST_LIMIT[kind]):
            if last < since:
                break
            period = self.period(kind, first, last, s, now)
            row = stored.get(period.key)
            sent = self.local(row["sent_at"]) if row and row["sent_at"] else None
            summary = None
            if row and not period.live:
                try:
                    summary = json.loads(row["summary"]) or None
                except ValueError:
                    summary = None
            items.append({"key": period.key, "label": period.label, "live": period.live,
                          "sent": sent is not None, "sent_label": due_label(sent) if sent else None, "summary": summary})
            first, last = previous_period(kind, first)
        return items

    async def detail(self, kind: str, first: date, last: date, now: datetime) -> dict:
        s = await self.reports.schedule()
        period = self.period(kind, first, last, s, now)
        _, _, summary = await self.summarize(period, now, s)
        if not period.live:
            await self.store.save_export(period, summary, sent=False)
        row = await self.store.export(period.key)
        sent = self.local(row["sent_at"]) if row and row["sent_at"] else None
        return {"key": period.key, "kind": kind, "label": period.label, "title": period.title, "live": period.live,
                "sent": sent is not None, "sent_label": due_label(sent) if sent else None, "summary": summary,
                "filename": period.filename}

    def excel_keyboard(self, now: datetime) -> InlineKeyboardMarkup:
        today = now.date()
        week, month = current_period(WEEK, today), current_period(MONTH, today)
        last_week, last_month = previous_period(WEEK, week[0]), previous_period(MONTH, month[0])
        rows = [
            [InlineKeyboardButton(text=f"📅 O'tgan hafta · {last_week[0]:%d.%m}–{last_week[1]:%d.%m}",
                                  callback_data=f"xl:{period_key(WEEK, last_week[0])}"),
             InlineKeyboardButton(text="📅 Shu hafta", callback_data=f"xl:{period_key(WEEK, week[0])}")],
            [InlineKeyboardButton(text=f"🗓 {MONTHS[last_month[0].month - 1].capitalize()}",
                                  callback_data=f"xl:{period_key(MONTH, last_month[0])}"),
             InlineKeyboardButton(text=f"🗓 {MONTHS[month[0].month - 1].capitalize()} (shu oy)",
                                  callback_data=f"xl:{period_key(MONTH, month[0])}")],
        ]
        button = self.reports.app_button("arxiv", "📁 Arxiv — Mini App")
        if button is not None:
            rows.append([button])
        return InlineKeyboardMarkup(inline_keyboard=rows)


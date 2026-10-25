"""AI uchun hisobot vositalari (v1.10): «bugun nechta qo'ng'iroq bo'ldi?», «kim keladi?», «Malika vazifani
bajardimi?» kabi savollarga model bazadagi aniq ma'lumotni shu vositalar orqali oladi.

- Egasi: oddiy AI suhbatida (o'zi tanlagan model) — fayl vositalari bilan birga report_* vositalari ham bor.
- Rahbarlar: botga yozgan savoliga AI faqat hisobot va vazifalar bo'yicha javob beradi (boshqa vosita va internet
  yo'q), telefon raqamlari yashiriladi; har rahbarga kunlik savollar chegarasi (LEADER_DAILY_LIMIT).
- Raqamlar kunlik hisobot qoidasida (stats.overview_between): davr — hisobot davri (masalan, 19:00 → 19:00),
  bir davrda bitta lid bir operatorga bir marta sanaladi."""
from __future__ import annotations

import json
import re
import time as clock
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..prompt import WEEKDAYS, uz_date
from .excel import format_phone, hide_numbers, mask_phone
from .leads import message_link
from .stats import (
    OTHER,
    Overview,
    Schedule,
    build_overview,
    current_day,
    month_of,
    neighbour_day,
    overview_between,
    period_bounds,
    report_days,
    span_bounds,
    week_of,
)
from .store import iso
from .tasks import CANCELLED, DONE, EVENT_LABELS, OPEN, Task, due_label, repeat_label

if TYPE_CHECKING:
    from .service import ReportService

TOOL_NAMES = ("report_stats", "report_leads", "task_status")
PERIODS = ("today", "yesterday", "this_week", "last_week", "this_month", "last_month", "day", "range")
LEADS_LIMIT, LEADS_MAX = 30, 100
TASKS_LIMIT, TASKS_MAX = 20, 100
COMMENT_LIMIT = 200
MAX_DAYS = 400  # oraliq so'rovi: shundan uzun davr bo'lmaydi
ANALYZE_BATCHES = 2  # javobdan oldin tahlil kutayotgan izohlar (narxi juda kam)
_APOS = str.maketrans({c: "'" for c in "ʻʼ‘’`´"})
STATE_TEXT = {
    "waiting": "⏳ muddati kelmagan", "overdue": "🔴 muddati o'tdi", "asked": "🔔 so'raldi, javob kutilmoqda",
    "no": "❌ «Yo'q» dedi", "silent": "⚠️ javob bermadi", "done": "✅ bajarildi", "cancelled": "🗑 bekor qilingan",
}

REPORT_GUIDE = """Call center va vazifalar (ma'lumot botning bazasida, vositalar orqali olinadi):
- Qo'ng'iroqlar, operatorlar, lidlar natijasi yoki vazifalar haqida so'ralsa — javobdan oldin report_stats, report_leads yoki task_status vositasi bilan aniq ma'lumot ol. Taxmin qilma, suhbatdagi eski raqamni qayta ishlatma (ma'lumot doim yangilanib turadi).
- Davr aytilmasa — bugun (joriy hisobot davri). «Kecha», «shu hafta», «o'tgan oy», aniq sana yoki oraliq bo'lsa — mos period bilan. Hisobot davri kalendar kun emas (masalan, kecha 19:00 → bugun 19:00) — kerak bo'lsa javobda ayt.
- «Qo'ng'iroq» — operator status (reaksiya) qo'ygan yoki natija haqida izoh yozgan lid; bir davrda bitta lid bir operatorga bir marta sanaladi. «Asosiy natija» — 👍 keladi kabi asosiy status.
- Javob qisqa bo'lsin: avval asosiy raqam, keyin kerak bo'lsa ro'yxat. Ro'yxat uzun bo'lsa — eng muhimlari va «yana N ta». Statusni ma'nosi bilan yoz (👍 keladi).
- Vazifa holati: ✅ bajarildi (kim yopgani bilan), ❌ «Yo'q» dedi, 🔔 so'raldi — javob kutilmoqda, ⚠️ javob bermadi, 🔴 muddati o'tdi, ⏳ muddati kelmagan.
- Vosita «error» qaytarsa, sababini qisqa ayt; odam topilmasa, ro'yxatdagi ismlardan mosini taklif qil."""

LEADER_PROMPT = """Sen TTA o'quv markazining call center hisobotlari va vazifalari bo'yicha yordamchisan. Rahbar bilan gaplashyapsan.
Bugungi sana: {sana}.
- Faqat call center natijalari (qo'ng'iroqlar, operatorlar, lidlar va ularning javoblari) va vazifalar haqidagi savollarga javob ber. Boshqa mavzudagi so'rovlarga (xat, tarjima, umumiy savollar) qisqa va muloyim rad javob ber: «Men faqat hisobot va vazifalar bo'yicha yordam beraman».
- Rahbar qaysi tilda yozsa, o'sha tilda javob ber; o'zbekcha bo'lsa — lotin yozuvida.
- Telefon raqamlari yashirilgan (+998 90 ***-**-67). To'liq raqam so'ralsa — lid postidagi havola orqali guruhdan ko'rishni yoki bot egasidan so'rashni ayt.
- Sozlamalarni o'zgartira olmaysan, vazifa bera olmaysan — faqat ma'lumot berasan.
- Javoblar Telegram'da ko'rinadi: **qalin** va ro'yxatlar mumkin; keng jadval tuzma — telefonda ro'yxat qulayroq."""

_PERIOD_PROPS: dict[str, Any] = {
    "period": {
        "type": "string", "enum": list(PERIODS),
        "description": (
            "today — bugun (joriy hisobot davri, standart); yesterday — kechagi hisobot kuni; this_week / last_week — "
            "shu / o'tgan hafta (dushanba–yakshanba); this_month / last_month — shu / o'tgan oy; day — aniq sana (date); "
            "range — oraliq (date_from … date_to)"
        ),
    },
    "date": {"type": "string", "description": "period=day uchun sana: YYYY-MM-DD"},
    "date_from": {"type": "string", "description": "period=range: boshlanish sanasi YYYY-MM-DD"},
    "date_to": {"type": "string", "description": "period=range: tugash sanasi YYYY-MM-DD (shu kun ham kiradi)"},
}


class ReportToolError(Exception):
    """Model'ga «error» sifatida qaytadi — u foydalanuvchiga tushuntiradi."""


def _norm(text: str | None) -> str:
    return " ".join(str(text or "").translate(_APOS).lower().lstrip("@").split())


def _matches(query: str, *candidates: str | None) -> bool:
    q = _norm(query)
    return bool(q) and any(q == _norm(c) or q in _norm(c) for c in candidates if c)


def _date(value: Any, field: str) -> date:
    try:
        return datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        raise ReportToolError(f"{field}: sana YYYY-MM-DD ko'rinishida bo'lsin") from None


def _limit(value: Any, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


@dataclass
class Window:
    label: str
    start: datetime
    end: datetime
    days: list[date]  # davrdagi hisobot kunlari
    live: bool

    def as_dict(self, now: datetime) -> dict:
        data = {"label": self.label, "start": f"{self.start:%Y-%m-%d %H:%M}", "end": f"{self.end:%Y-%m-%d %H:%M}",
                "report_days": len(self.days), "live": self.live}
        if self.live:
            data["until_now"] = f"{now:%Y-%m-%d %H:%M}"
        return data


def resolve_window(args: dict, now: datetime, s: Schedule) -> Window:
    """Savoldagi davr → hisobot davri (boshlanish, tugash). «today» — bugungi hisobot kuni (19:00 dan keyin ham
    bugungisi, dam olish kuni — keyingi ish kunining davri)."""
    tz = now.tzinfo
    period = str(args.get("period") or "today")
    today = now.date() if now.weekday() in s.days else current_day(now, s)
    if period in ("today", "yesterday", "day"):
        if period == "today":
            day, name = today, "bugun"
        elif period == "yesterday":
            day, name = neighbour_day(today, s, -1), "kecha"
        else:
            asked = _date(args.get("date"), "date")
            if asked > now.date():
                raise ReportToolError("Bu sana hali kelmagan")
            day, name = (asked if asked.weekday() in s.days else neighbour_day(asked, s, 1)), "sana"
        start, end = period_bounds(day, s, tz)  # type: ignore[arg-type]
        label = f"{name}: {day:%d.%m.%Y} ({WEEKDAYS[day.weekday()]}) hisobot davri"
        return Window(label, start, end, [day], now < end)
    if period in ("this_week", "last_week"):
        first, last = week_of(now.date())
        if period == "last_week":
            first, last = week_of(first - timedelta(days=1))
        label = ("shu hafta" if period == "this_week" else "o'tgan hafta") + f": {first:%d.%m} – {last:%d.%m.%Y}"
    elif period in ("this_month", "last_month"):
        first, last = month_of(now.date())
        if period == "last_month":
            first, last = month_of(first - timedelta(days=1))
        label = ("shu oy" if period == "this_month" else "o'tgan oy") + f": {first:%d.%m} – {last:%d.%m.%Y}"
    elif period == "range":
        first, last = _date(args.get("date_from"), "date_from"), _date(args.get("date_to"), "date_to")
        if first > last:
            first, last = last, first
        if (last - first).days > MAX_DAYS:
            raise ReportToolError(f"Oraliq juda uzun ({MAX_DAYS} kungacha bo'lsin)")
        label = f"oraliq: {first:%d.%m.%Y} – {last:%d.%m.%Y}"
    else:
        raise ReportToolError(f"Noma'lum davr: {period}")
    span = span_bounds(first, last, s, tz)  # type: ignore[arg-type]
    if span is None:
        raise ReportToolError("Bu davrda hisobot kuni yo'q")
    return Window(label, span[0], span[1], report_days(first, last, s), now < span[1])


class ReportTools:
    """Bitta suhbat navbati uchun vositalar: masked=True — rahbar (telefon raqamlari yashiriladi)."""

    def __init__(self, reports: "ReportService", *, masked: bool) -> None:
        self.reports = reports
        self.masked = masked
        self.names = TOOL_NAMES

    # -------------------------------------------------------------- schema
    @staticmethod
    def specs() -> list[dict]:
        return [
            {
                "name": "report_stats",
                "description": (
                    "Call center natijalari davr bo'yicha: jami qo'ng'iroqlar (operator belgilagan lidlar), har operator "
                    "va har status (👍 keladi, 👎 kelmaydi, 🆒 ko'tarmadi …) bo'yicha sonlar, yangi va hech kim "
                    "belgilamagan lidlar, kurslar, rad sabablari. operator — bitta operator natijasi; by_day — hafta, oy "
                    "yoki oraliqni kunlarga bo'lib."
                ),
                "input_schema": {"type": "object", "properties": {
                    **_PERIOD_PROPS,
                    "operator": {"type": "string", "description": "Faqat shu operator: ismi yoki @username"},
                    "by_day": {"type": "boolean", "description": "Har hisobot kuni bo'yicha alohida sonlar ham kerakmi"},
                }},
            },
            {
                "name": "report_leads",
                "description": (
                    "Lidlar ro'yxati: davrda tushgan yoki belgilangan lidlar — vaqti, ismi, telefoni, yoshi, tumani, kursi, "
                    "so'nggi natijasi, kim belgilagani, so'nggi izohi, qayta qo'ng'iroq vaqti. Status, operator, kurs "
                    "yoki ism/telefon bo'yicha saralash mumkin («bugun kim keladi?», «Malika kimlarga qo'ng'iroq qildi?»)."
                ),
                "input_schema": {"type": "object", "properties": {
                    **_PERIOD_PROPS,
                    "status": {"type": "string", "description": (
                        "Natija: status emojisi yoki ma'nosi («👍», «keladi», «ko'tarmadi»); «belgilanmagan» — hech kim "
                        "belgilamagan lidlar")},
                    "operator": {"type": "string", "description": "Shu operator belgilagan lidlar (ismi yoki @username)"},
                    "course": {"type": "string", "description": "Kurs nomidan bir qism"},
                    "search": {"type": "string", "description": "Lid ismi yoki telefon raqamidan bir qism"},
                    "limit": {"type": "integer", "description": f"Nechta lid ko'rsatilsin (standart {LEADS_LIMIT}, "
                                                                f"ko'pi bilan {LEADS_MAX})"},
                }},
            },
            {
                "name": "task_status",
                "description": (
                    "Vazifalar: kimga nima berilgan, muddati, holati (muddati kelmagan, so'raldi, «Yo'q» dedi, javob "
                    "bermadi, muddati o'tdi, bajarildi, bekor), kim yopgani va «Bajardingizmi?» so'rovlari tarixi. "
                    "Ijrochi, holat yoki vazifa matni bo'yicha saralash mumkin («Malika vazifani bajardimi?»)."
                ),
                "input_schema": {"type": "object", "properties": {
                    "assignee": {"type": "string", "description": "Ijrochi: ismi yoki @username"},
                    "status": {"type": "string", "enum": ["all", "open", "overdue", "done", "cancelled"],
                               "description": "all — hammasi (standart, yangilari oldin); overdue — ochiq va muddati o'tgan"},
                    "search": {"type": "string", "description": "Vazifa matnidan bir qism"},
                    "limit": {"type": "integer", "description": f"Nechta (standart {TASKS_LIMIT}, ko'pi bilan {TASKS_MAX})"},
                }},
            },
        ]

    async def run(self, name: str, args: dict) -> str:
        handler = {"report_stats": self.stats, "report_leads": self.leads, "task_status": self.tasks}.get(name)
        if handler is None:
            raise ReportToolError(f"Noma'lum vosita: {name}")
        try:
            data = await handler(args)
        except ReportToolError as exc:
            data = {"error": str(exc)}
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    # -------------------------------------------------------------- yordamchilar
    def _hide(self, text: str) -> str:
        return hide_numbers(text) if self.masked else text

    async def _prepare(self, args: dict) -> tuple[Window, Schedule, datetime]:
        reports = self.reports
        await reports.ensure()
        s = await reports.schedule()
        now = reports.now()
        if not await reports.store.groups_with("leads"):
            raise ReportToolError("Lidlar guruhi hali ulanmagan — call center ma'lumoti yo'q")
        await reports.analyze_pending(ANALYZE_BATCHES)  # yangi izohlar ham hisobga kirsin
        return resolve_window(args, now, s), s, now

    @staticmethod
    def _labels(ov: Overview) -> dict[str, str]:
        labels = {st.emoji: st.label for st in ov.statuses}
        labels[OTHER] = "aniqlanmagan"
        return labels

    def _row(self, ov: Overview, row: Any, labels: dict[str, str]) -> dict:
        data = {"name": row.name, "calls": row.total,
                "by_status": {f"{e} {labels.get(e, '')}".strip(): n for e, n in row.counts.items() if n},
                "main_result": ov.positive(row)}
        if row.username:
            data["username"] = "@" + row.username
        return data

    # -------------------------------------------------------------- report_stats
    async def stats(self, args: dict) -> dict:
        window, s, now = await self._prepare(args)
        store = self.reports.store
        ov = await overview_between(store, s, window.start, window.end, now, window.days[-1] if window.days else None)
        labels = self._labels(ov)
        everyone = ov.rows + ov.extras
        calls = sum(r.total for r in everyone)
        main = sum(ov.positive(r) for r in everyone)
        counts: dict[str, int] = {}
        for row in everyone:
            for emoji, n in row.counts.items():
                key = f"{emoji} {labels.get(emoji, '')}".strip()
                counts[key] = counts.get(key, 0) + n
        data: dict[str, Any] = {
            "period": window.as_dict(now),
            "statuses": [{"emoji": st.emoji, "label": st.label, "main": st.positive} for st in ov.statuses],
            "totals": {"calls": calls, "by_status": counts, "main_result": main,
                       "main_share": f"{round(main * 100 / calls)}%" if calls else None},
            "operators": [self._row(ov, r, labels) for r in ov.rows],
            "not_in_operator_list": [self._row(ov, r, labels) for r in ov.extras],
            "operator_list_set": ov.has_operator_list,
            "leads": {"new": ov.new_leads, "unmarked": ov.unmarked},
            "courses": [{"course": self._hide(c), "marked_leads": t, "main_result": p} for c, t, p in ov.courses[:10]],
            "refusal_reasons": [{"reason": self._hide(r), "count": n} for r, n in ov.reasons[:10]],
            "callback_requests": ov.callbacks, "conflicts": ov.conflicts, "call_comments": ov.comments,
            "comments_waiting_analysis": ov.pending,
        }
        operator = str(args.get("operator") or "").strip()
        if operator:
            ranked = sorted(everyone, key=lambda r: (-r.total, r.name.lower()))
            found = [r for r in ranked if _matches(operator, r.name, r.username)]
            if not found:
                raise ReportToolError(
                    f"«{operator}» topilmadi. Bu davrdagi operatorlar: " + (", ".join(r.name for r in ranked) or "yo'q"))
            row = found[0]
            data = {"period": data["period"], "operator": self._row(ov, row, labels),
                    "rank": ranked.index(row) + 1, "operators_total": len([r for r in ranked if r.total]),
                    "all_calls": calls, "listed": row in ov.rows}
        if args.get("by_day") and len(window.days) > 1:
            days = []
            for day in window.days[:62]:
                start, _ = period_bounds(day, s, now.tzinfo)  # type: ignore[arg-type]
                if start >= now:
                    break
                day_ov = await build_overview(store, s, day, now)
                people = day_ov.rows + day_ov.extras
                if operator:
                    people = [r for r in people if _matches(operator, r.name, r.username)]
                days.append({"date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()],
                             "calls": sum(r.total for r in people), "main_result": sum(day_ov.positive(r) for r in people),
                             "new_leads": day_ov.new_leads, "unmarked": day_ov.unmarked})
            data["days"] = days
        return data

    # -------------------------------------------------------------- report_leads
    def _status_filter(self, raw: str, ov: Overview) -> set[str | None]:
        """«👍» / «keladi» → {"👍"}; «belgilanmagan» → {None} (hech kim belgilamagan)."""
        value = _norm(raw).replace("️", "")
        if not value:
            return set()
        if value in ("belgilanmagan", "unmarked", "yo'q", "hech kim", "belgisiz"):
            return {None}
        if value in ("aniqlanmagan", "❔"):
            return {OTHER}
        found: set[str | None] = set()
        for st in ov.statuses:
            if value == st.emoji.replace("️", "") or _matches(value, st.label):
                found.add(st.emoji)
        if not found:
            raise ReportToolError(f"«{raw}» degan status yo'q. Statuslar: "
                                  + ", ".join(f"{st.emoji} {st.label}" for st in ov.statuses))
        return found

    async def leads(self, args: dict) -> dict:
        window, s, now = await self._prepare(args)
        reports = self.reports
        store = reports.store
        ov = await overview_between(store, s, window.start, window.end, now, window.days[-1] if window.days else None)
        outcome = ov if ov.live else await overview_between(store, s, window.start, now, now)  # natija — hozirgi holat
        wanted = self._status_filter(str(args.get("status") or ""), ov)
        operator = str(args.get("operator") or "").strip()
        course = _norm(args.get("course"))
        search = _norm(args.get("search"))
        digits = re.sub(r"\D", "", search)
        new = await store.leads_between(iso(window.start), iso(min(window.end, now)))
        ids = set(ov.lead_status) | {lead.id for lead in new}
        leads = await store.leads_by_ids(ids)
        notes = await reports.archive.store.lead_notes(ids)
        name_of = await reports.archive._names()
        labels = self._labels(ov)
        rows = []
        for lead in sorted(leads.values(), key=lambda item: (item.created_at or "", item.id)):
            status = outcome.lead_status.get(lead.id)
            if wanted and status not in wanted:
                continue
            touched_by = ov.lead_people.get(lead.id, [])
            if operator and not any(_matches(operator, name_of(uid)) for uid in touched_by):
                continue
            if course and course not in _norm(lead.course):
                continue
            if search and not (search in _norm(lead.name) or (len(digits) >= 3 and digits in re.sub(r"\D", "", lead.phones))):
                continue
            note = notes.get(lead.id) or {}
            created = reports.tasks.local(lead.created_at)
            phones = [p for p in lead.phones.split(",") if p]
            item = {
                "time": f"{created:%d.%m %H:%M}" if created else None,
                "name": self._hide(lead.name) or ("(post mazmuni noma'lum)" if lead.source == "stub" else "(ism yo'q)"),
                "phone": ", ".join((mask_phone if self.masked else format_phone)(p) for p in phones) or None,
                "age": self._hide(lead.age) or None, "district": self._hide(lead.district) or None,
                "course": self._hide(lead.course) or None,
                "status": f"{status} {labels.get(status, '')}".strip() if status else "belgilanmagan",
                "marked_by": ", ".join(self._hide(name_of(uid)) for uid in outcome.lead_people.get(lead.id, [])) or None,
                "last_comment": self._hide(" ".join(str(note.get("text") or "").split()))[:COMMENT_LIMIT] or None,
                "callback": self._hide(note.get("callback") or "") or None,
                "reason": self._hide(note.get("reason") or "") or None,
                "link": message_link(lead.chat_id, lead.message_id),
            }
            rows.append({k: v for k, v in item.items() if v is not None})
        limit = _limit(args.get("limit"), LEADS_LIMIT, LEADS_MAX)
        data = {"period": window.as_dict(now), "total": len(rows), "shown": min(len(rows), limit), "leads": rows[:limit],
                "note": "status — lidning hozirgi (so'nggi) natijasi; marked_by — uni belgilagan operatorlar"}
        if self.masked:
            data["phones"] = "yashirilgan (rahbar uchun)"
        return data

    # -------------------------------------------------------------- task_status
    async def tasks(self, args: dict) -> dict:
        reports = self.reports
        await reports.ensure()
        service = reports.tasks
        store = service.store
        now = reports.now()
        status = str(args.get("status") or "all")
        if status in (OPEN, "overdue"):
            items = await store.tasks(OPEN, 1000)
        elif status in (DONE, CANCELLED):
            items = await store.tasks(status, 500)
        else:
            items = await store.tasks(None, 500)  # yangilari oldin
        assignee = str(args.get("assignee") or "").strip()
        search = _norm(args.get("search"))
        if assignee:
            people = {t.who for t in items}
            items = [t for t in items if _matches(assignee, t.who, t.assignee_name, t.assignee_username)]
            if not items:
                known = sorted(people) or sorted({t.who for t in await store.tasks(None, 300)})
                raise ReportToolError(f"«{assignee}» uchun vazifa topilmadi. Vazifasi bor odamlar: "
                                      + (", ".join(known[:30]) or "yo'q"))
        if search:
            items = [t for t in items if search in _norm(t.text)]
        if status == "overdue":
            items = [t for t in items if t.due_at and t.due_at < iso(now)]
        limit = _limit(args.get("limit"), TASKS_LIMIT, TASKS_MAX)
        owners = set(reports.owners())
        rows = [await self._task(t, now, owners) for t in items[:limit]]
        return {"now": f"{now:%Y-%m-%d %H:%M}", "counts_all": await store.task_counts(iso(now)),
                "total": len(items), "shown": len(rows), "tasks": rows}

    async def _task(self, task: Task, now: datetime, owners: set[int]) -> dict:
        service = self.reports.tasks
        due, created, closed = service.local(task.due_at), service.local(task.created_at), service.local(task.done_at)
        item: dict[str, Any] = {
            "who": task.who, "text": self._hide(task.text), "state": STATE_TEXT.get(service.state_of(task, now), task.status),
            "due": due_label(due) if due else None, "given": due_label(created) if created else None,
        }
        if task.assignee_username:
            item["username"] = "@" + task.assignee_username
        if task.status != OPEN and closed:
            item["closed"] = due_label(closed)
            if task.status == DONE:
                item["closed_by"] = ("ijrochi o'zi «Ha» bosdi" if task.done_by is not None and task.done_by == task.assignee_id
                                     else "egasi yopdi" if task.done_by in owners else None)
        if task.no_count:
            item["no_answers"] = task.no_count
        if task.repeat:
            item["repeat"] = repeat_label(task.repeat)
        history = await service.store.history(task.id)
        item["history"] = [
            f"{due_label(moment) if (moment := service.local(h['created_at'])) else ''} {EVENT_LABELS.get(h['kind'], h['kind'])}"
            .strip() for h in history[-8:]
        ]
        return {k: v for k, v in item.items() if v is not None}


class LeaderMemory:
    """Rahbar suhbatining qisqa xotirasi (oxirgi savol-javoblar, 3 soat) va kunlik savollar hisobi.

    Xotirada — faqat yakuniy matnlar (vosita natijalari emas): keyingi savolda model ma'lumotni qayta oladi."""

    KEEP = 8  # xabar (4 ta savol-javob)
    TTL = 3 * 3600.0

    def __init__(self) -> None:
        self._turns: dict[int, tuple[float, list[dict]]] = {}
        self._asked: dict[int, tuple[date, int]] = {}

    def history(self, uid: int) -> list[dict]:
        stamp, turns = self._turns.get(uid, (0.0, []))
        if clock.monotonic() - stamp > self.TTL:
            self._turns.pop(uid, None)
            return []
        return list(turns)

    def add(self, uid: int, question: str, answer: str) -> None:
        turns = self.history(uid) + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        self._turns[uid] = (clock.monotonic(), turns[-self.KEEP:])

    def allowed(self, uid: int, day: date, limit: int) -> bool:
        if limit <= 0:
            return True
        asked_day, count = self._asked.get(uid, (day, 0))
        return asked_day != day or count < limit

    def count(self, uid: int, day: date) -> None:
        asked_day, count = self._asked.get(uid, (day, 0))
        self._asked[uid] = (day, count + 1 if asked_day == day else 1)


def leader_system(now: datetime) -> str:
    return LEADER_PROMPT.replace("{sana}", uz_date(now)) + "\n\n" + REPORT_GUIDE

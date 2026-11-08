"""AI uchun hisobot vositalari (v1.10): «bugun nechta qo'ng'iroq bo'ldi?», «kim keladi?», «Malika vazifani
bajardimi?» kabi savollarga model bazadagi aniq ma'lumotni shu vositalar orqali oladi.

- Egasi: oddiy AI suhbatida (o'zi tanlagan model) — fayl vositalari bilan birga report_* vositalari ham bor.
- Rahbarlar: botga yozgan savoliga AI faqat hisobot va vazifalar bo'yicha javob beradi (boshqa vosita va internet
  yo'q), telefon raqamlari yashiriladi; har rahbarga kunlik savollar chegarasi (LEADER_DAILY_LIMIT).
- Raqamlar kunlik hisobot qoidasida (stats.overview_between): davr — hisobot davri (masalan, 19:00 → 19:00),
  bir davrda bitta lid bir operatorga bir marta sanaladi; calendar=true — kalendar kunlar (00:00–24:00).
- v1.13 (bot/reports/insights.py): report_breakdown (kesimlar), report_send (kunlik hisobot / Excel shu chatga);
  faqat egasiga — bot_status (bot holati) va group_messages (guruh yozishmalari)."""
from __future__ import annotations

import json
import re
import time as clock
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from ..prompt import WEEKDAYS, uz_date
from . import insights
from .excel import format_phone, hide_numbers, mask_phone
from .insights import InsightError
from .leads import message_link
from .stats import (
    OTHER,
    Overview,
    Schedule,
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

BASE_TOOLS = ("report_stats", "report_leads", "task_status")
SHARED_TOOLS = BASE_TOOLS + ("report_breakdown", "report_send")  # egasi va rahbar
OWNER_TOOLS = ("bot_status", "group_messages")  # v1.13: faqat egasi
TOOL_NAMES = SHARED_TOOLS + OWNER_TOOLS
STATUS_TEXT = {"report_send": "📤 Hisobot tayyorlanmoqda…", "bot_status": "⚙️ Bot holati tekshirilmoqda…",
               "group_messages": "💬 Guruh yozishmalari o'qilmoqda…"}
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
- Davr aytilmasa — bugun (joriy hisobot davri). «Kecha», «shu hafta», «o'tgan oy», aniq sana yoki oraliq bo'lsa — mos period bilan. Hisobot davri kalendar kun emas (masalan, kecha 19:00 → bugun 19:00), raqamlari kunlik hisobot bilan bir xil. Aniq sana yoki soat bo'yicha so'ralsa («5-noyabrda nechta lid tushdi?», «kecha kechqurun») — calendar=true (00:00–24:00). Javobda qaysi davr ekanini qisqa ayt.
- «Qo'ng'iroq» — operator status (reaksiya) qo'ygan yoki natija haqida izoh yozgan lid; bir davrda bitta lid bir operatorga bir marta sanaladi. «Asosiy natija» — 👍 keladi kabi asosiy status.
- Odamlar: operators — jadvaldagi operatorlar (position=administrator — administrator, operatorlar bilan birga sanaladi); leaders — lidni o'zi belgilagan rahbarlar; other_departments — boshqa bo'limlar xodimlari (position — bo'lim nomi, masalan Marketing); not_in_operator_list — ro'yxatda yo'qlar. Rahbar va bo'limlar ham sanaladi (alohida). Umumiy son — hammasining yig'indisi. accounts — bir odamning bir nechta akkaunti (bitta odam sifatida sanaladi).
- Kesimlar va tahlil (qaysi kurs, tuman, yosh yoki soatda lid ko'p va natija yaxshi; kunlar dinamikasi; lid tushgandan birinchi qo'ng'iroqqacha necha daqiqa; kim lidni birinchi olgan) — report_breakdown. Xulosa va tavsiyani raqamlarga tayanib ayt.
- Hisobot yoki fayl so'ralsa («kunlik hisobotni yubor», «o'tgan hafta Excel», «1–15 noyabr Excel») — report_send: kind=daily — kunlik hisobot (rasm), bitta kun; kind=excel — hafta, oy yoki oraliq. Fayl chatga o'zi boradi: javobda 1–2 jumla yetarli, raqamlarni qayta sanab o'tirma. Faqat raqam so'ralsa — fayl yuborma.
- Javob qisqa bo'lsin: avval asosiy raqam, keyin kerak bo'lsa ro'yxat. Ro'yxat uzun bo'lsa — eng muhimlari va «yana N ta». Statusni ma'nosi bilan yoz (👍 keladi).
- Vazifa holati: ✅ bajarildi (kim yopgani bilan), ❌ «Yo'q» dedi, 🔔 so'raldi — javob kutilmoqda, ⚠️ javob bermadi, 🔴 muddati o'tdi, ⏳ muddati kelmagan.
- Vosita «error» qaytarsa, sababini qisqa ayt; odam topilmasa, ro'yxatdagi ismlardan mosini taklif qil."""

OWNER_GUIDE = """Bot va guruhlar (faqat egasi uchun):
- Xarajat, foydalanuvchilar, xodimlar ro'yxati, guruhlar (bot admini, oxirgi faollik), sozlamalar (hisobot vaqti, modellar, zaxira), yuborilgan hisobotlar, bugungi faollik — bot_status (kerakli section bilan).
- Guruhlarda kim nima yozgani («rahbarlar guruhida bugun nima deyishdi?», «Malika kecha nima yozdi?», «kim narx haqida so'radi?») — group_messages. Avval qisqa xulosa, keyin muhim xabarlar vaqti va muallifi bilan. Botning o'z sozlamalarini o'zgartira olmaysan — buning uchun buyruqlar va Mini App bor."""

LEADER_PROMPT = """Sen TTA o'quv markazining call center hisobotlari va vazifalari bo'yicha yordamchisan. Rahbar bilan gaplashyapsan.
Bugungi sana: {sana}.
- Faqat call center natijalari (qo'ng'iroqlar, operatorlar, lidlar va ularning javoblari) va vazifalar haqidagi savollarga javob ber. Boshqa mavzudagi so'rovlarga (xat, tarjima, umumiy savollar) qisqa va muloyim rad javob ber: «Men faqat hisobot va vazifalar bo'yicha yordam beraman».
- Rahbar qaysi tilda yozsa, o'sha tilda javob ber; o'zbekcha bo'lsa — lotin yozuvida.
- Telefon raqamlari yashirilgan (+998 90 ***-**-67). To'liq raqam so'ralsa — lid postidagi havola orqali guruhdan ko'rishni yoki bot egasidan so'rashni ayt.
- Sozlamalarni o'zgartira olmaysan, vazifa bera olmaysan — faqat ma'lumot berasan. Kunlik hisobot yoki Excel so'ralsa — report_send bilan shu chatga yuborasan (telefon raqamlari yashirilgan nusxa).
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
    "calendar": {"type": "boolean", "description": (
        "true — kalendar kun(lar) 00:00–24:00 («3-noyabrda nechta lid tushdi?»); standart — hisobot davri "
        "(masalan, kecha 19:00 → bugun 19:00), kunlik hisobot raqamlari bilan bir xil")},
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


def _flag(value: Any) -> bool:
    """Model ba'zan mantiqiy qiymatni matn qilib yuboradi («false»)."""
    return value is True or str(value).strip().lower() in ("true", "1", "ha", "yes")


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
    first: date | None = None  # so'ralgan sanalar: hafta — dushanba…yakshanba, oy — 1…oxirgi kun
    last: date | None = None
    calendar: bool = False  # True — kalendar kunlar (00:00–24:00), aks holda hisobot davri

    def as_dict(self, now: datetime) -> dict:
        data = {"label": self.label, "start": f"{self.start:%Y-%m-%d %H:%M}", "end": f"{self.end:%Y-%m-%d %H:%M}",
                "basis": "kalendar kunlar (00:00–24:00)" if self.calendar else "hisobot davri",
                "report_days": len(self.days), "live": self.live}
        if self.live:
            data["until_now"] = f"{now:%Y-%m-%d %H:%M}"
        return data

    def day_spans(self, s: Schedule) -> list[tuple[date, datetime, datetime]]:
        """Kunlarga bo'lish: hisobot davrida — hisobot kunlari, kalendarda — har kalendar kun (00:00–24:00)."""
        if not self.calendar:
            return [(day, *period_bounds(day, s, self.start.tzinfo)) for day in self.days]  # type: ignore[arg-type]
        spans, day = [], self.first or self.start.date()
        while day <= (self.last or day):
            start = datetime.combine(day, time(0), tzinfo=self.start.tzinfo)
            spans.append((day, start, start + timedelta(days=1)))
            day += timedelta(days=1)
        return spans


def _requested(period: str, args: dict, now: datetime, s: Schedule) -> tuple[str, date, date, bool]:
    """Davr nomi → (yorliq boshi, birinchi sana, oxirgi sana, bitta kunmi). Bitta kun — hisobot kuni (dam olish
    kuni — keyingi ish kuni); calendar=true bo'lsa resolve_window sanani o'zgartirmaydi."""
    calendar = _flag(args.get("calendar"))
    today = now.date() if calendar or now.weekday() in s.days else current_day(now, s)
    if period == "today":
        return "bugun", today, today, True
    if period == "yesterday":
        day = today - timedelta(days=1) if calendar else neighbour_day(today, s, -1)
        return "kecha", day, day, True
    if period == "day":
        asked = _date(args.get("date"), "date")
        if asked > now.date():
            raise ReportToolError("Bu sana hali kelmagan")
        day = asked if calendar or asked.weekday() in s.days else neighbour_day(asked, s, 1)
        return "sana", day, day, True
    if period in ("this_week", "last_week"):
        first, last = week_of(now.date())
        if period == "last_week":
            first, last = week_of(first - timedelta(days=1))
        return ("shu hafta" if period == "this_week" else "o'tgan hafta"), first, last, False
    if period in ("this_month", "last_month"):
        first, last = month_of(now.date())
        if period == "last_month":
            first, last = month_of(first - timedelta(days=1))
        return ("shu oy" if period == "this_month" else "o'tgan oy"), first, last, False
    if period == "range":
        first, last = _date(args.get("date_from"), "date_from"), _date(args.get("date_to"), "date_to")
        if first > last:
            first, last = last, first
        if (last - first).days > MAX_DAYS:
            raise ReportToolError(f"Oraliq juda uzun ({MAX_DAYS} kungacha bo'lsin)")
        if first > now.date():
            raise ReportToolError("Bu sanalar hali kelmagan")
        return "oraliq", first, last, False
    raise ReportToolError(f"Noma'lum davr: {period}")


def resolve_window(args: dict, now: datetime, s: Schedule) -> Window:
    """Savoldagi davr → hisobot davri (boshlanish, tugash). «today» — bugungi hisobot kuni (19:00 dan keyin ham
    bugungisi, dam olish kuni — keyingi ish kunining davri). calendar=true — kalendar kunlar 00:00–24:00."""
    tz = now.tzinfo
    name, first, last, single = _requested(str(args.get("period") or "today"), args, now, s)
    if _flag(args.get("calendar")):
        start = datetime.combine(first, time(0), tzinfo=tz)
        end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz)
        label = (f"{name}: {first:%d.%m.%Y} ({WEEKDAYS[first.weekday()]}), kalendar kun 00:00–24:00" if single
                 else f"{name}: {first:%d.%m} – {last:%d.%m.%Y}, kalendar kunlar")
        return Window(label, start, end, report_days(first, last, s), now < end, first, last, calendar=True)
    if single:
        start, end = period_bounds(first, s, tz)  # type: ignore[arg-type]
        label = f"{name}: {first:%d.%m.%Y} ({WEEKDAYS[first.weekday()]}) hisobot davri"
        return Window(label, start, end, [first], now < end, first, first)
    span = span_bounds(first, last, s, tz)  # type: ignore[arg-type]
    if span is None:
        raise ReportToolError("Bu davrda hisobot kuni yo'q")
    label = f"{name}: {first:%d.%m} – {last:%d.%m.%Y}" if name != "oraliq" else f"oraliq: {first:%d.%m.%Y} – {last:%d.%m.%Y}"
    return Window(label, span[0], span[1], report_days(first, last, s), now < span[1], first, last)


class ReportTools:
    """Bitta suhbat navbati uchun vositalar: masked=True — rahbar (telefon raqamlari yashiriladi, bot holati va
    guruh yozishmalari yo'q). report_send uchun bind() — qaysi chatga yuborish."""

    def __init__(self, reports: "ReportService", *, masked: bool) -> None:
        self.reports = reports
        self.masked = masked
        self.names = SHARED_TOOLS if masked else TOOL_NAMES
        self.target: tuple[Any, int, int] | None = None  # (bot, chat_id, so'ragan odam)
        self.sent = 0  # shu navbatda yuborilgan hisobot / fayllar

    def bind(self, bot: Any, chat_id: int, user_id: int) -> "ReportTools":
        self.target = (bot, chat_id, user_id)
        return self

    def guide(self) -> str:
        """Tizim ko'rsatmasiga qo'shiladigan qism (o'zgarmas matn — kesh ishlayveradi)."""
        return REPORT_GUIDE if self.masked else REPORT_GUIDE + "\n\n" + OWNER_GUIDE

    @staticmethod
    def status_text(name: str) -> str:
        return STATUS_TEXT.get(name, "📊 Hisobot ma'lumoti olinmoqda…")

    # -------------------------------------------------------------- schema
    def specs(self) -> list[dict]:
        specs = self._base_specs() + insights.shared_specs(_PERIOD_PROPS, masked=self.masked)
        if not self.masked:
            specs += insights.owner_specs(_PERIOD_PROPS)
        return [spec for spec in specs if spec["name"] in self.names]

    @staticmethod
    def _base_specs() -> list[dict]:
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
        handlers = {"report_stats": self.stats, "report_leads": self.leads, "task_status": self.tasks,
                    "report_breakdown": self.breakdown, "report_send": self.send, "bot_status": self.bot_status,
                    "group_messages": self.group_messages}
        handler = handlers.get(name) if name in self.names else None
        if handler is None:
            raise ReportToolError(f"Noma'lum vosita: {name}")
        try:
            data = await handler(args)
        except (ReportToolError, InsightError) as exc:
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
        data = {"name": self._hide(row.name), "calls": row.total,
                "by_status": {f"{e} {labels.get(e, '')}".strip(): n for e, n in row.counts.items() if n},
                "main_result": ov.positive(row)}
        if row.username:
            data["username"] = "@" + self._hide(row.username)
        if row.admin or row.leader or row.staff:
            data["position"] = "administrator" if row.admin else "rahbar" if row.leader else self._hide(row.group)
        if len(row.usernames) > 1:  # bir odamning bir nechta akkaunti (shaxsiy va ish) — bitta qator
            data["accounts"] = ["@" + self._hide(u) for u in row.usernames]
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
            "leaders": [self._row(ov, r, labels) for r in ov.leaders],
            "other_departments": [self._row(ov, r, labels) for _, members in ov.departments for r in members],
            "not_in_operator_list": [self._row(ov, r, labels) for r in ov.unlisted],
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
                raise ReportToolError(f"«{operator}» topilmadi. Bu davrdagi operatorlar: "
                                      + (", ".join(self._hide(r.name) for r in ranked) or "yo'q"))
            row = found[0]
            data = {"period": data["period"], "operator": self._row(ov, row, labels),
                    "rank": ranked.index(row) + 1, "operators_total": len([r for r in ranked if r.total]),
                    "all_calls": calls, "listed": row in ov.rows}
        spans = window.day_spans(s) if _flag(args.get("by_day")) else []
        if len(spans) > 1:
            days = []
            for day, start, end in spans[:62]:
                if start >= now:
                    break
                day_ov = await overview_between(store, s, start, end, now, day)
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
        # Raqam bo'yicha qidiruv — faqat egasiga: rahbar raqam bo'laklarini so'rab, yashirilgan raqamni tiklay olmasin
        digits = "" if self.masked else re.sub(r"\D", "", search)
        new = await store.leads_between(iso(window.start), iso(min(window.end, now)))
        ids = set(ov.lead_status) | {lead.id for lead in new}
        leads = await store.leads_by_ids(ids)
        notes = await reports.archive.store.lead_notes(ids)
        name_of = await reports.archive._names()
        labels = self._labels(ov)
        matching: set[int] | None = None
        if operator:  # ism yoki @username bo'yicha; bu davrda lid belgilaganlar orasidan
            users = await store.users()
            listed = {p.user_id: p.username for p in await store.people() if p.user_id is not None and p.username}
            touched_all = {uid for people in ov.lead_people.values() for uid in people}
            matching = {uid for uid in touched_all
                        if _matches(operator, name_of(uid), listed.get(uid), users.get(uid, (None, ""))[0])}
            if not matching:
                known = sorted({self._hide(name_of(uid)) for uid in touched_all})
                raise ReportToolError(f"«{operator}» bu davrda lid belgilamagan. Belgilaganlar: "
                                      + (", ".join(known[:30]) or "yo'q"))
        rows = []
        for lead in sorted(leads.values(), key=lambda item: (item.created_at or "", item.id)):
            status = outcome.lead_status.get(lead.id)
            if wanted and status not in wanted:
                continue
            touched_by = ov.lead_people.get(lead.id, [])
            if matching is not None and not matching.intersection(touched_by):
                continue
            if course and course not in _norm(lead.course):
                continue
            # rahbarga: ism ham raqamsiz ko'rinishda solishtiriladi (ism ichidagi raqam bo'laklari bilan taxmin qilib bo'lmasin)
            if search and not (search in _norm(self._hide(lead.name))
                               or (len(digits) >= 3 and digits in re.sub(r"\D", "", lead.phones))):
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
        shown = rows[-limit:]  # ko'p bo'lsa — eng so'nggilari (vaqt tartibida)
        data = {"period": window.as_dict(now), "total": len(rows), "shown": len(shown), "leads": shown,
                "note": "status — lidning hozirgi (so'nggi) natijasi; marked_by — uni belgilagan operatorlar"
                        + ("; ro'yxat uzun — eng so'nggi lidlar ko'rsatildi" if len(rows) > limit else "")}
        if self.masked:
            data["phones"] = "yashirilgan (rahbar uchun)"
            if re.search(r"\d{3}", search):
                data["search_note"] = "telefon raqami bo'yicha qidiruv rahbar uchun yopiq — ism bo'yicha qidiring"
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
                                      + (", ".join(self._hide(k) for k in known[:30]) or "yo'q"))
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
            "who": self._hide(task.who), "text": self._hide(task.text),
            "state": STATE_TEXT.get(service.state_of(task, now), task.status),
            "due": due_label(due) if due else None, "given": due_label(created) if created else None,
        }
        if task.assignee_username:
            item["username"] = "@" + self._hide(task.assignee_username)
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

    # -------------------------------------------------------------- v1.13: kesimlar
    async def breakdown(self, args: dict) -> dict:
        window, s, now = await self._prepare(args)
        name_of = await self.reports.archive._names()
        return await insights.breakdown(self.reports, window, s, now, args, masked=self.masked, name_of=name_of)

    # -------------------------------------------------------------- v1.13: hisobot / Excel'ni chatga yuborish
    async def send(self, args: dict) -> dict:
        kind = str(args.get("kind") or "")
        if kind not in ("daily", "excel"):
            raise ReportToolError("kind: daily (kunlik hisobot) yoki excel (Excel fayl)")
        if self.target is None:
            raise ReportToolError("Bu suhbatda fayl yuborib bo'lmaydi")
        if self.sent >= insights.SEND_LIMIT:
            raise ReportToolError(f"Bir javobda {insights.SEND_LIMIT} tadan ko'p hisobot yuborilmaydi")
        if kind == "daily":  # kunlik hisobot — doim hisobot kuni bo'yicha (kalendar kun emas)
            args = {**args, "calendar": False}
        window, s, now = await self._prepare(args)
        bot, chat_id, user_id = self.target
        key = f"{kind}:{window.start:%Y-%m-%d %H:%M}:{window.end:%Y-%m-%d %H:%M}"
        if not self.reports.claim_send(user_id, key):
            raise ReportToolError("Bu hisobot hozirgina yuborildi — chatda turibdi")
        self.sent += 1
        try:
            if kind == "daily":
                return await insights.send_daily(self.reports, bot, chat_id, window, now)
            return await insights.send_excel(self.reports, bot, chat_id, window, s, now, masked=self.masked)
        except BaseException:
            self.sent -= 1
            self.reports.release_send(user_id, key)
            raise

    # -------------------------------------------------------------- v1.13: faqat egasiga
    async def bot_status(self, args: dict) -> dict:
        await self.reports.ensure()
        return await insights.bot_status(self.reports, args, self.reports.now())

    async def group_messages(self, args: dict) -> dict:
        reports = self.reports
        await reports.ensure()
        now = reports.now()
        window = resolve_window({**args, "calendar": True}, now, await reports.schedule())  # yozishma — kalendar kun
        return await insights.group_messages(reports, window, args, now)


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

    def refund(self, uid: int, day: date) -> None:
        """Javob chiqmagan savol (xato) hisobdan qaytariladi."""
        asked_day, count = self._asked.get(uid, (day, 0))
        if asked_day == day and count > 0:
            self._asked[uid] = (day, count - 1)


def leader_system(now: datetime) -> str:
    return LEADER_PROMPT.replace("{sana}", uz_date(now)) + "\n\n" + REPORT_GUIDE

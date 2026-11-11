"""AI uchun qo'shimcha vositalar (v1.13): kunlik hisobot va Excel'ni chatga yuborish, lidlar kesimlari, bot holati va
guruh yozishmalari. Raqamlar kod bilan hisoblanadi (model faqat tushuntiradi); rahbarga (masked) telefon raqamlari
yashiriladi, bot holati va yozishmalar — faqat egasiga (bot/reports/assistant.py → ReportTools)."""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from ..models import label
from ..prompt import MONTHS, WEEKDAYS
from ..scheduler import BACKUP_STAMP_KEY, UPDATE_CHECK_KEY, schedule_label
from .archive import MONTH, RANGE, WEEK
from .excel import hide_numbers
from .render import split_message
from .stats import OTHER, Schedule, _course_key, current_day, in_work_hours, month_of, overview_between, report_days, week_of
from .store import CHAT_KEEP_DAYS, iso

if TYPE_CHECKING:
    from .assistant import Window
    from .service import ReportService

log = logging.getLogger(__name__)

SEND_LIMIT = 3  # bir javobda ko'pi bilan shuncha hisobot / fayl yuboriladi
REPEAT_GAP = 60.0  # soniya: aynan shu hisobot shu odamga qayta yuborilmaydi (model takrorlasa)
EXCEL_MAX_DAYS = 100
GROUP_BY = ("day", "hour", "weekday", "course", "district", "age", "operator", "status")
GROUPS_SHOWN = 40
MESSAGES_LIMIT, MESSAGES_MAX = 60, 200
MESSAGE_SHOWN = 500  # bitta xabardan model ko'radigan belgilar
REPLY_SHOWN = 80
SECTIONS = ("all", "activity", "costs", "users", "staff", "groups", "settings", "deliveries")
AGE_BUCKETS = ((0, 17, "17 va kichik"), (18, 24, "18–24"), (25, 34, "25–34"), (35, 44, "35–44"), (45, 99, "45+"))
UNKNOWN = "noma'lum"
ROLE_WORDS = {"leads": ("lid", "lidlar", "leads", "izoh"), "leaders": ("rahbar", "rahbarlar", "leaders", "direktor"),
              "tasks": ("vazifa", "vazifalar", "tasks"), "vouchers": ("vaucher", "vaucherlar", "voucher", "vouchers")}
ROLE_NAMES = {"leads": "lidlar", "leaders": "rahbarlar", "tasks": "vazifalar", "vouchers": "vaucherlar"}
DISTRICT_NOISE = {"tumani", "tuman", "t", "sh", "shahri", "shahar", "r-n", "rayon", "район", "district", "viloyati"}
_APOS = str.maketrans({c: "'" for c in "ʻʼ‘’`´"})


class InsightError(Exception):
    """Model'ga «error» sifatida qaytadi — u foydalanuvchiga tushuntiradi."""


def _local(value: str | None, tz: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(tz)
    except ValueError:
        return None


def _stamp(moment: datetime | None) -> str | None:
    return f"{moment:%Y-%m-%d %H:%M}" if moment is not None else None


def _norm(text: Any) -> str:
    return " ".join(str(text or "").translate(_APOS).lower().lstrip("@").split())


def _share(part: int, whole: int) -> str | None:
    return f"{round(part * 100 / whole)}%" if whole else None


def district_key(text: str) -> str:
    words = [w.strip(".,;:()") for w in _norm(text).split()]
    words = [w for w in words if w and w not in DISTRICT_NOISE]
    return " ".join(words[:2]).capitalize() if words else "ko'rsatilmagan"


def age_key(text: str, year: int) -> str:
    match = re.search(r"\d{1,4}", text or "")
    if match is None:
        return UNKNOWN
    value = int(match.group(0))
    if 1930 <= value <= year:  # tug'ilgan yili yozilgan
        value = year - value
    return next((name for low, high, name in AGE_BUCKETS if low <= value <= high), UNKNOWN)


# ---------------------------------------------------------------------- vositalar sxemasi
def shared_specs(period_props: dict, *, masked: bool) -> list[dict]:
    """Egasi va rahbar uchun: report_breakdown, report_send."""
    copy = " Rahbarga — telefon raqamlari yashirilgan nusxa." if masked else " Egasiga — to'liq nusxa (telefonlar bilan)."
    no_calendar = {k: v for k, v in period_props.items() if k != "calendar"}
    return [
        {
            "name": "report_breakdown",
            "description": (
                "Lidlar tahlili kesimlar bo'yicha (davrda tushgan lidlar va ularning hozirgi holati): soni, "
                "belgilangani, asosiy natija (👍 keladi) va ulushi, belgilanmagani, lid tushgandan birinchi "
                "qo'ng'iroqqacha daqiqa (mediana). by: day — kunlar, hour — soatlar, weekday — hafta kunlari, course — "
                "kurslar, district — tumanlar, age — yosh guruhlari, operator — lidni birinchi olgan odam (+ davrdagi "
                "barcha qo'ng'iroqlari), status — hozirgi natija. «Qaysi kursdan lid ko'p?», «qaysi soatda lid ko'p "
                "tushadi?», «operatorlar lidga qancha vaqtda qo'ng'iroq qiladi?», «oy bo'yicha dinamika» uchun."
            ),
            "input_schema": {"type": "object", "properties": {
                **period_props,
                "by": {"type": "string", "enum": list(GROUP_BY), "description": "Kesim (standart: day)"},
                "course": {"type": "string", "description": "Faqat shu kurs lidlari (nomidan bir qism)"},
                "district": {"type": "string", "description": "Faqat shu tuman lidlari (nomidan bir qism)"},
            }},
        },
        {
            "name": "report_send",
            "description": (
                "Hisobotni shu chatga yuboradi. kind=daily — kunlik hisobot (rasm + qisqa matn, rahbarlar guruhiga "
                "boradigani kabi) bitta hisobot kuni uchun: period today / yesterday / day. kind=excel — Excel fayl "
                "(Umumiy, Kunlar, Lidlar, Vazifalar varaqlari): shu / o'tgan hafta yoki oy, bitta kun yoki oraliq "
                f"({EXCEL_MAX_DAYS} kungacha), hisobot davrlari bo'yicha. Foydalanuvchi hisobot yoki faylni so'rasa "
                f"ishlat.{copy}"
            ),
            "input_schema": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["daily", "excel"],
                         "description": "daily — kunlik hisobot (rasm); excel — Excel fayl"},
                **no_calendar,
            }, "required": ["kind"]},
        },
    ]


def owner_specs(period_props: dict) -> list[dict]:
    """Faqat egasi uchun: bot_status, group_messages."""
    no_calendar = {k: v for k, v in period_props.items() if k != "calendar"}
    return [
        {
            "name": "bot_status",
            "description": (
                "Bot holati: activity — bugungi faollik (lidlar, reaksiyalar, izohlar, guruh yozishmalari, tahlil "
                "navbati, vazifalar); costs — Claude xarajati (bugun, joriy kredit davri va byudjet, jami; "
                "foydalanuvchilar va modellar bo'yicha); users — botdan parol bilan foydalanuvchilar; staff — xodimlar "
                "ro'yxati (operator, administrator, rahbar, bo'limlar, istisnolar); groups — ulangan guruhlar (vazifasi, "
                "bot adminmi, oxirgi faollik, yozishmalar soni); settings — versiya, hisobot jadvali, statuslar, "
                "modellar, zaxira; deliveries — yuborilgan kunlik hisobotlar, Excel'lar, kundaliklar, so'nggi zaxira."
            ),
            "input_schema": {"type": "object", "properties": {
                "section": {"type": "string", "enum": list(SECTIONS), "description": "Qaysi qism (standart: all — hammasi)"},
            }},
        },
        {
            "name": "group_messages",
            "description": (
                "Ulangan guruhlardagi yozishmalar: vaqti, guruhi, muallifi, matni (rasm, ovozli xabar, fayl — belgi "
                f"bilan), kimga javob yozgani. {CHAT_KEEP_DAYS} kungacha saqlanadi; davr — kalendar kunlar (standart: "
                f"bugun). Ko'p bo'lsa eng so'nggi {MESSAGES_LIMIT} tasi — aniqroq uchun group, author yoki search ber."
            ),
            "input_schema": {"type": "object", "properties": {
                **no_calendar,
                "group": {"type": "string", "description": "Guruh nomidan bir qism yoki vazifasi: lidlar / rahbarlar / vazifalar"},
                "author": {"type": "string", "description": "Muallif: ismi yoki @username"},
                "search": {"type": "string", "description": "Matndan bir qism (so'z yoki ibora)"},
                "limit": {"type": "integer",
                          "description": f"Nechta xabar (standart {MESSAGES_LIMIT}, ko'pi bilan {MESSAGES_MAX})"},
            }},
        },
    ]


# ---------------------------------------------------------------------- hisobot va Excel yuborish
async def send_daily(service: "ReportService", bot: Any, chat_id: int, window: "Window", now: datetime) -> dict:
    """Kunlik hisobot (rahbarlar guruhiga boradigani kabi: rasm + qisqa matn yoki matn) — faqat so'ragan odamga."""
    if len(window.days) != 1:
        raise InsightError("Kunlik hisobot bitta kun uchun (today, yesterday yoki day). Hafta, oy yoki oraliq — "
                           "kind=excel")
    day = window.days[0]
    overview, text, reasons = await service.compose(day, now, interim=window.live)
    card = await service.card(overview, reasons, interim=window.live, now=now)
    await service.refresh_bot_info(bot)
    markup = service.details_markup(None if overview.live else day)
    picture = False
    if card is not None:
        try:
            await service.send_card(bot, chat_id, card, markup)
            picture = True
        except TelegramBadRequest as exc:  # rasm o'tmasa — matn
            log.warning("Hisobot rasmi yuborilmadi (%s): %s", chat_id, exc)
    if not picture:
        parts = split_message(text)
        for index, part in enumerate(parts):
            await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML,
                                   reply_markup=markup if index == len(parts) - 1 else None)
    everyone = overview.rows + overview.extras
    return {"sent": "kunlik hisobot" + (" (rasm)" if picture else " (matn)"), "report_day": day.isoformat(),
            "period": f"{overview.start:%d.%m %H:%M} → {overview.end:%d.%m %H:%M}", "interim": overview.live,
            "calls": sum(r.total for r in everyone), "new_leads": overview.new_leads, "unmarked": overview.unmarked,
            "note": "Hisobot chatga yuborildi — javobda 1–2 jumla yetarli, raqamlarni qayta sanab o'tirma."}


def excel_period(window: "Window") -> tuple[str, date, date]:
    """Davr → Excel turi: to'liq hafta — haftalik, to'liq oy — oylik, qolgani — oraliq (bitta kun ham)."""
    first, last = window.first, window.last
    if first is None or last is None:
        if not window.days:
            raise InsightError("Bu davrda hisobot kuni yo'q")
        first, last = window.days[0], window.days[-1]
    if week_of(first) == (first, last):
        return WEEK, first, last
    if month_of(first) == (first, last):
        return MONTH, first, last
    return RANGE, first, last


async def send_excel(service: "ReportService", bot: Any, chat_id: int, window: "Window", s: Schedule, now: datetime,
                     *, masked: bool) -> dict:
    kind, first, last = excel_period(window)
    if (last - first).days >= EXCEL_MAX_DAYS:
        raise InsightError(f"Excel uchun davr {EXCEL_MAX_DAYS} kungacha bo'lsin — bo'lib so'rang (masalan, oyma-oy)")
    if not report_days(first, last, s):
        raise InsightError("Bu davrda hisobot kuni yo'q — Excel bo'sh bo'lardi")
    export = await service.archive.send_to(bot, chat_id, kind, first, last, now, masked=masked)
    summary = export.summary
    return {"sent": "Excel", "file": export.report.filename, "period": export.period.label,
            "calls": summary.get("calls"), "main_result": summary.get("positive"), "new_leads": summary.get("new_leads"),
            "phones": "yashirilgan (rahbar nusxasi)" if masked else "to'liq",
            "note": "Fayl chatga yuborildi — javobda 1–2 jumla yetarli."}


# ---------------------------------------------------------------------- lidlar kesimlari
async def first_marks(service: "ReportService", lead_ids: list[int]) -> dict[int, tuple[datetime, int]]:
    """Har lidning birinchi belgisi (status reaksiyasi yoki qo'ng'iroq izohi): (vaqt, odam). Istisnolar sanalmaydi."""
    store = service.store
    excluded = {p.user_id for p in await store.people("excluded") if p.user_id is not None}
    result: dict[int, tuple[datetime, int]] = {}
    for i in range(0, len(lead_ids), 400):
        chunk = lead_ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        rows = await store._all(
            f"SELECT lead_id, user_id, created_at FROM rp_events WHERE lead_id IN ({marks})"
            " AND ((kind = 'reaction' AND status IS NOT NULL) OR (kind = 'comment' AND is_call = 1))"
            " ORDER BY created_at, id", chunk)
        for row in rows:
            if row["lead_id"] in result or row["user_id"] in excluded:
                continue
            moment = _local(row["created_at"], service.tz)
            if moment is not None:
                result[row["lead_id"]] = (moment, row["user_id"])
    return result


def _minutes(values: list[float]) -> int | None:
    return round(statistics.median(values)) if values else None


async def breakdown(service: "ReportService", window: "Window", s: Schedule, now: datetime, args: dict, *,
                    masked: bool, name_of: Callable[[int], str]) -> dict:
    """Davrda TUSHGAN lidlar (kogorta) tanlangan kesimda va ularning hozirgi natijasi."""
    by = str(args.get("by") or "day")
    if by not in GROUP_BY:
        raise InsightError(f"by: {', '.join(GROUP_BY)} dan biri")
    store, tz = service.store, service.tz
    hide: Callable[[str], str] = hide_numbers if masked else str
    leads = await store.leads_between(iso(window.start), iso(min(window.end, now)))
    course, district = _norm(args.get("course")), _norm(args.get("district"))
    if course:
        leads = [lead for lead in leads if course in _norm(lead.course)]
    if district:
        leads = [lead for lead in leads if district in _norm(lead.district)]
    ov = await overview_between(store, s, window.start, window.end, now, window.days[-1] if window.days else None)
    outcome = ov if ov.live else await overview_between(store, s, window.start, now, now)  # natija — hozirgi holat
    positive = {st.emoji for st in ov.statuses if st.positive}
    labels = {st.emoji: st.label for st in ov.statuses}
    labels[OTHER] = "natijasi aniqlanmagan"
    marks = await first_marks(service, [lead.id for lead in leads])

    def key_of(lead: Any) -> str:
        created = _local(lead.created_at, tz)
        if by in ("day", "hour", "weekday") and created is None:
            return UNKNOWN
        if by == "day":  # kalendar kun yoki hisobot kuni (davr turiga qarab)
            day = created.date() if window.calendar else current_day(created, s)  # type: ignore[union-attr]
            return f"{day:%Y-%m-%d} ({WEEKDAYS[day.weekday()]})"
        if by == "hour":
            return f"{created.hour:02d}:00–{(created.hour + 1) % 24:02d}:00"  # type: ignore[union-attr]
        if by == "weekday":
            return WEEKDAYS[created.weekday()]  # type: ignore[union-attr]
        if by == "course":
            return _course_key(lead.course)
        if by == "district":
            return district_key(lead.district)
        if by == "age":
            return age_key(lead.age, now.year)
        if by == "status":
            value = outcome.lead_status.get(lead.id)
            return f"{value} {labels.get(value, '')}".strip() if value else "belgilanmagan"
        mark = marks.get(lead.id)
        return name_of(mark[1]) if mark else "belgilanmagan"

    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"leads": 0, "marked": 0, "main": 0, "work": [], "all": []})
    for lead in leads:
        bucket = groups[key_of(lead)]
        bucket["leads"] += 1
        status = outcome.lead_status.get(lead.id)
        bucket["marked"] += bool(status)
        bucket["main"] += status in positive
        mark, created = marks.get(lead.id), _local(lead.created_at, tz)
        if mark and created:
            minutes = max(0.0, (mark[0] - created).total_seconds() / 60)
            bucket["all"].append(minutes)
            if in_work_hours(created, s):
                bucket["work"].append(minutes)

    rows = []
    for key, data in groups.items():
        row: dict[str, Any] = {"group": hide(key), "new_leads": data["leads"], "marked": data["marked"],
                               "main_result": data["main"], "main_share": _share(data["main"], data["leads"]),
                               "unmarked": data["leads"] - data["marked"]}
        if data["work"]:
            row["first_call_min"] = _minutes(data["work"])
        rows.append({k: v for k, v in row.items() if v is not None})
    order = {"weekday": list(WEEKDAYS), "age": [name for _, _, name in AGE_BUCKETS] + [UNKNOWN]}.get(by)
    if order is not None:
        rows.sort(key=lambda r: order.index(r["group"]) if r["group"] in order else len(order))
    elif by in ("day", "hour"):
        rows.sort(key=lambda r: r["group"])
    else:
        rows.sort(key=lambda r: (-r["new_leads"], r["group"]))
    work = [m for data in groups.values() for m in data["work"]]
    every = [m for data in groups.values() for m in data["all"]]
    marked, main = sum(r["marked"] for r in rows), sum(r["main_result"] for r in rows)
    result: dict[str, Any] = {
        "period": window.as_dict(now), "by": by,
        "totals": {"new_leads": len(leads), "marked": marked, "main_result": main, "main_share": _share(main, len(leads)),
                   "unmarked": len(leads) - marked, "first_call_min_work_hours": _minutes(work),
                   "first_call_min_all": _minutes(every)},
        "groups": rows[:GROUPS_SHOWN],
        "note": ("Davrda tushgan lidlar va ularning hozirgi natijasi. main_result — asosiy natija ("
                 + (" / ".join(f"{st.emoji} {st.label}" for st in ov.statuses if st.positive) or "belgilanmagan")
                 + "). first_call_min — ish vaqtida (" + f"{s.work_start}–{s.work_end}"
                 + ") tushgan lidga birinchi belgigacha daqiqa (mediana); first_call_min_all — kechasi tushganlar ham."),
    }
    if course or district:
        result["filter"] = {k: v for k, v in (("course", args.get("course")), ("district", args.get("district"))) if v}
    if len(rows) > GROUPS_SHOWN:
        result["more_groups"] = len(rows) - GROUPS_SHOWN
    if by == "operator":  # davrdagi faollik: kim nechta lidga qo'ng'iroq qildi (oldingi kunlarda tushganlar ham)
        result["calls_in_period"] = [
            {"name": hide(r.name), "calls": r.total, "main_result": ov.positive(r)}
            for r in sorted(ov.rows + ov.extras, key=lambda r: (-r.total, r.name.lower())) if r.total
        ]
        result["note"] += (" group — lidni birinchi bo'lib belgilagan odam; calls_in_period — davrdagi barcha "
                           "qo'ng'iroqlari (kunlik hisobot qoidasida).")
    return result


# ---------------------------------------------------------------------- bot holati (faqat egasiga)
async def bot_status(service: "ReportService", args: dict, now: datetime) -> dict:
    section = str(args.get("section") or "all")
    if section not in SECTIONS:
        raise InsightError(f"section: {', '.join(SECTIONS)} dan biri")
    builders = {"activity": _activity, "costs": _costs, "users": _users, "staff": _staff, "groups": _groups,
                "settings": _settings, "deliveries": _deliveries}
    parts = list(builders) if section == "all" else [section]
    data: dict[str, Any] = {"now": _stamp(now)}
    for part in parts:
        data[part] = await builders[part](service, now)
    return data


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _activity(service: "ReportService", now: datetime) -> dict:
    store = service.store
    since = iso(_day_start(now))
    counts = await store.counts_since(since)
    messages = await store.message_counts(since)
    return {"today_from_00_00": {"leads_and_posts": counts["leads"], "reactions": counts["reactions"],
                                 "lead_comments": counts["comments"], "group_messages": sum(messages.values())},
            "comments_waiting_analysis": await store.pending_count(),
            "voucher_posts_waiting_analysis": await service.vouchers.store.pending_count(),
            "tasks": await service.tasks.store.task_counts(iso(now))}


async def _costs(service: "ReportService", now: datetime) -> dict:
    from ..handlers import billing_cycle_start  # noqa: PLC0415 — handlers bu modulni import qiladi (sikl)

    ctx = service.ctx
    db = ctx.db
    cycle_start = billing_cycle_start(now, ctx.settings.budget_reset_day)
    names = {m.user_id: m.display for m in await db.members()}
    names.update(await service.usage_names())
    for owner in service.owners():
        names[owner] = "egasi"
    today, today_n = await db.spend_all(_day_start(now))
    cycle, cycle_n = await db.spend_all(cycle_start)
    total, total_n = await db.spend_all()
    data: dict[str, Any] = {
        "today_usd": round(today, 4), "today_requests": today_n,
        "cycle_since": f"{cycle_start.day}-{MONTHS[cycle_start.month - 1]}",
        "cycle_usd": round(cycle, 2), "cycle_requests": cycle_n,
        "all_time_usd": round(total, 2), "all_time_requests": total_n,
        "cycle_by_user": [{"who": names.get(uid, f"ID {uid}"), "usd": round(cost, 2), "requests": n}
                          for uid, cost, n in (await db.spend_by_user(cycle_start))[:15]],
        "cycle_by_model": [{"model": label(model), "usd": round(cost, 2), "requests": n}
                           for model, cost, n in await db.spend_by_model(None, cycle_start)],
        "note": "Botning o'z hisobi (taxminiy); aniq balans — platform.claude.com → Billing.",
    }
    budget = ctx.settings.monthly_budget
    if budget > 0:
        data["budget_usd"] = budget
        data["budget_used"] = f"{round(cycle * 100 / budget)}%"
    return data


async def _users(service: "ReportService", now: datetime) -> dict:
    members = await service.ctx.db.members()
    return {"owners": len(service.owners()),
            "members": [{"name": m.name or "Nomsiz", "username": f"@{m.username}" if m.username else None,
                         "active": m.active, "joined": (m.joined_at or "")[:10]} for m in members],
            "note": "members — parol bilan kirib AI'dan foydalanayotganlar; xodimlar — staff bo'limida"}


async def _staff(service: "ReportService", now: datetime) -> dict:
    groups: dict[str, list] = defaultdict(list)
    section = {"operator": "operators", "leader": "leaders", "excluded": "excluded", "staff": "departments"}
    for person in await service.store.people():
        item: dict[str, Any] = {"name": person.label,
                                "account": f"@{person.username}" if person.username else f"ID {person.user_id}"}
        if person.is_admin:
            item["position"] = "administrator"
        if person.kind == "staff":
            item["department"] = person.title
        if person.kind == "operator":
            item["gets_reminders"] = person.dm_ok
        if person.user_id is None:
            item["not_seen_yet"] = True  # bot hali ko'rmagan (guruhda yozmagan yoki botni ochmagan)
        groups[section.get(person.kind, person.kind)].append(item)
    return dict(groups) or {"note": "Xodimlar ro'yxati bo'sh — /xodimlar bilan qo'shiladi"}


async def _groups(service: "ReportService", now: datetime) -> dict:
    tz = service.tz
    week = await service.store.message_counts(iso(now - timedelta(days=7)))
    rows = []
    for group in await service.store.groups():
        rows.append({"title": group.title, "roles": [ROLE_NAMES.get(r, r) for r in group.roles] or ["tanlanmagan"],
                     "active": group.active, "bot_admin": group.is_admin,
                     "last_activity": _stamp(_local(group.last_seen, tz)), "messages_7_days": week.get(group.chat_id, 0)})
    return {"groups": rows, "bot_reads_all_messages": bool(service.bot_info.get("can_read_all", True)),
            "note": ("Bot admin bo'lmagan guruhda (BotFather'da Group Privacy yoqiq bo'lsa) faqat o'ziga yozilgan "
                     "xabarlarni ko'radi: oddiy yozishmalar va reaksiyalar yozilmaydi.")}


async def _settings(service: "ReportService", now: datetime) -> dict:
    ctx = service.ctx
    settings = ctx.settings
    s = await service.schedule()
    return {
        "version": ctx.updater.version if ctx.updater is not None else None,
        "daily_report": {"enabled": s.enabled, "time": s.time, "days": s.days_label(), "as_picture": s.picture},
        "unmarked_reminder": (f"{s.remind_minutes} daqiqadan keyin, {s.work_start}–{s.work_end}"
                              if s.remind_minutes else "o'chiq"),
        "journal_to_owner": s.journal_time if s.journal_enabled else "o'chiq",
        "excel_auto": {"weekly": s.weekly_enabled, "monthly": s.monthly_enabled, "time": s.excel_time},
        "statuses": [f"{st.emoji} {st.label}" + (" (asosiy natija)" if st.positive else "")
                     for st in await service.store.statuses()],
        "models": {"default": label(settings.default_model), "comment_analysis": label(settings.report_model)},
        "leader_daily_questions": settings.leader_daily_limit or "cheklanmagan",
        "backup": schedule_label(settings.backup_time, settings.backup_day) if settings.backup_time else "o'chiq",
        "update_check": settings.update_check, "timezone": settings.timezone, "mini_app": bool(settings.webapp_url),
        "group_messages_kept_days": CHAT_KEEP_DAYS,
    }


async def _deliveries(service: "ReportService", now: datetime) -> dict:
    tz = service.tz
    archive = service.archive.store
    daily = [{"period": f"{r['period_start']} → {r['period_end']}", "sent": _stamp(_local(r["sent_at"], tz))}
             for r in await service.store.recent_reports(10)]
    excel = []
    for row in await archive._all(
            "SELECT kind, first_day, last_day, sent_at FROM rp_exports ORDER BY first_day DESC, kind LIMIT 10"):
        excel.append({"kind": "haftalik" if row["kind"] == WEEK else "oylik",
                      "period": f"{row['first_day']} – {row['last_day']}",
                      "auto_sent": _stamp(_local(row["sent_at"], tz)) or "yo'q (faqat so'ralganda yasalgan)"})
    db = service.ctx.db
    return {"daily_reports": daily, "excel": excel,
            "journals": [j["day"] for j in await archive.journals(7)],
            "last_backup": await db.get_config(BACKUP_STAMP_KEY) or "hali yo'q",
            "last_update_check": await db.get_config(UPDATE_CHECK_KEY) or "hali yo'q",
            "last_report_went_to_owner_instead": service.last_fallback}


# ---------------------------------------------------------------------- guruh yozishmalari (faqat egasiga)
async def _replied_leads(service: "ReportService", rows: list[dict]) -> dict[tuple[int, int], Any]:
    """Javob berilgan xabar lid posti bo'lsa — o'sha lid (izoh qaysi lidga yozilgani ko'rinsin)."""
    wanted: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        wanted[row["chat_id"]].add(row["reply_to"])
    found: dict[tuple[int, int], Any] = {}
    for chat_id, ids in wanted.items():
        for mid in ids:
            lead = await service.store.lead_by_message(chat_id, mid)
            if lead is not None:
                found[(chat_id, mid)] = lead
    return found


def _group_matches(wanted: str, title: str, roles: tuple[str, ...]) -> bool:
    if wanted in _norm(title):
        return True
    return len(wanted) >= 3 and any(word.startswith(wanted) or wanted.startswith(word)
                                    for role in roles for word in ROLE_WORDS.get(role, ()))


async def group_messages(service: "ReportService", window: "Window", args: dict, now: datetime) -> dict:
    store, tz = service.store, service.tz
    groups = [g for g in await store.groups() if g.roles]
    if not groups:
        raise InsightError("Bot hali hech qaysi guruhga ulanmagan")
    wanted = _norm(args.get("group"))
    if wanted:
        picked = [g for g in groups if _group_matches(wanted, g.title, g.roles)]
        if not picked:
            raise InsightError("Bunday guruh topilmadi. Guruhlar: " + ", ".join(g.title for g in groups))
        groups = picked
    titles = {g.chat_id: g.title or str(g.chat_id) for g in groups}
    rows = await store.logged_messages(iso(window.start), iso(min(window.end, now)), list(titles))
    index = {(r["chat_id"], r["message_id"]): r for r in rows}
    author, search = _norm(args.get("author")), _norm(args.get("search"))
    if author:
        rows = [r for r in rows if author in _norm(r["name"]) or author == _norm(r["username"])]
    if search:
        rows = [r for r in rows if search in _norm(r["text"])]
    try:
        limit = max(1, min(int(args.get("limit") or MESSAGES_LIMIT), MESSAGES_MAX))
    except (TypeError, ValueError):
        limit = MESSAGES_LIMIT
    shown = rows[-limit:]  # ko'p bo'lsa — eng so'nggilari (vaqt tartibida)
    leads = await _replied_leads(service, [r for r in shown if r["reply_to"] and (r["chat_id"], r["reply_to"]) not in index])

    def item(row: dict) -> dict:
        moment = _local(row["created_at"], tz)
        text = row["text"] if len(row["text"]) <= MESSAGE_SHOWN else row["text"][:MESSAGE_SHOWN - 1] + "…"
        data: dict[str, Any] = {"time": f"{moment:%d.%m %H:%M}" if moment else None,
                                "author": row["name"] + (f" (@{row['username']})" if row["username"] else ""),
                                "text": text}
        if len(titles) > 1:
            data["group"] = titles.get(row["chat_id"], "")
        target = index.get((row["chat_id"], row["reply_to"])) if row["reply_to"] else None
        lead = leads.get((row["chat_id"], row["reply_to"]))
        if target is not None:
            data["reply_to"] = f"{target['name']}: {target['text'][:REPLY_SHOWN]}"
        elif lead is not None:  # lidlar guruhida — lid posti ostidagi izoh
            data["reply_to"] = "lid: " + " · ".join(x for x in (lead.name or "ismsiz", lead.course) if x)
        if row["edited"]:
            data["edited"] = True
        return data

    result: dict[str, Any] = {
        "period": window.as_dict(now), "groups": list(titles.values()), "total": len(rows), "shown": len(shown),
        "by_group": dict(Counter(titles.get(r["chat_id"], "") for r in rows)) if len(titles) > 1 else None,
        "top_authors": [{"name": name, "messages": n} for name, n in Counter(r["name"] for r in rows).most_common(8)],
        "messages": [item(r) for r in shown],
    }
    if len(rows) > limit:
        result["note"] = f"Ko'p — eng so'nggi {limit} tasi ko'rsatildi; aniqroq uchun author, search yoki qisqaroq davr."
    blind = [g.title for g in groups if not g.is_admin]
    if blind and not service.bot_info.get("can_read_all", True):
        result["warning"] = ("Bu guruhlarda bot admin emas — faqat o'ziga yozilgan xabarlarni ko'radi: "
                             + ", ".join(blind) + ". Hammasi yozilishi uchun botni admin qiling.")
    return {k: v for k, v in result.items() if v is not None}

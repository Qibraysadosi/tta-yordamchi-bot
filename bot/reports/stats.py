"""Hisobot davri va hisob-kitob: kim nechta lidga qo'ng'iroq qildi, qanday natija bilan.

Qoidalar (kelishilgan):
- Davr — hisobot vaqtidan hisobot vaqtigacha (masalan, kecha 19:00 → bugun 19:00); dam olish kunlari
  keyingi ish kunining davriga qo'shiladi.
- Bir davrda bitta lid bir odam uchun 1 marta sanaladi («nechta odamga qo'ng'iroq qildi»).
- Natija — eng so'nggi harakat: reaksiya yoki izoh (AI tahlili). Reaksiya va izoh mos kelmasa — «ziddiyat».
- Izohda natija aniqlanmasa, o'sha odamning shu lidga oldingi reaksiyasi olinadi.
- Operatorlar ro'yxati bo'sh bo'lsa, hamma sanaladi; bo'lmasa ro'yxatdagilar jadvalda, qolganlar —
  «ro'yxatda yo'q» qatorida; istisnolar hech qayerda sanalmaydi."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .store import Event, Lead, Person, ReportStore, Status, iso

SCHEDULE_KEY = "report:schedule"
OTHER = "❔"  # natijasi aniqlanmagan qo'ng'iroqlar ustuni
WEEKDAY_SHORT = ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya")


def _hhmm(value: str, default: str) -> str:
    try:
        hours, minutes = (int(x) for x in str(value).split(":"))
    except (TypeError, ValueError):
        return default
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return f"{hours:02d}:{minutes:02d}"
    return default


@dataclass
class Schedule:
    time: str = "19:00"  # kunlik hisobot vaqti
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5)  # weekday(): dushanba=0 … yakshanba=6
    enabled: bool = True
    remind_minutes: int = 30  # belgilanmagan lid eslatmasi (0 — o'chiq)
    work_start: str = "09:00"
    work_end: str = "19:00"
    journal_enabled: bool = True  # kundalik — egasiga lichkada (ish kunlari)
    journal_time: str = "20:00"
    weekly_enabled: bool = True  # haftalik Excel — dushanba
    monthly_enabled: bool = True  # oylik Excel — oyning 1-sanasi
    excel_time: str = "09:00"

    @classmethod
    def from_json(cls, raw: str | None) -> "Schedule":
        base = cls()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return base.updated(data)

    def updated(self, data: dict) -> "Schedule":
        days = self.days
        if "days" in data and isinstance(data["days"], (list, tuple)):
            picked = sorted({int(d) for d in data["days"] if str(d).isdecimal() and 0 <= int(d) <= 6})
            days = tuple(picked) or self.days
        remind = self.remind_minutes
        if "remind_minutes" in data:
            try:
                remind = max(0, min(int(data["remind_minutes"]), 24 * 60))
            except (TypeError, ValueError):
                pass
        return Schedule(
            time=_hhmm(data.get("time", self.time), self.time),
            days=days,
            enabled=bool(data.get("enabled", self.enabled)),
            remind_minutes=remind,
            work_start=_hhmm(data.get("work_start", self.work_start), self.work_start),
            work_end=_hhmm(data.get("work_end", self.work_end), self.work_end),
            journal_enabled=bool(data.get("journal_enabled", self.journal_enabled)),
            journal_time=_hhmm(data.get("journal_time", self.journal_time), self.journal_time),
            weekly_enabled=bool(data.get("weekly_enabled", self.weekly_enabled)),
            monthly_enabled=bool(data.get("monthly_enabled", self.monthly_enabled)),
            excel_time=_hhmm(data.get("excel_time", self.excel_time), self.excel_time),
        )

    def to_json(self) -> str:
        return json.dumps(self.as_dict())

    def as_dict(self) -> dict:
        return {
            "time": self.time, "days": list(self.days), "enabled": self.enabled,
            "remind_minutes": self.remind_minutes, "work_start": self.work_start, "work_end": self.work_end,
            "journal_enabled": self.journal_enabled, "journal_time": self.journal_time,
            "weekly_enabled": self.weekly_enabled, "monthly_enabled": self.monthly_enabled, "excel_time": self.excel_time,
        }

    def days_label(self) -> str:
        days = list(self.days)
        if days == list(range(days[0], days[-1] + 1)) and len(days) > 2:
            return f"{WEEKDAY_SHORT[days[0]]}–{WEEKDAY_SHORT[days[-1]]}"
        return ", ".join(WEEKDAY_SHORT[d] for d in days)


def _at(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=tz)


def period_end(day: date, s: Schedule, tz: ZoneInfo) -> datetime:
    return _at(day, s.time, tz)


def period_bounds(day: date, s: Schedule, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Shu kungi hisobot davri: oldingi hisobot kunining vaqtidan shu kungi vaqtgacha."""
    end = period_end(day, s, tz)
    previous = day - timedelta(days=1)
    for _ in range(7):
        if previous.weekday() in s.days:
            break
        previous -= timedelta(days=1)
    return period_end(previous, s, tz), end


def current_day(now: datetime, s: Schedule) -> date:
    """Hozirgi vaqt tushadigan davr (keyingi hisobot kuni)."""
    tz = now.tzinfo
    for offset in range(8):
        candidate = now.date() + timedelta(days=offset)
        if candidate.weekday() in s.days and period_end(candidate, s, tz) > now:  # type: ignore[arg-type]
            return candidate
    return now.date()


def display_day(now: datetime, s: Schedule) -> date:
    """Ko'rsatish uchun davr (/hisobot, Mini App): hisobot vaqtidan yarim tungacha — hozirgina tugagan bugungi davr
    (keyingisi hali bo'sh), qolgan paytda — joriy davr."""
    today = now.date()
    if today.weekday() in s.days and period_end(today, s, now.tzinfo) <= now:  # type: ignore[arg-type]
        return today
    return current_day(now, s)


def latest_due_day(now: datetime, s: Schedule) -> date | None:
    """Vaqti o'tgan eng so'nggi hisobot kuni (bugun yoki oldingi kunlar)."""
    tz = now.tzinfo
    for offset in range(8):
        candidate = now.date() - timedelta(days=offset)
        if candidate.weekday() in s.days and period_end(candidate, s, tz) <= now:  # type: ignore[arg-type]
            return candidate
    return None


def neighbour_day(day: date, s: Schedule, step: int) -> date:
    """Oldingi (step=-1) yoki keyingi (step=1) hisobot kuni."""
    candidate = day
    for _ in range(8):
        candidate += timedelta(days=step)
        if candidate.weekday() in s.days:
            return candidate
    return day + timedelta(days=step)


def in_work_hours(now: datetime, s: Schedule) -> bool:
    return now.weekday() in s.days and s.work_start <= now.strftime("%H:%M") < s.work_end


# ---------------------------------------------------------------------- hafta va oy (Excel)
def report_days(first: date, last: date, s: Schedule) -> list[date]:
    days, day = [], first
    while day <= last:
        if day.weekday() in s.days:
            days.append(day)
        day += timedelta(days=1)
    return days


def span_bounds(first: date, last: date, s: Schedule, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """Bir nechta kunning umumiy davri — kunlik hisobotlar davrlarining yig'indisi (raqamlar ular bilan bir xil)."""
    days = report_days(first, last, s)
    if not days:
        return None
    return period_bounds(days[0], s, tz)[0], period_bounds(days[-1], s, tz)[1]


def week_of(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def month_of(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    following = (first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1))
    return first, following - timedelta(days=1)


# ---------------------------------------------------------------------- hisob-kitob
@dataclass
class Row:
    key: str  # «u:<id>» yoki «p:<id>» (ro'yxatdagi, hali ko'rinmagan odam)
    name: str
    username: str | None
    user_id: int | None
    operator: bool
    total: int = 0
    counts: Counter = field(default_factory=Counter)  # status emoji → son; OTHER — aniqlanmagan

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "username": self.username, "user_id": self.user_id,
                "operator": self.operator, "total": self.total, "counts": dict(self.counts)}


@dataclass
class Overview:
    day: date
    start: datetime
    end: datetime
    live: bool
    statuses: list[Status]
    rows: list[Row]
    extras: list[Row]
    new_leads: int
    unmarked: int
    courses: list[tuple[str, int, int]]  # (kurs, belgilangan lidlar, asosiy natija soni)
    conflicts: int
    comments: int
    pending: int
    reasons: list[tuple[str, int]]  # (sabab, son) — AI tahlili natijasi, aniq matn bo'yicha
    callbacks: int
    has_operator_list: bool
    lead_status: dict[int, str] = field(default_factory=dict)  # lid → so'nggi natija (Excel «Lidlar» varag'i)
    lead_people: dict[int, list[int]] = field(default_factory=dict)  # lid → belgilagan odamlar (Telegram ID)

    @property
    def totals(self) -> Row:
        total = Row(key="total", name="JAMI", username=None, user_id=None, operator=True)
        for row in self.rows:
            total.total += row.total
            total.counts.update(row.counts)
        return total

    def positive(self, row: Row) -> int:
        return sum(row.counts.get(s.emoji, 0) for s in self.statuses if s.positive)

    def columns(self) -> list[str]:
        """Jadval ustunlari: statuslar tartibida + aniqlanmaganlar bo'lsa ❔."""
        cols = [s.emoji for s in self.statuses]
        everyone = self.rows + self.extras
        if any(r.counts.get(OTHER) for r in everyone):
            cols.append(OTHER)
        return cols


def _course_key(course: str) -> str:
    words = course.replace("(", " ").replace(")", " ").split()
    return " ".join(words[:3]).lower()[:28] or "kurs ko'rsatilmagan"


def _match(people: list[Person], user_id: int, username: str | None) -> Person | None:
    """Ro'yxatdagi odam: ID bo'yicha; username — faqat hali ID bilan bog'lanmagan yozuvda
    (bo'shatilgan username'ni olgan boshqa odam ro'yxatdagining o'rniga sanalmasin)."""
    uname = (username or "").lower()
    for person in people:
        if person.user_id == user_id or (person.user_id is None and uname and person.username == uname):
            return person
    return None


async def build_overview(store: ReportStore, s: Schedule, day: date, now: datetime) -> Overview:
    start, end = period_bounds(day, s, now.tzinfo)  # type: ignore[arg-type]
    return await overview_between(store, s, start, end, now, day)


async def overview_between(store: ReportStore, s: Schedule, start: datetime, end: datetime, now: datetime,
                           day: date | None = None) -> Overview:
    """Istalgan davr bo'yicha natijalar (kunlik hisobot, hafta, oy) — bir xil qoidalar bilan."""
    day = day or end.date()
    live = now < end
    upto = min(end, now)
    start_iso, upto_iso = iso(start), iso(upto)

    statuses = await store.statuses()
    known = {st.emoji for st in statuses}
    positive = {st.emoji for st in statuses if st.positive}
    events = await store.events_between(start_iso, upto_iso)
    people = await store.people()
    operators = [p for p in people if p.kind == "operator"]
    excluded = [p for p in people if p.kind == "excluded"]
    users = await store.users()

    def is_excluded(user_id: int) -> bool:  # istisnolar (rahbarlar va h.k.) hech qayerda sanalmaydi
        return _match(excluded, user_id, users.get(user_id, (None, ""))[0]) is not None

    # (lid, odam) bo'yicha harakatlar: so'nggi reaksiya va so'nggi natijali izoh
    reaction: dict[tuple[int, int], Event] = {}
    comment: dict[tuple[int, int], Event] = {}
    called: set[tuple[int, int]] = set()  # qo'ng'iroq haqida izoh yozilgan (natijasi bor-yo'qligidan qat'i nazar)
    conflicts: set[tuple[int, int]] = set()
    reasons: Counter = Counter()
    callbacks: set[int] = set()
    comment_count = 0
    for ev in events:
        if is_excluded(ev.user_id):
            continue
        pair = (ev.lead_id, ev.user_id)
        if ev.kind == "reaction":
            reaction[pair] = ev
        elif ev.kind == "comment" and ev.is_call:
            comment_count += 1
            called.add(pair)
            if ev.status:
                comment[pair] = ev
            if ev.reason and ev.status not in positive:
                reasons[ev.reason.strip().lower()] += 1
            if ev.callback:
                callbacks.add(ev.lead_id)
    # Reaksiya olib tashlangan (yoki status emas) va izoh ham yo'q bo'lsa — sanalmaydi
    touched = {pair for pair in set(reaction) | called if pair in called or reaction[pair].status}
    fallback = await store.reactions_before(start_iso, [p for p in touched if p not in reaction and p not in comment])

    status_of: dict[tuple[int, int], str] = {}
    latest_of_lead: dict[int, tuple[str, str]] = {}  # lid → (vaqt, status) — kurslar kesimi uchun
    for pair in sorted(touched):  # tartib barqaror: bir xil vaqtli harakatlarda natija har safar bir xil
        r, c = reaction.get(pair), comment.get(pair)
        r_status = r.status if r and r.status in known else None
        c_status = c.status if c and c.status in known else None
        if r_status and c_status and r_status != c_status:
            conflicts.add(pair)
        if r_status and (not c_status or (r.created_at, r.id) >= (c.created_at, c.id)):  # type: ignore[union-attr]
            value, stamp = r_status, r.created_at  # type: ignore[union-attr]
        elif c_status:
            value, stamp = c_status, c.created_at  # type: ignore[union-attr]
        else:
            before = fallback.get(pair)
            value = before if before in known else OTHER
            stamp = max((e.created_at for e in (r, c) if e is not None), default="")
        status_of[pair] = value
        lead_id = pair[0]
        if lead_id not in latest_of_lead or stamp >= latest_of_lead[lead_id][0]:
            latest_of_lead[lead_id] = (stamp, value)

    rows: dict[str, Row] = {}
    extras: dict[str, Row] = {}
    for person in operators:  # ro'yxatdagilar natija bo'lmasa ham ko'rinadi
        key = f"u:{person.user_id}" if person.user_id else f"p:{person.id}"
        rows[key] = Row(key=key, name=person.label, username=person.username, user_id=person.user_id, operator=True)
    for (lead_id, user_id), value in status_of.items():
        username, first_name = users.get(user_id, (None, ""))
        key = f"u:{user_id}"
        person = _match(operators, user_id, username)
        if person is not None:
            row = rows.get(key) or rows.pop(f"p:{person.id}", None)
            if row is None:
                row = Row(key=key, name=person.label, username=person.username, user_id=user_id, operator=True)
            row.key, row.user_id = key, user_id
            rows[key] = row
        elif not operators:
            row = rows.setdefault(key, Row(key=key, name=first_name or (f"@{username}" if username else f"ID {user_id}"),
                                           username=username, user_id=user_id, operator=False))
        else:
            row = extras.setdefault(key, Row(key=key, name=first_name or (f"@{username}" if username else f"ID {user_id}"),
                                             username=username, user_id=user_id, operator=False))
        row.total += 1
        row.counts[value] += 1

    new = await store.leads_between(start_iso, upto_iso)
    marked = await store.touched_lead_ids(lead.id for lead in new)
    leads = await store.leads_by_ids(latest_of_lead)
    course_total: Counter = Counter()
    course_positive: Counter = Counter()
    for lead_id, (_, value) in latest_of_lead.items():
        lead: Lead | None = leads.get(lead_id)
        key = _course_key(lead.course if lead else "")
        course_total[key] += 1
        if value in positive:
            course_positive[key] += 1
    courses = sorted(
        ((k, course_total[k], course_positive[k]) for k in course_total),
        key=lambda item: (-item[2], -item[1], item[0]),
    )

    def order(item: Row) -> tuple:
        return (-item.total, item.name.lower())

    lead_people: dict[int, list[int]] = {}
    for lead_id, user_id in sorted(status_of):
        lead_people.setdefault(lead_id, []).append(user_id)
    return Overview(
        day=day, start=start, end=end, live=live, statuses=statuses,
        rows=sorted(rows.values(), key=order), extras=sorted(extras.values(), key=order),
        new_leads=len(new), unmarked=sum(1 for lead in new if lead.id not in marked),
        courses=courses, conflicts=len(conflicts), comments=comment_count,
        pending=await store.pending_count(start_iso, upto_iso),
        reasons=reasons.most_common(), callbacks=len(callbacks), has_operator_list=bool(operators),
        lead_status={lead_id: value for lead_id, (_, value) in latest_of_lead.items()}, lead_people=lead_people,
    )

"""Lid oqimi nazorati (v1.16): ish vaqtida odatdagidan uzoq lid tushmasa — egasiga ogohlantirish, lidlar yana tushsa —
«tiklandi» xabari.

Chegara o'zi hisoblanadi: so'nggi 14 ish kunida har soatda o'rtacha nechta lid tushgani (soatlik profil). Jimlik
boshidan hozirgacha shu profil bo'yicha kamida 4.6 ta lid kutilgan bo'lsa (Puasson: «bitta ham tushmaslik» ehtimoli
1% dan kam) va kamida 45 daqiqa o'tgan bo'lsa — ogohlantirish. Lid kam tushadigan bo'lsa (14 kunda 20 tadan kam) —
ogohlantirilmaydi: tasodifiy tanaffuslar ko'p. Jimlik faqat ish kunlari, ish vaqtida (work_start–work_end) o'lchanadi.
Har jimlik uchun bitta xabar; holat bot_config'da (bot qayta ishga tushsa takrorlanmaydi). Meta Ads ulangan bo'lsa —
xabarda kabinet holati, bugungi sarf va Meta bo'yicha lidlar (sabab reklamadami yoki forma → Telegram ulanishidami)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from .stats import Schedule, in_work_hours
from .store import iso
from .tasks import from_iso

if TYPE_CHECKING:
    from .service import ReportService

log = logging.getLogger(__name__)

PROFILE_DAYS = 14
MIN_DAYS = 3  # kamida shuncha ish kuni kuzatilgan bo'lsin
MIN_SAMPLE = 20  # 14 kunda shundan kam lid — ogohlantirilmaydi
ALERT_EXPECTED = 4.6  # kutilgan lidlar soni: e^-4.6 ≈ 1% — tasodifan bitta ham tushmasligi shu qadar kam
MIN_SILENCE = timedelta(minutes=45)
CHECK_EVERY = timedelta(minutes=5)
PROFILE_TTL = timedelta(hours=1)
STATE_KEY = "report:silence"


def duration(delta: timedelta) -> str:
    minutes = max(0, round(delta.total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} soat {minutes} daqiqa"
    return f"{hours} soat" if hours else f"{minutes} daqiqa"


def _hm(value: str) -> time:
    hours, minutes = (int(x) for x in value.split(":"))
    return time(hours, minutes)


@dataclass
class Profile:
    hourly: list[float]  # soat (0–23) → bir ish kunida shu soatda o'rtacha nechta lid
    days: int
    leads: int

    def expected(self, start: datetime, end: datetime) -> float:
        """[start, end) oralig'ida kutilgan lidlar soni."""
        total, cursor = 0.0, start
        while cursor < end:
            step = min(end, cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            total += self.hourly[cursor.hour] * (step - cursor).total_seconds() / 3600
            cursor = step
        return total

    def per_hour(self, s: Schedule) -> float:
        """Ish vaqtida soatiga o'rtacha lid."""
        first, last = _hm(s.work_start), _hm(s.work_end)
        hours = [h for h in range(24) if first.hour <= h < max(last.hour + (1 if last.minute else 0), first.hour + 1)]
        return sum(self.hourly[h] for h in hours) / len(hours) if hours else 0.0

    def typical_wait(self, s: Schedule) -> timedelta | None:
        """Ish vaqtida o'rtacha qancha jimlikdan keyin xabar keladi."""
        rate = self.per_hour(s)
        return max(MIN_SILENCE, timedelta(hours=ALERT_EXPECTED / rate)) if rate > 0 else None


class LeadWatch:
    def __init__(self, reports: "ReportService") -> None:
        self.reports = reports
        self._profile: tuple[datetime, Profile | None] | None = None
        self._next: datetime | None = None

    async def profile(self, now: datetime, s: Schedule) -> Profile | None:
        cached = self._profile
        if cached is not None and timedelta(0) <= now - cached[0] < PROFILE_TTL:
            return cached[1]
        reports, tz = self.reports, self.reports.tz
        today = now.date()
        since = (await reports.since()).astimezone(tz)
        first = since.date() + (timedelta(days=1) if since.time() > _hm(s.work_start) else timedelta(0))
        first = max(first, today - timedelta(days=PROFILE_DAYS))
        days = {first + timedelta(days=i) for i in range((today - first).days)}
        days = {d for d in days if d.weekday() in s.days}
        profile = None
        if len(days) >= MIN_DAYS:
            start = datetime.combine(min(days), time(0), tzinfo=tz)
            counts, total = [0] * 24, 0
            for lead in await reports.store.leads_between(iso(start), iso(datetime.combine(today, time(0), tzinfo=tz))):
                moment = from_iso(lead.created_at, tz)
                if moment is not None and moment.date() in days:
                    counts[moment.hour] += 1
                    total += 1
            if total >= MIN_SAMPLE:
                profile = Profile([c / len(days) for c in counts], len(days), total)
        self._profile = (now, profile)
        return profile

    async def _state(self) -> dict | None:
        raw = await self.reports.ctx.db.get_config(STATE_KEY)
        try:
            state = json.loads(raw) if raw else None
        except ValueError:
            return None
        return state if isinstance(state, dict) and state.get("since") else None

    async def _save(self, state: dict | None) -> None:
        await self.reports.ctx.db.set_config(STATE_KEY, json.dumps(state) if state else "")

    async def check(self, bot: Any, now: datetime) -> bool:
        """30 soniyalik sikldan (5 daqiqada bir marta): jimlik bo'lsa — ogohlantirish, tiklansa — xabar."""
        if self._next is not None and now < self._next:
            return False
        self._next = now + CHECK_EVERY
        reports, tz = self.reports, self.reports.tz
        last = from_iso(await reports.store.last_lead_at(), tz)
        state = await self._state()
        if state is not None and last is not None and last > from_iso(state["since"], tz):  # type: ignore[operator]
            await self._recovered(bot, state, last)
            state = None
        s = await reports.schedule()
        if not s.ads_alerts or not in_work_hours(now, s) or not await reports.store.groups_with("leads"):
            return False
        day_start = datetime.combine(now.date(), _hm(s.work_start), tzinfo=tz)
        silent_from = max(last, day_start) if last is not None else day_start
        if now - silent_from < MIN_SILENCE or (state is not None and state["since"] == iso(silent_from)):
            return False
        profile = await self.profile(now, s)
        if profile is None:
            return False
        expected = profile.expected(silent_from, now)
        if expected < ALERT_EXPECTED:
            return False
        await self._save({"since": iso(silent_from), "last": iso(last) if last else None, "alerted": iso(now)})
        await reports._tell_owners(bot, await self._alert_text(now, silent_from, last, expected))
        return True

    async def _alert_text(self, now: datetime, silent_from: datetime, last: datetime | None, expected: float) -> str:
        if last is not None and last >= silent_from:
            head = f"{last:%H:%M} dan beri ({duration(now - last)})"
        else:
            head = f"Ish boshlanganidan ({silent_from:%H:%M}) beri ({duration(now - silent_from)}"
            head += f"; oxirgi lid — {last:%d.%m %H:%M})" if last is not None else ")"
        lines = [
            "⚠️ <b>Lid kelmay qoldi</b>",
            f"{head} lidlar guruhiga bitta ham ariza tushmadi — odatda shu vaqtda ~{round(expected)} ta tushardi.",
            "",
            "Tekshiring: reklama ishlayaptimi (to'xtagan, to'lov, rad etilgan), ariza formasi va forma → Telegram ulanishi.",
        ]
        try:
            extra = await self.reports.ads.alert_lines(now)
        except Exception:  # noqa: BLE001 — Meta ma'lumoti bo'lmasa ham ogohlantirish ketsin
            log.exception("Meta holati olinmadi")
            extra = []
        if extra:
            lines += [""] + extra
        lines.append("<i>Ogohlantirishni o'chirish: /reklama o'chir</i>")
        return "\n".join(lines)

    async def _recovered(self, bot: Any, state: dict, new: datetime) -> None:
        await self._save(None)
        tz = self.reports.tz
        before = from_iso(state.get("last"), tz) or from_iso(state["since"], tz)
        pause = f" Tanaffus — {duration(new - before)} ({before:%H:%M} dan)." if before is not None else ""
        await self.reports._tell_owners(bot, f"✅ <b>Lidlar yana tushyapti</b>: {new:%H:%M} da yangi lid.{pause}")

    async def status(self, now: datetime) -> str:
        """/reklama va Mini App uchun qisqa holat."""
        s = await self.reports.schedule()
        if not s.ads_alerts:
            return "🔕 Ogohlantirishlar o'chiq (yoqish: /reklama yoq)"
        profile = await self.profile(now, s)
        if profile is None:
            return ("🔔 Lid oqimi kuzatiladi; ogohlantirish uchun hali ma'lumot kam (so'nggi 14 ish kunida kamida "
                    f"{MIN_SAMPLE} ta lid kerak)")
        wait = profile.typical_wait(s)
        tail = f" — taxminan {duration(wait)} bitta ham tushmasa, sizga xabar keladi" if wait else ""
        return f"🔔 Lid oqimi: ish vaqtida odatda soatiga ~{profile.per_hour(s):.1f} ta lid{tail}"

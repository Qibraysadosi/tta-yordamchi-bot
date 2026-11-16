"""Reklama nazorati — Meta Ads (v1.16, faqat egasiga): sarf, lidlar, «keladi» va vaucher narxi, muammolar.

Ma'lumot Meta'dan (bot/meta_ads.py) va botning o'zidan: guruhga tushgan lidlar, asosiy natija (kogorta — davrda tushgan
lidlarning hozirgi natijasi) va vaucherga yuborilganlar. Meta kunlari — kalendar kunlar, reklama kabineti vaqti bilan
(bot vaqtidan farq qilsa — aytiladi).
Ko'rinadi: /reklama, kundalik (20:00), kunlik tavsiya («ads» bo'limi), AI suhbati (ads_stats), lid oqimi
ogohlantirishi. Rahbarlar guruhidagi hisobotga chiqmaydi.
Ogohlantirish (30 daqiqada bir tekshiradi, Schedule.ads_alerts): kabinet faol emas, xarajat chegarasining 90% i
ishlatildi, reklama / ad set / kampaniya rad etildi yoki muammoli — har muammo bir marta (bot_config)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
from datetime import date, datetime, time, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import meta_ads
from ..meta_ads import (LEVEL_LABELS, LEVELS, NO_CENTS, PROBLEM_STATUSES, AdAccount, AdObject, InsightRow,
                        MetaAdsClient, MetaAdsError, cents_of)
from . import insights, vouchers
from .assistant import Window, resolve_window
from .stats import report_days
from .store import iso

if TYPE_CHECKING:
    from .service import ReportService

log = logging.getLogger(__name__)

CACHE_TTL = 600.0  # soniya: Meta ma'lumoti shu vaqt qayta so'ralmaydi
INFO_TIMEOUT = 6.0  # Mini App sozlamalari Meta sekin javob bersa ham tez ochilsin
CACHE_SIZE = 200
CHECK_EVERY = timedelta(minutes=30)
PROBLEMS_KEY = "report:ads_problems"
AUTH_KEY = "report:ads_auth_warned"
AUTH_REPEAT = timedelta(hours=24)
CAP_WARN = 0.9
GAP_MIN, GAP_SHARE = 5, 0.7  # Meta lidlari guruhdagidan ≥5 ta ko'p va guruhda <70% — forma → Telegram ulanishi
TOP, TOP_ADSETS, PROBLEMS_SHOWN = 15, 20, 10
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}
NOTE = ("Meta ma'lumoti — kalendar kunlar (reklama kabineti vaqti bilan). meta_leads — Meta hisoblagan lidlar (forma "
        "to'ldirilgani); our_leads — lidlar guruhiga tushgan arizalar; main_result — ulardan asosiy natijaga (masalan, "
        "«keladi») aylangani (davrda tushgan lidlarning hozirgi natijasi); vouchers — vaucherga yuborilganlar. "
        "cost_per_* — sarf / shu son.")


def esc(text: str) -> str:
    """Telegram HTML uchun (apostrof — o'zbekcha so'zlarda — o'zgarmaydi)."""
    return html.escape(text, quote=False)


def money(value: float | None, currency: str) -> str:
    if value is None:
        return "—"
    cur = (currency or "").upper()
    text = f"{value:,.0f}" if cur in NO_CENTS or value >= 10_000 else f"{value:,.2f}"
    text = text.replace(",", " ")
    symbol = SYMBOLS.get(cur)
    return f"{symbol}{text}" if symbol else f"{text} {cur}".strip()


def _ratio(value: float, count: int | None) -> float | None:
    return round(value / count, 2) if count else None


def _row(r: InsightRow) -> dict:
    data: dict[str, Any] = {"name": r.name, "spend": round(r.spend, 2), "impressions": r.impressions,
                            "clicks": r.clicks, "meta_leads": r.leads}
    if r.day:
        data["date"] = r.day
    if r.level in ("adset", "ad") and r.campaign:
        data["campaign"] = r.campaign
    if r.level == "ad" and r.adset:
        data["adset"] = r.adset
    for key, value in (("cost_per_meta_lead", r.cpl), ("ctr_pct", r.ctr), ("cpm", r.cpm),
                       ("frequency", r.frequency or None)):
        if value is not None:
            data[key] = round(value, 2)
    return data


def _object(obj: AdObject) -> dict:
    data: dict[str, Any] = {"level": LEVEL_LABELS.get(obj.level, obj.level), "name": obj.name, "status": obj.status_label}
    for key in ("campaign", "adset", "issue"):
        if getattr(obj, key):
            data[key] = getattr(obj, key)
    if obj.daily_budget is not None:
        data["daily_budget"] = round(obj.daily_budget, 2)
    if obj.lifetime_budget is not None:
        data["lifetime_budget"] = round(obj.lifetime_budget, 2)
    return data


class AdsService:
    def __init__(self, reports: "ReportService", client: MetaAdsClient | None = None) -> None:
        self.reports = reports
        self.ctx = reports.ctx
        self.client: MetaAdsClient | None = client if client is not None else meta_ads.build(reports.ctx.settings)
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._next_check: datetime | None = None

    @property
    def connected(self) -> bool:
        return self.client is not None

    def not_connected(self) -> str:
        problem = self.ctx.settings.meta_problem
        return ("Meta Ads ulanmagan" + (f" ({problem})" if problem else "") + ". Ulash: serverdagi .env ga "
                "META_ACCESS_TOKEN va META_AD_ACCOUNT_ID yozing, keyin konteynerni qayta yarating "
                "(README → «Reklama nazorati»)")

    def _client(self) -> MetaAdsClient:
        if self.client is None:
            raise MetaAdsError(self.not_connected())
        return self.client

    async def _cached(self, key: tuple, build: Callable[[], Awaitable[Any]], *, fresh: bool = False) -> Any:
        hit = self._cache.get(key)
        if hit is not None and not fresh and monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        value = await build()
        if len(self._cache) >= CACHE_SIZE:
            self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
        self._cache[key] = (monotonic(), value)
        return value

    # ------------------------------------------------------------------ Meta ma'lumoti
    async def account(self, *, fresh: bool = False) -> AdAccount:
        client = self._client()
        return await self._cached(("account",), client.account, fresh=fresh)

    async def insights(self, level: str, since: date, until: date, *, by_day: bool = False) -> list[InsightRow]:
        client = self._client()
        return await self._cached(("insights", level, since, until, by_day),
                                  lambda: client.insights(level, since, until, by_day=by_day))

    async def problems(self, *, fresh: bool = False) -> list[AdObject]:
        """Rad etilgan / muammoli kampaniya, ad set va reklamalar."""
        client = self._client()

        async def build() -> list[AdObject]:
            cents = cents_of((await self.account()).currency)
            found: list[AdObject] = []
            for level in ("campaign", "adset", "ad"):
                found += await client.objects(level, PROBLEM_STATUSES, cents=cents)
            return found

        return await self._cached(("problems",), build, fresh=fresh)

    async def active(self) -> list[AdObject]:
        """Faol kampaniya va ad set'lar (byudjeti bilan)."""
        client = self._client()

        async def build() -> list[AdObject]:
            cents = cents_of((await self.account()).currency)
            return [obj for level in ("campaign", "adset")
                    for obj in await client.objects(level, ("ACTIVE",), cents=cents)]

        return await self._cached(("active",), build)

    # ------------------------------------------------------------------ bizning natija
    async def funnel(self, first: date, last: date, now: datetime, *, by_day: bool = False) -> dict:
        """Kalendar kunlarda guruhga tushgan lidlar, ularning asosiy natijasi va vaucherga yuborilganlar."""
        reports = self.reports
        s = await reports.schedule()
        tz = reports.tz
        start = datetime.combine(first, time(0), tzinfo=tz)
        end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz)
        window = Window(f"{first:%d.%m}–{last:%d.%m.%Y}, kalendar kunlar", start, end, report_days(first, last, s),
                        now < end, first, last, calendar=True)
        found = await insights.breakdown(reports, window, s, now, {"by": "day"}, masked=True, name_of=lambda _: "")
        totals = found["totals"]
        result: dict[str, Any] = {"our_leads": totals["new_leads"], "main_result": totals["main_result"]}
        if by_day:
            result["days"] = {g["group"][:10]: g for g in found["groups"]}
        if await reports.store.groups_with(vouchers.ROLE) or await reports.vouchers.store.voucher_total():
            listed = await vouchers.voucher_list(reports.vouchers, window, {"limit": 1}, now, masked=True)
            result["vouchers"] = listed["total"]
        return result

    async def summary(self, first: date, last: date, now: datetime) -> dict:
        rows = await self.insights("account", first, last)
        spend = sum(r.spend for r in rows)
        meta, impressions, clicks = (sum(getattr(r, k) for r in rows) for k in ("leads", "impressions", "clicks"))
        funnel = await self.funnel(first, last, now)
        data: dict[str, Any] = {"spend": round(spend, 2), "impressions": impressions, "clicks": clicks,
                                "meta_leads": meta, "our_leads": funnel["our_leads"],
                                "main_result": funnel["main_result"]}
        if "vouchers" in funnel:
            data["vouchers"] = funnel["vouchers"]
        for key, count in (("cost_per_meta_lead", meta), ("cost_per_our_lead", funnel["our_leads"]),
                           ("cost_per_main_result", funnel["main_result"]), ("cost_per_voucher", data.get("vouchers"))):
            value = _ratio(spend, count)
            if value is not None:
                data[key] = value
        if impressions:
            data["ctr_pct"] = round(clicks * 100 / impressions, 2)
            data["cpm"] = round(spend * 1000 / impressions, 2)
        return data

    async def _main(self) -> str:
        main = next((st for st in await self.reports.store.statuses() if st.positive), None)
        return f"{main.emoji} {main.label}" if main is not None else "asosiy natija"

    def _same_tz(self, account: AdAccount) -> bool:
        return not account.timezone or account.timezone == self.ctx.settings.timezone

    def _tz_note(self, account: AdAccount) -> str | None:
        if self._same_tz(account):
            return None
        return f"Meta kunlari — kabinet vaqti ({account.timezone}) bo'yicha, bot vaqti — {self.ctx.settings.timezone}"

    # ------------------------------------------------------------------ matnlar
    def _line(self, label: str, data: dict, currency: str, main: str) -> str:
        line = f"<b>{label}</b>: {money(data['spend'], currency)} · Meta {data['meta_leads']} lid · guruhga {data['our_leads']}"
        if data.get("cost_per_our_lead") is not None:
            line += f" ({money(data['cost_per_our_lead'], currency)})"
        emoji = main.split(" ")[0]
        line += f" · {esc(emoji)} {data['main_result']}"
        if data.get("cost_per_main_result") is not None:
            line += f" ({money(data['cost_per_main_result'], currency)})"
        if "vouchers" in data:
            line += f" · 📄 {data['vouchers']}"
            if data.get("cost_per_voucher") is not None:
                line += f" ({money(data['cost_per_voucher'], currency)})"
        return line

    def _problem_lines(self, problems: list[AdObject]) -> list[str]:
        if not problems:
            return []
        lines = [f"⚠️ <b>Muammoli reklamalar: {len(problems)}</b>"]
        for obj in problems[:PROBLEMS_SHOWN]:
            item = f"• {LEVEL_LABELS.get(obj.level, obj.level)} «{esc(obj.name)}» — {obj.status_label}"
            if obj.issue:
                item += f": {esc(obj.issue[:120])}"
            lines.append(item)
        if len(problems) > PROBLEMS_SHOWN:
            lines.append(f"… va yana {len(problems) - PROBLEMS_SHOWN} ta")
        return lines

    async def overview_text(self, now: datetime) -> str:
        """/reklama: lid oqimi holati va Meta Ads xulosasi."""
        lines = ["📣 <b>Reklama nazorati</b>", "", esc(await self.reports.watch.status(now)), ""]
        if not self.connected:
            problem = self.ctx.settings.meta_problem
            lines += [
                "<b>Meta Ads</b>: ulanmagan" + (f" — {esc(problem)}" if problem else "") + ".",
                "Ulansa: kunlik sarf, lid, «keladi» va vaucher narxi; rad etilgan reklama, to'lov va kabinet muammolari "
                "haqida darrov xabar; kundalik va kunlik tavsiyada reklama tahlili.",
                "Ulash: serverdagi <code>.env</code> ga <code>META_ACCESS_TOKEN</code> va <code>META_AD_ACCOUNT_ID</code> "
                "yozing, keyin <code>docker compose up -d --force-recreate</code> (README → «Reklama nazorati»).",
            ]
            return "\n".join(lines)
        try:
            account = await self.account()
            cur, main = account.currency, await self._main()
            today = now.date()
            status = account.status_label if account.active else f"⚠️ {account.status_label}"
            lines.append(f"<b>Meta Ads</b> · «{esc(account.name)}» · {esc(status)} · {cur}")
            share = account.cap_share
            if share is not None:
                lines.append(f"Xarajat chegarasi: {share:.0%} ishlatildi")
            with_vouchers = False
            for label, first in (("Bugun", today), ("Kecha", today - timedelta(days=1)), ("7 kun", today - timedelta(days=6)),
                                 ("30 kun", today - timedelta(days=29))):
                last = today - timedelta(days=1) if label == "Kecha" else today
                summary = await self.summary(first, last, now)
                with_vouchers = with_vouchers or "vouchers" in summary
                lines.append(self._line(label, summary, cur, main))
            campaigns = sorted(await self.insights("campaign", today - timedelta(days=6), today), key=lambda r: -r.spend)
            if campaigns:
                lines += ["", "<b>Kampaniyalar (7 kun)</b>"]
                for r in campaigns[:8]:
                    item = f"• {esc(r.name or '—')} — {money(r.spend, cur)} · {r.leads} lid"
                    if r.cpl is not None:
                        item += f" ({money(r.cpl, cur)})"
                    lines.append(item)
            problems = self._problem_lines(await self.problems())
            if problems:
                lines += [""] + problems
            note = self._tz_note(account)
            ours = esc(main) + (" va 📄 vaucher" if with_vouchers else "")
            lines += ["", f"<i>Meta lidi — forma to'ldirilgani; guruhga — lidlar guruhiga tushgani; {ours} — shu "
                          "kunlarda tushgan lidlardan. Qavsda — bittasining narxi.</i>"]
            if note:
                lines.append(f"<i>{esc(note)}</i>")
        except MetaAdsError as exc:
            lines.append(f"<b>Meta Ads</b>: ⚠️ {esc(str(exc))}")
        return "\n".join(lines)

    async def journal_lines(self, day: date, now: datetime) -> list[str]:
        """Kundalik uchun qisqa blok (Meta ulanmagan bo'lsa — bo'sh)."""
        if not self.connected:
            return []
        try:
            account = await self.account()
            cur, main = account.currency, await self._main()
            today = await self.summary(day, day, now)
            week = await self.summary(day - timedelta(days=6), day, now)
            problems = await self.problems()
        except MetaAdsError as exc:
            return ["📣 <b>Reklama (Meta)</b>", f"⚠️ {esc(str(exc))}"]
        head = "📣 <b>Reklama (Meta)</b>"
        if not account.active:
            head += f" · ⚠️ kabinet {esc(account.status_label)}"
        lines = [head, self._line("Bugun", today, cur, main), self._line("7 kun", week, cur, main)]
        if self._same_tz(account) and today["meta_leads"] - today["our_leads"] >= GAP_MIN \
                and today["our_leads"] < today["meta_leads"] * GAP_SHARE:
            lines.append(f"⚠️ Meta'da bugun {today['meta_leads']} ta lid, guruhga {today['our_leads']} ta tushdi — "
                         "forma → Telegram ulanishini tekshiring")
        if problems:
            names = ", ".join(f"«{esc(p.name)}» ({p.status_label})" for p in problems[:3])
            lines.append(f"⚠️ Muammoli reklamalar: {len(problems)} — {names}" + (" …" if len(problems) > 3 else ""))
        note = self._tz_note(account)
        if note:
            lines.append(f"<i>{esc(note)}</i>")
        return lines

    async def alert_lines(self, now: datetime) -> list[str]:
        """Lid oqimi to'xtaganda: sabab reklamadami yoki ulanishdami."""
        if not self.connected:
            return ["💡 Meta Ads ulansa, sababini ham shu yerda ko'rsataman (/reklama)."]
        try:
            account = await self.account(fresh=True)
            rows = await self.insights("account", now.date(), now.date())
            problems = await self.problems()
            ours = (await self.funnel(now.date(), now.date(), now))["our_leads"]
        except MetaAdsError as exc:
            return [f"📣 Meta: ⚠️ {esc(str(exc))}"]
        spend, meta = sum(r.spend for r in rows), sum(r.leads for r in rows)
        lines = [f"📣 Meta: kabinet {esc(account.status_label)} · bugun {money(spend, account.currency)} "
                 f"sarflandi · Meta bo'yicha {meta} ta lid (guruhga {ours} ta tushdi)"]
        if not account.active:
            lines.append("⚠️ Kabinet faol emas — reklama ko'rsatilmayapti.")
        elif spend <= 0:
            lines.append("⚠️ Bugun reklamaga pul sarflanmagan — reklama to'xtagan bo'lishi mumkin (byudjet, to'lov, jadval).")
        elif self._same_tz(account) and meta > ours:
            lines.append("⚠️ Meta'da lidlar bor, lekin guruhga kam tushyapti — forma → Telegram ulanishini tekshiring.")
        if problems:
            lines.append(f"⚠️ Muammoli reklamalar: {len(problems)} — /reklama")
        return lines

    async def info(self) -> dict:
        """Mini App sozlamalari uchun."""
        data: dict[str, Any] = {"connected": self.connected, "problem": self.ctx.settings.meta_problem}
        if self.connected:
            try:
                account = await asyncio.wait_for(self.account(), INFO_TIMEOUT)
                data.update(account=account.name, status=account.status_label, active=account.active,
                            currency=account.currency)
            except MetaAdsError as exc:
                data["error"] = str(exc)
            except asyncio.TimeoutError:
                data["error"] = "Meta hozir javob bermayapti — birozdan keyin qayta oching"
        return data

    # ------------------------------------------------------------------ kunlik tavsiya va AI suhbati
    async def digest(self, day: date, now: datetime) -> dict | None:
        """Kunlik tavsiya uchun reklama ma'lumoti (Meta ulanmagan bo'lsa — None)."""
        if not self.connected:
            return None
        try:
            account = await self.account()
            first14, first7, first30 = (day - timedelta(days=n) for n in (13, 6, 29))
            daily = {r.day: r for r in await self.insights("account", first14, day, by_day=True) if r.day}
            ours = (await self.funnel(first14, day, now, by_day=True))["days"]
            days = []
            for n in range(14):
                d = (first14 + timedelta(days=n)).isoformat()
                row, our = daily.get(d), ours.get(d) or {}
                days.append({"date": d, "spend": round(row.spend, 2) if row else 0.0, "meta_leads": row.leads if row else 0,
                             "our_leads": our.get("new_leads", 0), "main_result": our.get("main_result", 0)})

            def top(rows: list[InsightRow], count: int) -> list[dict]:
                return [_row(r) for r in sorted(rows, key=lambda r: -r.spend)[:count] if r.spend or r.leads]

            result: dict[str, Any] = {
                "account": {"name": account.name, "currency": account.currency, "status": account.status_label,
                            "timezone": account.timezone or self.ctx.settings.timezone},
                "note": NOTE, "days": days,
                "last_7_days": await self.summary(first7, day, now),
                "last_30_days": await self.summary(first30, day, now),
                "campaigns_7d": top(await self.insights("campaign", first7, day), TOP),
                "adsets_7d": top(await self.insights("adset", first7, day), TOP_ADSETS),
                "ads_7d": top(await self.insights("ad", first7, day), TOP),
                "campaigns_30d": top(await self.insights("campaign", first30, day), TOP),
                "active_budgets": [_object(obj) for obj in await self.active()],
                "problems": [_object(obj) for obj in await self.problems()],
            }
            share = account.cap_share
            if share is not None:
                result["account"]["spend_cap_used"] = f"{share:.0%}"
            return result
        except MetaAdsError as exc:
            return {"unavailable": str(exc)}

    async def stats(self, args: dict, now: datetime) -> dict:
        """AI vositasi ads_stats (faqat egasi)."""
        if not self.connected:
            raise MetaAdsError(self.not_connected())
        level = str(args.get("level") or "campaign")
        if level not in LEVELS:
            raise MetaAdsError(f"level: {', '.join(LEVELS)} dan biri")
        window = resolve_window({**args, "calendar": True}, now, await self.reports.schedule())
        first = window.first or now.date()
        last = min(window.last or first, now.date())
        try:
            limit = max(1, min(int(args.get("limit") or 20), 100))
        except (TypeError, ValueError):
            limit = 20
        account = await self.account()
        rows = sorted(await self.insights(level, first, last, by_day=bool(args.get("by_day"))),
                      key=lambda r: (r.day or "", -r.spend))  # nusxa: keshdagi ro'yxat o'zgarmasin
        result: dict[str, Any] = {
            "period": window.as_dict(now), "level": level,
            "account": {"name": account.name, "currency": account.currency, "status": account.status_label},
            "totals": await self.summary(first, last, now),
            "rows": [_row(r) for r in rows[:limit]],
            "problems": [_object(obj) for obj in await self.problems()],
            "note": NOTE,
        }
        if len(rows) > limit:
            result["more_rows"] = len(rows) - limit
        note = self._tz_note(account)
        if note:
            result["timezone_note"] = note
        return result

    # ------------------------------------------------------------------ ogohlantirish
    async def check(self, bot: Any, now: datetime) -> int:
        """30 soniyalik sikldan (30 daqiqada bir marta): yangi muammolar — egasiga bir marta."""
        if not self.connected or (self._next_check is not None and now < self._next_check):
            return 0
        self._next_check = now + CHECK_EVERY
        if not (await self.reports.schedule()).ads_alerts:
            return 0
        try:
            account = await self.account(fresh=True)
            problems = await self.problems(fresh=True)
        except MetaAdsError as exc:
            if exc.auth:
                await self._warn_auth(bot, now, str(exc))
            return 0
        current: dict[str, str] = {}
        if not account.active:
            current[f"account:{account.status}"] = f"Reklama kabineti: {account.status_label} — reklama ko'rsatilmaydi"
        share = account.cap_share
        if share is not None and share >= CAP_WARN:
            current["cap"] = f"Kabinet xarajat chegarasining {share:.0%} i ishlatildi — chegaraga yetganda reklama to'xtaydi"
        for obj in problems:
            text = f"{LEVEL_LABELS.get(obj.level, obj.level)} «{obj.name}» — {obj.status_label}"
            current[f"{obj.level}:{obj.id}:{obj.status}"] = text + (f": {obj.issue[:150]}" if obj.issue else "")
        db = self.ctx.db
        try:
            seen = json.loads(await db.get_config(PROBLEMS_KEY) or "{}")
        except ValueError:
            seen = {}
        new = [key for key in current if key not in seen]
        await db.set_config(PROBLEMS_KEY, json.dumps({key: seen.get(key, iso(now)) for key in current}))
        if not new:
            return 0
        lines = ["⚠️ <b>Meta reklama: muammo</b>"] + [f"• {esc(current[key])}" for key in new[:PROBLEMS_SHOWN]]
        if len(new) > PROBLEMS_SHOWN:
            lines.append(f"… va yana {len(new) - PROBLEMS_SHOWN} ta")
        lines.append("Batafsil: /reklama · Ads Manager'da tekshiring")
        await self.reports._tell_owners(bot, "\n".join(lines))
        return len(new)

    async def _warn_auth(self, bot: Any, now: datetime, reason: str) -> None:
        db = self.ctx.db
        last = await db.get_config(AUTH_KEY)
        if last and last > iso(now - AUTH_REPEAT):
            return
        await db.set_config(AUTH_KEY, iso(now))
        await self.reports._tell_owners(bot, f"⚠️ <b>Meta Ads</b>: {esc(reason)}")

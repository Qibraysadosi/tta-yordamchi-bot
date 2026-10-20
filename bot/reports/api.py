"""Mini App «Hisobotlar» bo'limi uchun JSON API (/api/report/...).

Kirish webapp.py dagi middleware'da tekshiriladi: o'qish — egasi va rahbarlar, o'zgartirish — faqat egasi."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..models import label
from ..prompt import MONTHS, WEEKDAYS
from .service import ROLE_LABELS, ReportService
from .stats import OTHER, Overview, Row, current_day, neighbour_day
from .store import PEOPLE_KINDS, ROLES, clean_username, iso

if TYPE_CHECKING:
    from aiogram import Bot

    from ..handlers import AppContext

log = logging.getLogger(__name__)

STATUS_EMOJI_LIMIT = 8
STATUS_LABEL_LIMIT = 30
NAME_LIMIT = 40


def _json(data: Any, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False), status=status, content_type="application/json",
                        charset="utf-8")


def _error(message: str, status: int = 400, code: str = "error") -> web.Response:
    return _json({"error": code, "message": message}, status)


def _day_label(day: date) -> str:
    return f"{day.day}-{MONTHS[day.month - 1]}, {WEEKDAYS[day.weekday()]}"


def _row(ov: Overview, row: Row) -> dict:
    data = row.as_dict()
    data["positive"] = ov.positive(row)
    return data


class ReportAPI:
    def __init__(self, ctx: "AppContext", bot: "Bot", uid_key: Any, user_key: Any, access_key: Any) -> None:
        self.ctx = ctx
        self.bot = bot
        self.uid_key, self.user_key, self.access_key = uid_key, user_key, access_key

    @property
    def service(self) -> ReportService:
        return self.ctx.reports

    def routes(self, add) -> None:
        add("GET", "/api/report/overview", self.overview)
        add("GET", "/api/report/people", self.people)
        add("POST", "/api/report/people", self.add_person)
        add("PATCH", "/api/report/people/{person_id}", self.update_person)
        add("DELETE", "/api/report/people/{person_id}", self.delete_person)
        add("GET", "/api/report/settings", self.settings)
        add("PATCH", "/api/report/groups/{chat_id}", self.update_group)
        add("POST", "/api/report/statuses", self.add_status)
        add("PATCH", "/api/report/statuses/{status_id}", self.update_status)
        add("DELETE", "/api/report/statuses/{status_id}", self.delete_status)
        add("PATCH", "/api/report/schedule", self.update_schedule)
        add("POST", "/api/report/send", self.send_now)

    async def _body(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="JSON kutilgan edi")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ ko'rish (egasi va rahbarlar)
    async def glance(self) -> dict | None:
        """Tanlov ekrani uchun qisqa ko'rsatkichlar (joriy davr)."""
        service = self.service
        if service is None or not await service.store.groups_with("leads"):
            return None
        ov = await service.overview()
        everyone = [ov.totals] + ov.extras  # ro'yxatdagilar jami + ro'yxatda yo'qlar (Bugun ekrani bilan bir xil)
        return {"calls": sum(r.total for r in everyone), "positive": sum(ov.positive(r) for r in everyone),
                "positive_emoji": next((s.emoji for s in ov.statuses if s.positive), "👍"),
                "new_leads": ov.new_leads, "unmarked": ov.unmarked}

    async def overview(self, request: web.Request) -> web.Response:
        service = self.service
        await service.ensure()
        now = service.now()
        schedule = await service.schedule()
        today = current_day(now, schedule)
        day = today
        raw = (request.query.get("day") or "").strip()
        if raw:
            try:
                day = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                return _error("Sana noto'g'ri (YYYY-MM-DD)")
            if day > today:
                day = today
        ov = await service.overview(day, now)
        report = await service.store.report(ov.end.strftime("%Y-%m-%d %H:%M"))
        reasons: list[tuple[str, int]] = ov.reasons[:5]
        clustered = False
        if report and report.get("insights"):
            try:
                stored = json.loads(report["insights"])
                if isinstance(stored, list) and stored:
                    reasons = [(str(x[0]), int(x[1])) for x in stored]
                    clustered = True
            except (ValueError, TypeError, IndexError):
                pass
        access = request[self.access_key]
        return _json({
            "day": day.isoformat(), "label": _day_label(day),
            "start": ov.start.strftime("%Y-%m-%dT%H:%M"), "end": ov.end.strftime("%Y-%m-%dT%H:%M"),
            "live": ov.live, "prev": neighbour_day(day, schedule, -1).isoformat(),
            "next": neighbour_day(day, schedule, 1).isoformat() if day < today else None,
            "statuses": [{"id": s.id, "emoji": s.emoji, "label": s.label, "positive": s.positive} for s in ov.statuses],
            "columns": ov.columns(), "other": OTHER,
            "rows": [_row(ov, r) for r in ov.rows], "extras": [_row(ov, r) for r in ov.extras],
            "totals": _row(ov, ov.totals), "has_operator_list": ov.has_operator_list,
            "leads": {"new": ov.new_leads, "unmarked": ov.unmarked},
            "courses": [{"course": c, "total": t, "positive": p} for c, t, p in ov.courses[:8]],
            "conflicts": ov.conflicts, "comments": ov.comments, "pending": ov.pending, "callbacks": ov.callbacks,
            "reasons": [{"label": r, "count": n} for r, n in reasons], "reasons_grouped": clustered,
            "sent": report is not None, "sent_at": report.get("sent_at") if report else None,
            "has_groups": bool(await service.store.groups_with("leads")),
            "schedule": {"time": schedule.time, "days": schedule.days_label()},
            "role": "owner" if access.get("owner") else "leader",
        })

    async def people(self, request: web.Request) -> web.Response:
        result: dict[str, list] = {"operator": [], "excluded": [], "leader": []}
        for person in await self.service.store.people():
            result.setdefault(person.kind, []).append({
                "id": person.id, "username": person.username, "user_id": person.user_id, "name": person.name,
                "label": person.label, "seen": person.user_id is not None, "dm_ok": person.dm_ok,
            })
        return _json({"operators": result["operator"], "excluded": result["excluded"], "leaders": result["leader"]})

    # ------------------------------------------------------------------ o'zgartirish (faqat egasi)
    async def add_person(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        kind = str(body.get("kind") or "")
        if kind not in PEOPLE_KINDS:
            return _error("Noma'lum ro'yxat")
        who = str(body.get("who") or "").strip()
        name = " ".join(str(body.get("name") or "").split())[:NAME_LIMIT]
        user_id: int | None = None
        username: str | None = None
        if who.lstrip("-").isdigit():
            user_id = int(who)
        else:
            username = clean_username(who)
            if username is None:
                return _error("Username noto'g'ri: @ bilan yoki usiz, 4–32 ta lotin harfi, raqam yoki _ (yoki Telegram ID)")
        store = self.service.store
        person = await store.add_person(kind, username=username, user_id=user_id, name=name)
        # operator va istisno bir-biriga zid: biriga qo'shilsa, ikkinchisidan olinadi
        opposite = {"operator": "excluded", "excluded": "operator"}.get(kind)
        if opposite:
            for other in await store.people(opposite):
                if (username and other.username == username) or (other.user_id and other.user_id == person.user_id):
                    await store.delete_person(other.id)
        return _json({"ok": True, "person": {"id": person.id, "label": person.label}})

    async def update_person(self, request: web.Request) -> web.Response:
        person = await self._person(request)
        if person is None:
            return _error("Topilmadi", 404, "not_found")
        body = await self._body(request)
        name = " ".join(str(body["name"]).split())[:NAME_LIMIT] if "name" in body else None
        await self.service.store.update_person(person.id, name=name)
        return _json({"ok": True})

    async def delete_person(self, request: web.Request) -> web.Response:
        person = await self._person(request)
        if person is None or not await self.service.store.delete_person(person.id):
            return _error("Topilmadi", 404, "not_found")
        return _json({"ok": True})

    async def _person(self, request: web.Request):
        try:
            return await self.service.store.person(int(request.match_info["person_id"]))
        except ValueError:
            return None

    async def settings(self, request: web.Request) -> web.Response:
        service = self.service
        await service.ensure()
        store = service.store
        schedule = await service.schedule()
        since = iso(service.now() - timedelta(hours=24))
        operators = await store.people("operator")
        await service.refresh_bot_info(self.bot)  # BotFather'da Mini App yoqilgan bo'lsa — darrov ko'rinsin
        return _json({
            "groups": [
                {"chat_id": g.chat_id, "title": g.title, "roles": list(g.roles), "active": g.active,
                 "is_admin": g.is_admin, "last_seen": g.last_seen}
                for g in await store.groups()
            ],
            "role_labels": {k: v.split(" ", 1)[1] for k, v in ROLE_LABELS.items()},
            "statuses": [{"id": s.id, "emoji": s.emoji, "label": s.label, "positive": s.positive}
                         for s in await store.statuses()],
            "schedule": {"time": schedule.time, "days": list(schedule.days), "enabled": schedule.enabled,
                         "remind_minutes": schedule.remind_minutes, "work_start": schedule.work_start,
                         "work_end": schedule.work_end, "days_label": schedule.days_label()},
            "bot": {"username": service.bot_info.get("username"),
                    "has_main_web_app": bool(service.bot_info.get("has_main_web_app")),
                    "can_read_all": bool(service.bot_info.get("can_read_all"))},
            "model": label(self.ctx.settings.report_model),
            "last_report": await store.last_report(),
            "diag": await store.counts_since(since),
            "operators_dm": sum(1 for p in operators if p.dm_ok),
            "operators": len(operators),
        })

    async def update_group(self, request: web.Request) -> web.Response:
        try:
            chat_id = int(request.match_info["chat_id"])
        except ValueError:
            return _error("Guruh topilmadi", 404, "not_found")
        store = self.service.store
        group = await store.group(chat_id)
        if group is None:
            return _error("Guruh topilmadi", 404, "not_found")
        body = await self._body(request)
        if "roles" in body:
            roles = body["roles"] if isinstance(body["roles"], list) else []
            await store.set_group_roles(chat_id, [r for r in roles if r in ROLES])
        if "active" in body:
            await store.set_group_active(chat_id, bool(body["active"]))
        group = await store.group(chat_id)
        is_admin = group.is_admin if group else False
        if group is not None and group.has("leads"):
            checked = await self.service.check_admin(self.bot, chat_id)
            is_admin = bool(checked) if checked is not None else is_admin
        return _json({"ok": True, "roles": list(group.roles) if group else [], "active": bool(group and group.active),
                      "is_admin": is_admin})

    async def add_status(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        emoji = "".join(str(body.get("emoji") or "").split())
        text = " ".join(str(body.get("label") or "").split())
        if not emoji or len(emoji) > STATUS_EMOJI_LIMIT:
            return _error("Emoji kiriting (bitta belgi)")
        if not text or len(text) > STATUS_LABEL_LIMIT:
            return _error(f"Ma'nosini yozing ({STATUS_LABEL_LIMIT} belgigacha)")
        if any(s.emoji == emoji for s in await self.service.store.statuses()):
            return _error("Bu emoji allaqachon bor", 409, "exists")
        status = await self.service.store.add_status(emoji, text, bool(body.get("positive")))
        self.service.statuses_changed()
        return _json({"ok": True, "status": {"id": status.id, "emoji": status.emoji}})

    async def update_status(self, request: web.Request) -> web.Response:
        try:
            status_id = int(request.match_info["status_id"])
        except ValueError:
            return _error("Status topilmadi", 404, "not_found")
        body = await self._body(request)
        text = None
        if "label" in body:
            text = " ".join(str(body["label"] or "").split())
            if not text or len(text) > STATUS_LABEL_LIMIT:
                return _error(f"Ma'nosini yozing ({STATUS_LABEL_LIMIT} belgigacha)")
        position = None
        if "position" in body:
            try:
                position = int(body["position"])
            except (TypeError, ValueError):
                return _error("Tartib raqami noto'g'ri")
        positive = bool(body["positive"]) if "positive" in body else None
        if not await self.service.store.update_status(status_id, label=text, positive=positive, position=position):
            return _error("Status topilmadi", 404, "not_found")
        self.service.statuses_changed()
        return _json({"ok": True})

    async def delete_status(self, request: web.Request) -> web.Response:
        try:
            status_id = int(request.match_info["status_id"])
        except ValueError:
            return _error("Status topilmadi", 404, "not_found")
        if not await self.service.store.delete_status(status_id):
            return _error("Status topilmadi", 404, "not_found")
        self.service.statuses_changed()
        return _json({"ok": True})

    async def update_schedule(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        if "days" in body and (not isinstance(body["days"], list) or not body["days"]):
            return _error("Kamida bitta kunni tanlang")
        allowed = {k: body[k] for k in ("time", "days", "enabled", "remind_minutes", "work_start", "work_end") if k in body}
        if not allowed:
            return _error("O'zgartiriladigan sozlama yo'q")
        schedule = await self.service.save_schedule(allowed)
        return _json({"ok": True, "schedule": {
            "time": schedule.time, "days": list(schedule.days), "enabled": schedule.enabled,
            "remind_minutes": schedule.remind_minutes, "work_start": schedule.work_start,
            "work_end": schedule.work_end, "days_label": schedule.days_label(),
        }})

    async def send_now(self, request: web.Request) -> web.Response:
        service = self.service
        if not await service.store.groups_with("leads"):
            return _error("Hali lidlar guruhi yo'q — botni guruhga qo'shing", 409, "no_groups")
        now = service.now()
        day = current_day(now, await service.schedule())
        sent = await service.send_report(self.bot, day, now, interim=True)
        leaders = await service.store.groups_with("leaders")
        return _json({"ok": True, "sent": sent, "target": "leaders" if leaders else "owner"})

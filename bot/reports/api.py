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
from .tasks import CANCELLED, DONE, OPEN, REPEAT_RE, TEXT_LIMIT, Task, default_due, due_label, repeat_label

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
        add("GET", "/api/report/tasks", self.tasks_list)
        add("POST", "/api/report/tasks", self.add_task)
        add("GET", "/api/report/tasks/{task_id}", self.task_detail)
        add("PATCH", "/api/report/tasks/{task_id}", self.update_task)
        add("POST", "/api/report/tasks/{task_id}/ask", self.ask_task)

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
        counts = await service.tasks.store.task_counts(iso(service.now()))
        return {"tasks_open": counts["open"], "tasks_overdue": counts["overdue"],
                "calls": sum(r.total for r in everyone), "positive": sum(ov.positive(r) for r in everyone),
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
        report = await service.store.report_for_day(day.isoformat())
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
            "tasks": await service.tasks.store.task_counts(iso(now)),
            "schedule": {"time": schedule.time, "days": schedule.days_label()},
            "role": "owner" if access.get("owner") else "leader",
        })

    async def people(self, request: web.Request) -> web.Response:
        owner = bool(request[self.access_key].get("owner"))
        result: dict[str, list] = {"operator": [], "excluded": [], "leader": []}
        for person in await self.service.store.people():
            if not owner and person.kind != "operator":
                continue  # rahbar faqat operatorlarni ko'radi (istisnolar va rahbarlar ro'yxati — egasiga)
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
        if group is not None and (group.has("leads") or group.has("tasks")):  # ikkalasida ham bot xabarlarni o'qishi kerak
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
        candidate = (await self.service.schedule()).updated(allowed)
        if candidate.work_end <= candidate.work_start:
            return _error("Ish vaqti tugashi boshlanishidan keyin bo'lsin (masalan, 09:00–19:00)")
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
        fallback = service.last_fallback  # keyingi await'dan oldin: parallel kunlik yuborish o'zgartirmasin
        if not sent:
            return _error("Hisobot yuborilmadi: Telegram bilan aloqa yo'q. Birozdan keyin qayta urinib ko'ring.", 502, "send_failed")
        leaders = await service.store.groups_with("leaders")
        target = "leaders" if leaders and not fallback else "owner"
        return _json({"ok": True, "sent": sent, "target": target})

    # ------------------------------------------------------------------ vazifalar
    def _task(self, task: Task, now: datetime, groups: dict[int, str]) -> dict:
        tasks = self.service.tasks
        due = tasks.local(task.due_at)
        closed = tasks.local(task.done_at)
        created = tasks.local(task.created_at)
        return {
            "id": task.id, "text": task.text, "who": task.who, "username": task.assignee_username,
            "user_id": task.assignee_id, "due": due.strftime("%Y-%m-%dT%H:%M") if due else None,
            "due_label": due_label(due) if due else None, "due_set": task.due_set, "repeat": task.repeat or "",
            "repeat_label": repeat_label(task.repeat), "status": task.status, "state": tasks.state_of(task, now),
            "asks": task.asks, "no_count": task.no_count, "group": groups.get(task.poll_chat) if task.poll_chat else None,
            "created_label": due_label(created) if created else None, "closed_label": due_label(closed) if closed else None,
        }

    async def _groups(self) -> dict[int, str]:
        return {g.chat_id: g.title or str(g.chat_id) for g in await self.service.store.groups()}

    async def _task_or_404(self, request: web.Request) -> Task | None:
        try:
            return await self.service.tasks.store.get(int(request.match_info["task_id"]))
        except ValueError:
            return None

    async def tasks_list(self, request: web.Request) -> web.Response:
        service = self.service
        await service.ensure()
        status = request.query.get("status", OPEN)
        if status not in (OPEN, DONE, CANCELLED, "all"):
            status = OPEN
        now = service.now()
        groups = await self._groups()
        items = await service.tasks.store.tasks(None if status == "all" else status, 200)
        payload: dict[str, Any] = {
            "status": status, "items": [self._task(t, now, groups) for t in items],
            "counts": await service.tasks.store.task_counts(iso(now)),
            "has_group": bool(await service.store.groups_with("tasks")),
        }
        if request[self.access_key].get("owner"):
            schedule = await service.schedule()
            payload["people"] = await service.tasks.store.known_people()
            payload["default_due"] = default_due(now, schedule).strftime("%Y-%m-%dT%H:%M")
        return _json(payload)

    async def task_detail(self, request: web.Request) -> web.Response:
        task = await self._task_or_404(request)
        if task is None:
            return _error("Vazifa topilmadi", 404, "not_found")
        tasks = self.service.tasks
        data = self._task(task, self.service.now(), await self._groups())
        labels = {"created": "qayd etildi", "asked": "«Bajardingizmi?» so'raldi", "yes": "✅ bajarildi",
                  "no": "❌ «Yo'q» dedi", "escalated": "⚠️ javob bermadi — sizga xabar", "due": "⏰ muddat o'zgardi",
                  "cancelled": "🗑 bekor qilindi", "reopened": "↩️ qayta ochildi", "nogroup": "⚠️ guruh yo'q edi",
                  "sendfail": "⚠️ guruhga yozib bo'lmadi", "norepeat": "🔁 takror to'xtatildi"}
        data["history"] = [
            {"label": labels.get(h["kind"], h["kind"]), "at": due_label(moment) if (moment := tasks.local(h["created_at"])) else ""}
            for h in await tasks.store.history(task.id)
        ]
        return _json(data)

    async def add_task(self, request: web.Request) -> web.Response:
        service = self.service
        body = await self._body(request)
        who = str(body.get("who") or "").strip()
        text = " ".join(str(body.get("text") or "").split())
        if not text or len(text) > TEXT_LIMIT:
            return _error(f"Vazifa matnini yozing ({TEXT_LIMIT} belgigacha)")
        if who.lstrip("-").isdigit():
            person = await service.tasks.resolve(int(who), None)
        else:
            username = clean_username(who)
            if username is None:
                return _error("Kimga: @username yoki Telegram ID")
            person = await service.tasks.resolve(None, username)
        now = service.now()
        due = self._parse_due(body.get("due"), now)
        if isinstance(due, web.Response):
            return due
        repeat = str(body.get("repeat") or "").strip()
        if repeat and not REPEAT_RE.fullmatch(repeat):
            return _error("Takror noto'g'ri")
        poll_chat = await service.tasks.pick_group(self.bot, person)
        task = await service.tasks.create(creator_id=request[self.uid_key], assignee=person, text=text, due=due,
                                          repeat=repeat or None, poll_chat=poll_chat, now=now)
        return _json({"ok": True, "task": self._task(task, now, await self._groups())})

    @staticmethod
    def _parse_due(raw: Any, now: datetime) -> datetime | None | web.Response:
        value = str(raw or "").strip()
        if not value:
            return None
        try:
            due = datetime.strptime(value[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=now.tzinfo)
        except ValueError:
            return _error("Muddat noto'g'ri")
        if due < now - timedelta(minutes=5):
            return _error("Muddat o'tib ketgan vaqt bo'lmasin")
        return due

    async def update_task(self, request: web.Request) -> web.Response:
        task = await self._task_or_404(request)
        if task is None:
            return _error("Vazifa topilmadi", 404, "not_found")
        service = self.service
        tasks = service.tasks
        body = await self._body(request)
        now = service.now()
        uid = request[self.uid_key]
        if "text" in body:
            text = " ".join(str(body["text"] or "").split())
            if not text or len(text) > TEXT_LIMIT:
                return _error(f"Vazifa matnini yozing ({TEXT_LIMIT} belgigacha)")
            await tasks.store.update(task.id, text=text)
        if "repeat" in body:
            repeat = str(body["repeat"] or "").strip()
            if repeat and not REPEAT_RE.fullmatch(repeat):
                return _error("Takror noto'g'ri")
            if repeat:
                await tasks.store.update(task.id, repeat=repeat)
            else:
                await tasks.stop_repeat(task, uid)
        if body.get("due"):
            due = self._parse_due(body["due"], now)
            if isinstance(due, web.Response):
                return due
            if due is not None:
                await tasks.set_due(task, due, uid)
        status = body.get("status")
        task = await tasks.store.get(task.id) or task
        if status == DONE and task.status == OPEN:
            await tasks.close(self.bot, task, uid, now)
        elif status == CANCELLED and task.status == OPEN:
            await tasks.cancel(self.bot, task, uid, now)
        elif status == OPEN and task.status != OPEN:
            await tasks.reopen(task, uid, now)
        task = await tasks.store.get(task.id) or task
        return _json({"ok": True, "task": self._task(task, now, await self._groups())})

    async def ask_task(self, request: web.Request) -> web.Response:
        task = await self._task_or_404(request)
        if task is None:
            return _error("Vazifa topilmadi", 404, "not_found")
        if task.status != OPEN:
            return _error("Vazifa yopilgan", 409, "closed")
        if not await self.service.tasks.ask_now(self.bot, task, self.service.now()):
            return _error("So'rov yuborilmadi: «📋 Vazifalar» guruhi yo'q yoki bot u yerga yoza olmaydi", 409, "not_sent")
        return _json({"ok": True})

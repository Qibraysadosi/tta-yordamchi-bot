"""Mini App «Hisobotlar» bo'limi uchun JSON API (/api/report/...).

Kirish webapp.py dagi middleware'da tekshiriladi: o'qish — egasi va rahbarlar, o'zgartirish — faqat egasi."""
from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiohttp import web

from ..models import label
from ..prompt import MONTHS, WEEKDAYS
from .archive import MONTH, WEEK, parse_key
from .excel import hide_numbers
from .service import ROLE_LABELS, ReportService
from .stats import OTHER, Overview, Row, current_day, display_day, neighbour_day, period_bounds
from .store import PEOPLE_KINDS, ROLES, clean_username, iso
from .tasks import CANCELLED, DONE, EVENT_LABELS, OPEN, REPEAT_RE, TEXT_LIMIT, Task, default_due, due_label, repeat_label

if TYPE_CHECKING:
    from aiogram import Bot

    from ..handlers import AppContext

log = logging.getLogger(__name__)

ARCHIVE_KINDS = {"daily", "weekly", "monthly", "journal"}
SCHEDULE_FIELDS = ("time", "days", "enabled", "remind_minutes", "work_start", "work_end", "journal_enabled",
                   "journal_time", "weekly_enabled", "monthly_enabled", "excel_time")
TAG_RE = re.compile(r"<[^>]+>")
SEND_COOLDOWN = 15.0  # soniya: bir odam Excel'ni shuncha vaqtda bir marta oladi (har so'rov faylni qaytadan yasaydi)
STATUS_EMOJI_LIMIT = 8
STATUS_LABEL_LIMIT = 30
NAME_LIMIT = 40
MIN_DAY = date(2000, 1, 1)  # undan oldingi sana — xato (yil 1 kabi qiymatlar hisobni buzmasin)


def _same(text: str) -> str:
    return text


def _json(data: Any, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False), status=status, content_type="application/json",
                        charset="utf-8")


def _error(message: str, status: int = 400, code: str = "error") -> web.Response:
    return _json({"error": code, "message": message}, status)


def _day_label(day: date) -> str:
    return f"{day.day}-{MONTHS[day.month - 1]}, {WEEKDAYS[day.weekday()]}"


def _plain(text: str) -> str:
    """Telegram HTML → oddiy matn (Mini App'da xavfsiz ko'rsatish uchun)."""
    return html.unescape(TAG_RE.sub("", text or ""))


def _schedule(schedule) -> dict:
    data = schedule.as_dict()
    data["days_label"] = schedule.days_label()
    return data


def _masked_summary(summary: dict | None) -> dict | None:
    """Arxiv ko'rsatkichlaridagi operator ismlari — rahbar uchun raqamlarsiz (asl lug'at o'zgarmaydi)."""
    if not summary or not summary.get("top"):
        return summary
    return dict(summary, top=[[hide_numbers(str(item[0])), *item[1:]] for item in summary["top"]])


def _row(ov: Overview, row: Row, show: Callable[[str], str] = _same) -> dict:
    data = row.as_dict()
    data["positive"] = ov.positive(row)
    for key in ("name", "username"):
        if data.get(key):
            data[key] = show(data[key])
    return data


class ReportAPI:
    def __init__(self, ctx: "AppContext", bot: "Bot", uid_key: Any, user_key: Any, access_key: Any) -> None:
        self.ctx = ctx
        self.bot = bot
        self.uid_key, self.user_key, self.access_key = uid_key, user_key, access_key
        self._sent_at: dict[int, float] = {}  # Excel olgan odam → oxirgi muvaffaqiyatli yuborish (monotonic)
        self._building: set[int] = set()  # hozir fayli yasalayotganlar

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
        add("GET", "/api/report/archive", self.archive_list)
        add("GET", "/api/report/archive/{key}", self.archive_detail)
        add("POST", "/api/report/archive/{key}/send", self.archive_send)

    async def _body(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="JSON kutilgan edi")
        return data if isinstance(data, dict) else {}

    def _show(self, request: web.Request) -> Callable[[str], str]:
        """Rahbarga odamlar yozgan matnlardagi telefon raqamlari yashiriladi (Excel nusxasi bilan bir xil)."""
        return _same if request[self.access_key].get("owner") else hide_numbers

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
        today = current_day(now, schedule)  # joriy (davom etayotgan) davr — eng keyingi ko'rsatiladigan kun
        day = display_day(now, schedule)  # 19:00 dan keyin — hozirgina tugagan bugungi davr
        raw = (request.query.get("day") or "").strip()
        if raw:
            try:
                day = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                return _error("Sana noto'g'ri (YYYY-MM-DD)")
            if day < MIN_DAY:
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
        show = self._show(request)
        return _json({
            "day": day.isoformat(), "label": _day_label(day),
            "start": ov.start.strftime("%Y-%m-%dT%H:%M"), "end": ov.end.strftime("%Y-%m-%dT%H:%M"),
            "live": ov.live, "prev": neighbour_day(day, schedule, -1).isoformat(),
            "next": neighbour_day(day, schedule, 1).isoformat() if day < today else None,
            "statuses": [{"id": s.id, "emoji": s.emoji, "label": s.label, "positive": s.positive} for s in ov.statuses],
            "columns": ov.columns(), "other": OTHER,
            "rows": [_row(ov, r, show) for r in ov.rows], "extras": [_row(ov, r, show) for r in ov.extras],
            "totals": _row(ov, ov.totals), "has_operator_list": ov.has_operator_list,
            "leads": {"new": ov.new_leads, "unmarked": ov.unmarked},
            "courses": [{"course": show(c), "total": t, "positive": p} for c, t, p in ov.courses[:8]],
            "conflicts": ov.conflicts, "comments": ov.comments, "pending": ov.pending, "callbacks": ov.callbacks,
            "reasons": [{"label": show(r), "count": n} for r, n in reasons], "reasons_grouped": clustered,
            "sent": report is not None, "sent_at": report.get("sent_at") if report else None,
            "has_groups": bool(await service.store.groups_with("leads")),
            "tasks": await service.tasks.store.task_counts(iso(now)),
            "schedule": {"time": schedule.time, "days": schedule.days_label()},
            "role": "owner" if access.get("owner") else "leader",
        })

    async def people(self, request: web.Request) -> web.Response:
        owner = bool(request[self.access_key].get("owner"))
        show = self._show(request)
        result: dict[str, list] = {"operator": [], "excluded": [], "leader": []}
        for person in await self.service.store.people():
            if not owner and person.kind != "operator":
                continue  # rahbar faqat operatorlarni ko'radi (istisnolar va rahbarlar ro'yxati — egasiga)
            result.setdefault(person.kind, []).append({
                "id": person.id, "username": person.username, "user_id": person.user_id,
                "name": show(person.name) if person.name else person.name,
                "label": show(person.label), "seen": person.user_id is not None, "dm_ok": person.dm_ok,
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
        before = {p.id for p in await store.people(kind)}
        person = await store.add_person(kind, username=username, user_id=user_id, name=name)
        if kind in ("operator", "leader") and person.id not in before and person.user_id is not None:
            await self.service.welcome(self.bot, person)  # botni avval ochgan bo'lsa — darrov ulanadi (eslatmalar)
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
            "schedule": _schedule(schedule),
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
        allowed = {k: body[k] for k in SCHEDULE_FIELDS if k in body}
        if not allowed:
            return _error("O'zgartiriladigan sozlama yo'q")
        candidate = (await self.service.schedule()).updated(allowed)
        if candidate.work_end <= candidate.work_start:
            return _error("Ish vaqti tugashi boshlanishidan keyin bo'lsin (masalan, 09:00–19:00)")
        schedule = await self.service.save_schedule(allowed)
        return _json({"ok": True, "schedule": _schedule(schedule)})

    async def send_now(self, request: web.Request) -> web.Response:
        service = self.service
        if not await service.store.groups_with("leads"):
            return _error("Hali lidlar guruhi yo'q — botni guruhga qo'shing", 409, "no_groups")
        now = service.now()
        schedule = await service.schedule()
        day = display_day(now, schedule)  # 19:00 dan keyin — bugungi (tugagan) hisobot, keyingi bo'sh davr emas
        live = now < period_bounds(day, schedule, service.tz)[1]
        # tugagan, lekin hali yuborilmagan kun (19:00–19:02) — shu yuborish kunlik hisobot bo'ladi (takror kelmaydi)
        record = not live and not await service.store.report_for_day(day.isoformat())
        sent = await service.send_report(self.bot, day, now, interim=live, record=record)
        fallback = service.last_fallback  # keyingi await'dan oldin: parallel kunlik yuborish o'zgartirmasin
        if not sent:
            return _error("Hisobot yuborilmadi: Telegram bilan aloqa yo'q. Birozdan keyin qayta urinib ko'ring.", 502, "send_failed")
        leaders = await service.store.groups_with("leaders")
        target = "leaders" if leaders and not fallback else "owner"
        return _json({"ok": True, "sent": sent, "target": target})

    # ------------------------------------------------------------------ vazifalar
    def _task(self, task: Task, now: datetime, groups: dict[int, str], show: Callable[[str], str] = _same) -> dict:
        tasks = self.service.tasks
        due = tasks.local(task.due_at)
        closed = tasks.local(task.done_at)
        created = tasks.local(task.created_at)
        group = groups.get(task.poll_chat) if task.poll_chat else None
        return {
            "id": task.id, "text": show(task.text), "who": show(task.who), "username": task.assignee_username,
            "user_id": task.assignee_id, "due": due.strftime("%Y-%m-%dT%H:%M") if due else None,
            "due_label": due_label(due) if due else None, "due_set": task.due_set, "repeat": task.repeat or "",
            "repeat_label": repeat_label(task.repeat), "status": task.status, "state": tasks.state_of(task, now),
            "asks": task.asks, "no_count": task.no_count, "group": show(group) if group else None,
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
            "status": status, "items": [self._task(t, now, groups, self._show(request)) for t in items],
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
        data = self._task(task, self.service.now(), await self._groups(), self._show(request))
        labels = dict(EVENT_LABELS, escalated="⚠️ javob bermadi — sizga xabar")
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

    # ------------------------------------------------------------------ arxiv (v1.9)
    async def archive_list(self, request: web.Request) -> web.Response:
        service = self.service
        await service.ensure()
        owner = bool(request[self.access_key].get("owner"))
        kind = request.query.get("kind", "weekly")
        if kind not in ARCHIVE_KINDS:
            kind = "weekly"
        if kind == "journal" and not owner:
            return _error("Kundalik faqat bot egasiga ko'rinadi.", 403, "forbidden")
        archive = service.archive
        now = service.now()
        items: list[dict] = []
        if kind == "daily":
            for report in await archive.store.daily_reports():
                try:
                    day = datetime.strptime(report["period_end"][:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                sent = archive.local(report["sent_at"])
                items.append({"key": day.isoformat(), "label": _day_label(day),
                              "sent_label": f"{sent:%d.%m %H:%M}" if sent else None})
        elif kind == "journal":
            for entry in await archive.store.journals():
                day = datetime.strptime(entry["day"], "%Y-%m-%d").date()
                sent = archive.local(entry["sent_at"])
                items.append({"key": f"journal:{entry['day']}", "label": _day_label(day),
                              "sent_label": f"{sent:%d.%m %H:%M}" if sent else None})
        else:
            items = await archive.periods(WEEK if kind == "weekly" else MONTH, now)
            if not owner:
                items = [dict(item, summary=_masked_summary(item.get("summary"))) for item in items]
        schedule = await service.schedule()
        return _json({"kind": kind, "items": items, "schedule": _schedule(schedule), "owner": owner,
                      "has_groups": bool(await service.store.groups_with("leads")),
                      "leaders_group": bool(await service.store.groups_with("leaders"))})

    async def archive_detail(self, request: web.Request) -> web.Response:
        service = self.service
        key = request.match_info["key"]
        if key.startswith("journal:"):
            if not request[self.access_key].get("owner"):
                return _error("Kundalik faqat bot egasiga ko'rinadi.", 403, "forbidden")
            entry = await service.archive.store.journal(key.partition(":")[2])
            if entry is None:
                return _error("Kundalik topilmadi", 404, "not_found")
            day = datetime.strptime(entry["day"], "%Y-%m-%d").date()
            return _json({"key": key, "kind": "journal", "label": _day_label(day), "text": _plain(entry["text"])})
        await service.ensure()
        period = await self._period(key)
        if period is None:
            return _error("Davr topilmadi", 404, "not_found")
        data = await service.archive.detail(*period, service.now())
        owner = bool(request[self.access_key].get("owner"))
        if not owner:
            data["summary"] = _masked_summary(data["summary"])
        data["masked"] = not owner
        return _json(data)

    async def archive_send(self, request: web.Request) -> web.Response:
        """Excel faylni so'ragan odamning o'ziga (botdagi chatiga): egasiga to'liq, rahbarga telefonlarsiz."""
        service = self.service
        await service.ensure()
        period = await self._period(request.match_info["key"])
        if period is None:
            return _error("Davr topilmadi", 404, "not_found")
        uid = request[self.uid_key]
        if uid in self._building:
            return _error("Fayl tayyorlanmoqda — bir oz kuting.", 429, "busy")
        if time.monotonic() - self._sent_at.get(uid, -SEND_COOLDOWN) < SEND_COOLDOWN:
            return _error("Fayl hozirgina yuborildi — chatni tekshiring yoki birozdan keyin qayta urining.", 429, "too_fast")
        owner = bool(request[self.access_key].get("owner"))
        self._building.add(uid)  # bir odam bir vaqtda bitta faylni yasaydi
        try:
            await service.archive.send_to(self.bot, uid, *period, service.now(), masked=not owner)
        except TelegramForbiddenError:
            return _error("Bot sizga yoza olmaydi: botni oching va /start bosing, keyin qayta urinib ko'ring.", 409,
                          "blocked")
        except TelegramAPIError as exc:
            log.warning("Excel yuborilmadi: %s", exc)
            return _error("Fayl yuborilmadi: Telegram bilan aloqa yo'q. Birozdan keyin qayta urinib ko'ring.", 502,
                          "send_failed")
        except Exception:  # noqa: BLE001 — xato foydalanuvchiga tushunarli, tafsiloti logda
            log.exception("Excel tayyorlanmadi (%s)", request.match_info["key"])
            return _error("Faylni tayyorlab bo'lmadi. Birozdan keyin qayta urinib ko'ring.", 500, "build_failed")
        finally:
            self._building.discard(uid)
        self._sent_at[uid] = time.monotonic()  # faqat muvaffaqiyatli yuborilgandan keyin
        return _json({"ok": True, "masked": not owner})

    async def _period(self, key: str) -> tuple[str, date, date] | None:
        """Kalit → davr; faqat kuzatish boshlangandan bugungacha bo'lgan davrlar (boshqasi — topilmadi)."""
        period = parse_key(key)
        if period is None:
            return None
        service = self.service
        since = (await service.since()).astimezone(service.tz).date()
        _, first, last = period
        return period if last >= since and first <= service.now().date() else None

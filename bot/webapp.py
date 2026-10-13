"""Telegram Mini App: HTTPS sahifa (bot/miniapp/) va JSON API (aiohttp).

Kirish: har so'rovda `Authorization: tma <initData>` sarlavhasi — Telegram imzosi bot tokeni bilan
tekshiriladi, foydalanuvchi egasi yoki parol bilan kirgan a'zo bo'lishi kerak. Server faqat konteyner
ichidagi portda tinglaydi; tashqaridan unga Caddy/Nginx (HTTPS) orqali kiriladi (README → Mini App)."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import html
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from aiohttp import web

from . import scheduler
from .access import MIN_PASSWORD_LENGTH, hash_password
from .config import EFFORT_LEVELS
from .github import GitHubError
from .handlers import (
    PASSWORD_KEY,
    AppContext,
    UpdateCancelled,
    _confirm_keyboard,
    billing_cycle_start,
    github_check_text,
    run_chat_for,
    stage_from_github,
    update_summary,
)
from .models import AUTO_MODEL, MODELS, SELECTABLE_MODELS, label
from .modes import MODES
from .updater import UpdateError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "miniapp"
INIT_DATA_MAX_AGE = 24 * 3600
LINK_TTL = 15 * 60
STATS_DAYS = 30
ASK_LIMIT = 8000

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
UID = web.RequestKey("uid", int) if hasattr(web, "RequestKey") else "uid"  # type: ignore[assignment]
USER = web.RequestKey("user", dict) if hasattr(web, "RequestKey") else "user"  # type: ignore[assignment]


class AuthError(Exception):
    pass


# ------------------------------------------------------------------ Telegram initData tekshiruvi
def validate_init_data(init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE, now: float | None = None) -> dict:
    """Telegram.WebApp.initData satrini tekshiradi; to'g'ri bo'lsa maydonlar lug'atini qaytaradi."""
    if not init_data or len(init_data) > 8192:
        raise AuthError("initData yo'q")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    fields = dict(pairs)
    received = fields.pop("hash", None)
    if not received:
        raise AuthError("hash yo'q")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    signing_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(signing_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("imzo noto'g'ri")
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise AuthError("auth_date noto'g'ri") from exc
    if (now if now is not None else time.time()) - auth_date > max_age:
        raise AuthError("sessiya eskirgan — ilovani qayta oching")
    try:
        user = json.loads(fields.get("user") or "{}")
    except json.JSONDecodeError as exc:
        raise AuthError("user maydoni noto'g'ri") from exc
    if not isinstance(user, dict) or "id" not in user:
        raise AuthError("foydalanuvchi aniqlanmadi")
    fields["user"] = user
    return fields


def _json(data: Any, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False), status=status, content_type="application/json", charset="utf-8"
    )


def _error(message: str, status: int = 400, code: str = "error") -> web.Response:
    return _json({"error": code, "message": message}, status)


def _when(stamp: str, tz: ZoneInfo) -> str:
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    except ValueError:
        return stamp
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _message_preview(content: Any) -> tuple[str, list[str]]:
    """Saqlangan xabar bloklaridan matn va biriktirmalar nomi."""
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    attachments: list[str] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "image":
            attachments.append("🖼 rasm")
        elif kind == "document":
            attachments.append(f"📎 {block.get('title') or 'hujjat'}")
    return "\n".join(texts).strip(), attachments


class WebApp:
    def __init__(self, ctx: AppContext, bot: Bot) -> None:
        self.ctx = ctx
        self.bot = bot
        self.tz = ZoneInfo(ctx.settings.timezone)
        self.app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[self.auth_middleware])
        self._routes()

    # ------------------------------------------------------------------ marshrutlar
    def _routes(self) -> None:
        add = self.app.router.add_route
        add("GET", "/", self.index)
        add("GET", "/index.html", self.index)
        add("GET", "/health", self.health)
        add("GET", "/api/me", self.me)
        add("PATCH", "/api/settings", self.patch_settings)
        add("POST", "/api/conversations/new", self.new_conversation)
        add("GET", "/api/conversations", self.conversations)
        add("GET", "/api/conversations/{conv_id}", self.conversation)
        add("POST", "/api/conversations/{conv_id}/resume", self.resume_conversation)
        add("POST", "/api/ask", self.ask)
        add("GET", "/api/files", self.files)
        add("POST", "/api/files/{file_id}/send", self.send_file)
        add("GET", "/api/files/{file_id}/link", self.file_link)
        add("DELETE", "/api/files/{file_id}", self.delete_file)
        add("GET", "/api/stats", self.stats)
        add("GET", "/api/templates", self.templates)
        add("POST", "/api/templates", self.add_template)
        add("DELETE", "/api/templates/{template_id}", self.delete_template)
        add("GET", "/api/admin", self.admin)
        add("POST", "/api/admin/password", self.admin_password)
        add("POST", "/api/admin/members/{member_id}", self.admin_member)
        add("POST", "/api/admin/backup", self.admin_backup)
        add("POST", "/api/admin/restore", self.admin_restore)
        add("POST", "/api/admin/github", self.admin_github)
        add("GET", "/files/{file_id}/download", self.download)
        self.app.router.add_static("/static", STATIC_DIR, show_index=False)

    # ------------------------------------------------------------------ kirish nazorati
    @web.middleware
    async def auth_middleware(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        if not request.path.startswith("/api/"):
            return await handler(request)
        header = request.headers.get("Authorization", "")
        scheme, _, init_data = header.partition(" ")
        if scheme.lower() != "tma":
            return _error("Ilova faqat Telegram ichida ochiladi.", 401, "unauthorized")
        try:
            fields = validate_init_data(init_data, self.ctx.settings.telegram_token)
        except AuthError as exc:
            return _error(f"Kirish tekshiruvi o'tmadi: {exc}", 401, "unauthorized")
        user = fields["user"]
        uid = int(user["id"])
        if not self.ctx.is_owner(uid):
            member = await self.ctx.db.member(uid)
            if member is None:
                return _error("Avval botda parol bilan kiring, keyin ilovani oching.", 403, "no_access")
            if not member.active:
                return _error("Bot egasi sizning kirishingizni yopgan.", 403, "no_access")
        request[UID] = uid
        request[USER] = user
        if request.path.startswith("/api/admin") and not self.ctx.is_owner(uid):
            return _error("Faqat bot egasi uchun.", 403, "forbidden")
        await self.ctx.user_settings(uid)  # sozlamalar qatori bo'lmasa — yaratiladi (UPDATE'lar ishlashi uchun)
        return await handler(request)

    async def _body(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="JSON kutilgan edi")
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ sahifa
    async def index(self, request: web.Request) -> web.Response:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        version = self.ctx.updater.version if self.ctx.updater else "0"
        page = page.replace("{{version}}", html.escape(version))
        return web.Response(
            text=page, content_type="text/html", charset="utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline';"
                    " img-src 'self' data:; connect-src 'self'; font-src 'self'"
                ),
            },
        )

    async def health(self, request: web.Request) -> web.Response:
        return _json({"ok": True, "version": self.ctx.updater.version if self.ctx.updater else None})

    # ------------------------------------------------------------------ profil va sozlamalar
    async def _settings_payload(self, uid: int) -> dict:
        s = await self.ctx.user_settings(uid)
        return {
            "model": s.model, "effort": s.effort, "web_search": s.web_search, "show_cost": s.show_cost,
            "mode": s.mode or "oddiy", "conv_id": s.conv_id, "model_override": s.model_override,
        }

    async def me(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        user: dict = request[USER]
        ctx = self.ctx
        settings = await self._settings_payload(uid)
        now = ctx.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cycle_start = billing_cycle_start(now, ctx.settings.budget_reset_day)
        today, today_n = await ctx.db.spend(uid, day_start)
        cycle, _ = await ctx.db.spend(uid, cycle_start)
        all_cycle, _ = await ctx.db.spend_all(cycle_start)
        conversation = await ctx.db.conversation(uid, settings["conv_id"])
        budget = ctx.settings.monthly_budget
        files, files_total = await ctx.db.files(uid, limit=1)
        _, conv_total = await ctx.db.conversations(uid, limit=1)
        return _json({
            "user": {
                "id": uid, "name": user.get("first_name") or user.get("username") or "Foydalanuvchi",
                "username": user.get("username"), "is_owner": ctx.is_owner(uid),
            },
            "version": ctx.updater.version if ctx.updater else None,
            "settings": settings,
            "models": [{"id": AUTO_MODEL, "label": "Avto", "note": "savolga qarab arzon yoki kuchli model"}]
            + [
                {"id": m, "label": MODELS[m].label, "note": MODELS[m].note,
                 "price_in": MODELS[m].rates.input, "price_out": MODELS[m].rates.output}
                for m in SELECTABLE_MODELS
            ],
            "efforts": list(EFFORT_LEVELS),
            "modes": [{"key": m.key, "label": m.label, "emoji": m.emoji, "hint": m.hint} for m in MODES.values()],
            "spend": {"today": round(today, 4), "today_requests": today_n, "cycle": round(cycle, 4)},
            "budget": {
                "monthly": budget, "used_all": round(all_cycle, 4),
                "percent": round(all_cycle / budget * 100, 1) if budget > 0 else None,
                "economy_percent": ctx.settings.budget_economy_percent,
                "economy_model": label(ctx.settings.economy_model),
                "cycle_start": cycle_start.strftime("%Y-%m-%d"),
            },
            "conversation": (
                {"conv_id": conversation.conv_id, "title": conversation.title, "turns": conversation.turns}
                if conversation else {"conv_id": settings["conv_id"], "title": "Yangi suhbat", "turns": 0}
            ),
            "counts": {"conversations": conv_total, "files": files_total},
            "webapp_url": ctx.settings.webapp_url,
        })

    async def patch_settings(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        body = await self._body(request)
        fields: dict[str, Any] = {}
        if "model" in body:
            model = str(body["model"])
            if model != AUTO_MODEL and model not in SELECTABLE_MODELS:
                return _error("Noma'lum model")
            fields["model"] = model
            fields["model_override"] = None
        if "effort" in body:
            effort = body["effort"]
            if effort not in (None, "", "default") and effort not in EFFORT_LEVELS:
                return _error("Noma'lum fikrlash darajasi")
            fields["effort"] = effort if effort in EFFORT_LEVELS else None
        if "web_search" in body:
            fields["web_search"] = bool(body["web_search"])
        if "show_cost" in body:
            fields["show_cost"] = bool(body["show_cost"])
        if "mode" in body:
            mode = str(body["mode"] or "oddiy")
            if mode not in MODES:
                return _error("Noma'lum rejim")
            fields["mode"] = mode
        if not fields:
            return _error("O'zgartiriladigan sozlama yo'q")
        await self.ctx.db.update_settings(uid, **fields)
        return _json({"ok": True, "settings": await self._settings_payload(uid)})

    # ------------------------------------------------------------------ suhbatlar
    async def _notify(self, uid: int, text: str) -> int | None:
        try:
            message = await self.bot.send_message(chat_id=uid, text=text, parse_mode=ParseMode.HTML)
            return message.message_id
        except TelegramAPIError as exc:
            log.warning("Chatga xabar yuborilmadi (%s): %s", uid, exc)
            return None

    async def new_conversation(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        await self.ctx.db.new_conversation(uid)
        self.ctx.pending.pop(uid, None)
        await self._notify(uid, "🆕 Yangi suhbat boshlandi (ilovadan). Savolingizni yozing.")
        return _json({"ok": True, "settings": await self._settings_payload(uid)})

    async def conversations(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        query = (request.query.get("q") or "").strip()
        offset = max(int(request.query.get("offset") or 0), 0)
        limit = min(max(int(request.query.get("limit") or 30), 1), 100)
        if query:
            items = await self.ctx.db.search_conversations(uid, query, limit=limit)
            total = len(items)
        else:
            items, total = await self.ctx.db.conversations(uid, limit=limit, offset=offset)
        s = await self.ctx.user_settings(uid)
        return _json({
            "items": [
                {"conv_id": c.conv_id, "title": c.title, "turns": c.turns,
                 "updated_at": _when(c.updated_at, self.tz), "created_at": _when(c.created_at, self.tz)}
                for c in items
            ],
            "total": total, "current": s.conv_id,
        })

    async def conversation(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        try:
            conv_id = int(request.match_info["conv_id"])
        except ValueError:
            return _error("Suhbat raqami noto'g'ri", 404, "not_found")
        conversation = await self.ctx.db.conversation(uid, conv_id)
        if conversation is None:
            return _error("Suhbat topilmadi", 404, "not_found")
        history = await self.ctx.db.history(uid, conv_id, 0)
        messages = []
        for item in history:
            text, attachments = _message_preview(item.content)
            messages.append({"id": item.id, "role": item.role, "text": text, "attachments": attachments})
        return _json({
            "conversation": {"conv_id": conv_id, "title": conversation.title, "turns": conversation.turns,
                             "updated_at": _when(conversation.updated_at, self.tz)},
            "messages": messages,
        })

    async def resume_conversation(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        try:
            conv_id = int(request.match_info["conv_id"])
        except ValueError:
            return _error("Suhbat raqami noto'g'ri", 404, "not_found")
        conversation = await self.ctx.db.switch_conversation(uid, conv_id)
        if conversation is None:
            return _error("Suhbat topilmadi", 404, "not_found")
        await self._notify(
            uid, f"↩️ Suhbat davom ettirilmoqda: <b>{html.escape(conversation.title)}</b>\nSavolingizni yozing."
        )
        return _json({"ok": True, "conversation": {"conv_id": conv_id, "title": conversation.title}})

    async def ask(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        body = await self._body(request)
        text = str(body.get("text") or "").strip()
        if not text:
            return _error("Savol matni bo'sh")
        if len(text) > ASK_LIMIT:
            return _error(f"Savol juda uzun ({ASK_LIMIT} belgigacha)")
        if self.ctx.lock_for(uid).locked():
            return _error("Oldingi javob hali yozilmoqda — tugashini kuting.", 409, "busy")
        echo = await self._notify(uid, f"📨 <b>Ilovadan savol:</b>\n{html.escape(text)}")
        if echo is None:
            return _error("Chatga yozib bo'lmadi — botni ochib /start bosing.", 502, "telegram")
        blocks = self.ctx.take_pending(uid) + [{"type": "text", "text": text}]
        task = asyncio.create_task(run_chat_for(uid, uid, echo, self.bot, self.ctx, blocks), name=f"ask:{uid}")
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return _json({"ok": True, "message": "Javob chatda yoziladi."})

    # ------------------------------------------------------------------ fayllar
    async def files(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        offset = max(int(request.query.get("offset") or 0), 0)
        limit = min(max(int(request.query.get("limit") or 50), 1), 200)
        items, total = await self.ctx.db.files(uid, limit=limit, offset=offset)
        titles: dict[int, str] = {}
        result = []
        for item in items:
            if item.conv_id and item.conv_id not in titles:
                conversation = await self.ctx.db.conversation(uid, item.conv_id)
                titles[item.conv_id] = conversation.title if conversation else ""
            result.append({
                "id": item.id, "name": item.name, "kind": item.kind, "size": item.size,
                "created_at": _when(item.created_at, self.tz), "conv_id": item.conv_id,
                "conv_title": titles.get(item.conv_id, ""), "downloadable": bool(item.path),
                "sendable": bool(item.file_id or item.path),
            })
        return _json({"items": result, "total": total})

    async def _own_file(self, request: web.Request):
        try:
            file_pk = int(request.match_info["file_id"])
        except ValueError:
            return None
        item = await self.ctx.db.file(file_pk)
        if item is None or item.user_id != request[UID]:
            return None
        return item

    async def send_file(self, request: web.Request) -> web.Response:
        item = await self._own_file(request)
        if item is None:
            return _error("Fayl topilmadi", 404, "not_found")
        uid: int = request[UID]
        try:
            if item.file_id:
                if item.kind == "image":
                    await self.bot.send_photo(chat_id=uid, photo=item.file_id, caption=item.name)
                else:
                    await self.bot.send_document(chat_id=uid, document=item.file_id, caption=f"📎 {item.name}")
            else:
                data = self.ctx.files.read(item) if self.ctx.files else None
                if data is None:
                    return _error("Faylning nusxasi saqlanmagan", 410, "gone")
                await self.bot.send_document(
                    chat_id=uid, document=BufferedInputFile(data, filename=item.name), caption=f"📎 {item.name}"
                )
        except TelegramAPIError as exc:
            log.warning("Fayl qayta yuborilmadi: %s", exc)
            return _error("Telegram faylni yubormadi — birozdan keyin urinib ko'ring.", 502, "telegram")
        return _json({"ok": True})

    def _sign(self, file_pk: int, uid: int, expires: int) -> str:
        key = self.ctx.settings.telegram_token.encode()
        return hmac.new(key, f"{file_pk}:{uid}:{expires}".encode(), hashlib.sha256).hexdigest()[:40]

    async def file_link(self, request: web.Request) -> web.Response:
        item = await self._own_file(request)
        if item is None:
            return _error("Fayl topilmadi", 404, "not_found")
        if not item.path:
            return _error("Faylning nusxasi saqlanmagan — chatga yuborish mumkin", 410, "gone")
        expires = int(time.time()) + LINK_TTL
        uid: int = request[UID]
        url = f"files/{item.id}/download?u={uid}&e={expires}&s={self._sign(item.id, uid, expires)}"
        base = self.ctx.settings.webapp_url or ""
        return _json({"url": base + url, "name": item.name, "expires": expires})

    async def download(self, request: web.Request) -> web.StreamResponse:
        try:
            file_pk = int(request.match_info["file_id"])
            uid = int(request.query.get("u", ""))
            expires = int(request.query.get("e", ""))
        except ValueError:
            raise web.HTTPNotFound()
        signature = request.query.get("s", "")
        if expires < time.time() or not hmac.compare_digest(signature, self._sign(file_pk, uid, expires)):
            raise web.HTTPForbidden(text="Havola eskirgan — ilovadan qayta oching")
        item = await self.ctx.db.file(file_pk)
        if item is None or item.user_id != uid:
            raise web.HTTPNotFound()
        data = self.ctx.files.read(item) if self.ctx.files else None
        if data is None:
            raise web.HTTPGone(text="Fayl nusxasi saqlanmagan")
        safe = item.name.replace('"', "")
        return web.Response(
            body=data, content_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "private, no-store"},
        )

    async def delete_file(self, request: web.Request) -> web.Response:
        item = await self._own_file(request)
        if item is None:
            return _error("Fayl topilmadi", 404, "not_found")
        if self.ctx.files:
            await self.ctx.files.delete(item)
        else:
            await self.ctx.db.delete_file(item.id)
        return _json({"ok": True})

    # ------------------------------------------------------------------ xarajat
    async def stats(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        ctx = self.ctx
        now = ctx.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cycle_start = billing_cycle_start(now, ctx.settings.budget_reset_day)
        since = day_start - timedelta(days=STATS_DAYS - 1)
        rows = await ctx.db.usage_rows(uid, since)
        days: dict[str, dict[str, float | int]] = {}
        for offset in range(STATS_DAYS):
            key = (since + timedelta(days=offset)).strftime("%Y-%m-%d")
            days[key] = {"date": key, "cost": 0.0, "requests": 0, "searches": 0}
        for stamp, cost, searches in rows:
            key = _when(stamp, self.tz)[:10]
            if key in days:
                days[key]["cost"] = round(float(days[key]["cost"]) + cost, 6)
                days[key]["requests"] = int(days[key]["requests"]) + 1
                days[key]["searches"] = int(days[key]["searches"]) + searches
        today, today_n = await ctx.db.spend(uid, day_start)
        cycle, cycle_n = await ctx.db.spend(uid, cycle_start)
        total, total_n = await ctx.db.spend(uid)
        by_model = [
            {"model": model, "label": label(model), "cost": round(cost, 4), "count": count}
            for model, cost, count in await ctx.db.spend_by_model(uid, cycle_start)
        ]
        payload: dict[str, Any] = {
            "days": list(days.values()),
            "totals": {"today": round(today, 4), "today_requests": today_n, "cycle": round(cycle, 4),
                       "cycle_requests": cycle_n, "all": round(total, 4), "all_requests": total_n},
            "by_model": by_model,
            "budget": {"monthly": ctx.settings.monthly_budget, "cycle_start": cycle_start.strftime("%Y-%m-%d"),
                       "reset_day": ctx.settings.budget_reset_day, "days_elapsed": (day_start - cycle_start).days + 1},
            "show_cost": (await ctx.user_settings(uid)).show_cost,
        }
        if ctx.is_owner(uid):
            all_today, _ = await ctx.db.spend_all(day_start)
            all_cycle, _ = await ctx.db.spend_all(cycle_start)
            names = {m.user_id: m.display for m in await ctx.db.members()}
            by_user = [
                {"user_id": user_id, "name": "Siz" if user_id == uid else names.get(user_id, f"ID {user_id}"),
                 "cost": round(cost, 4), "count": count}
                for user_id, cost, count in await ctx.db.spend_by_user(cycle_start)
            ]
            payload["everyone"] = {"today": round(all_today, 4), "cycle": round(all_cycle, 4), "by_user": by_user[:20]}
        return _json(payload)

    # ------------------------------------------------------------------ shablonlar
    async def templates(self, request: web.Request) -> web.Response:
        items = await self.ctx.db.templates(request[UID])
        return _json({"items": [{"id": t.id, "name": t.name, "text": t.text} for t in items]})

    async def add_template(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        name = " ".join(str(body.get("name") or "").split())[:40]
        text = str(body.get("text") or "").strip()[:4000]
        if not name or not text:
            return _error("Shablon nomi va matni kerak")
        template = await self.ctx.db.add_template(request[UID], name, text)
        return _json({"ok": True, "item": {"id": template.id, "name": template.name, "text": template.text}})

    async def delete_template(self, request: web.Request) -> web.Response:
        try:
            template_id = int(request.match_info["template_id"])
        except ValueError:
            return _error("Shablon topilmadi", 404, "not_found")
        if not await self.ctx.db.delete_template(request[UID], template_id):
            return _error("Shablon topilmadi", 404, "not_found")
        return _json({"ok": True})

    # ------------------------------------------------------------------ admin
    async def admin(self, request: web.Request) -> web.Response:
        ctx = self.ctx
        now = ctx.now()
        cycle_start = billing_cycle_start(now, ctx.settings.budget_reset_day)
        spent = {uid: cost for uid, cost, _ in await ctx.db.spend_by_user(cycle_start)}
        members = [
            {"user_id": m.user_id, "name": m.name or "Nomsiz", "username": m.username, "active": m.active,
             "joined_at": _when(m.joined_at, self.tz), "cycle_cost": round(spent.get(m.user_id, 0.0), 4)}
            for m in await ctx.db.members()
        ]
        versions = await asyncio.to_thread(ctx.updater.backup_versions) if ctx.updater else []
        all_cycle, _ = await ctx.db.spend_all(cycle_start)
        budget = ctx.settings.monthly_budget
        return _json({
            "password_set": bool(await ctx.db.get_config(PASSWORD_KEY)),
            "members": members,
            "version": ctx.updater.version if ctx.updater else None,
            "backups": [
                {"version": version, "created_at": created.astimezone(self.tz).strftime("%Y-%m-%dT%H:%M:%S") if created else None}
                for version, created in versions
            ],
            "budget": {"monthly": budget, "used_all": round(all_cycle, 4),
                       "percent": round(all_cycle / budget * 100, 1) if budget > 0 else None,
                       "economy_percent": ctx.settings.budget_economy_percent, "reset_day": ctx.settings.budget_reset_day,
                       "cycle_start": cycle_start.strftime("%Y-%m-%d")},
            "backup_time": ctx.settings.backup_time,
            "owner_ids": sorted(ctx.settings.owner_ids),
            "default_model": label(ctx.settings.default_model),
            "github_repo": ctx.github.repo if ctx.github is not None else None,
            "update_check": bool(ctx.github is not None and ctx.settings.update_check),
        })

    async def admin_password(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        value = str(body.get("password") or "").strip()
        if body.get("remove") or value.lower() in ("off", "o'chir"):
            await self.ctx.db.delete_config(PASSWORD_KEY)
            return _json({"ok": True, "password_set": False})
        if len(value) < MIN_PASSWORD_LENGTH:
            return _error(f"Parol kamida {MIN_PASSWORD_LENGTH} belgidan iborat bo'lsin")
        await self.ctx.db.set_config(PASSWORD_KEY, await asyncio.to_thread(hash_password, value))
        return _json({"ok": True, "password_set": True})

    async def admin_member(self, request: web.Request) -> web.Response:
        try:
            member_id = int(request.match_info["member_id"])
        except ValueError:
            return _error("Foydalanuvchi topilmadi", 404, "not_found")
        body = await self._body(request)
        active = bool(body.get("active", False))
        if await self.ctx.db.member(member_id) is None:
            return _error("Foydalanuvchi topilmadi", 404, "not_found")
        await self.ctx.db.set_member_active(member_id, active)
        if not active:
            self.ctx.pending.pop(member_id, None)
            task = self.ctx.active.get(member_id)
            if task is not None and not task.done():
                task.cancel()
        return _json({"ok": True, "active": active})

    async def admin_backup(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        ok = await scheduler.send_backup(
            self.bot, self.ctx.db, self.ctx.settings.db_path, [uid], self.ctx.settings.timezone,
            reason="Zaxira nusxa (ilovadan)",
        )
        if not ok:
            return _error("Zaxira nusxa olinmadi — serverdagi loglarni tekshiring", 500, "failed")
        return _json({"ok": True, "message": "Zaxira nusxa chatga yuborildi."})

    async def admin_restore(self, request: web.Request) -> web.Response:
        uid: int = request[UID]
        ctx = self.ctx
        if ctx.updater is None:
            return _error("Yangilash tizimi ulanmagan", 500, "failed")
        try:
            async with ctx.staging_lock:
                staged = await asyncio.to_thread(ctx.updater.stage_restore)
        except UpdateError as exc:
            return _error(str(exc), 409, "nothing")
        old = ctx.staged.pop(uid, None)
        if old is not None and old.path != staged.path:
            ctx.updater.discard(old)
        ctx.staged[uid] = staged
        try:
            await self.bot.send_message(
                chat_id=uid,
                text=(
                    f"↩️ Oldingi versiyaga qaytish: v{html.escape(staged.current_version)} → "
                    f"<b>v{html.escape(staged.version)}</b>\nTasdiqlaysizmi? Bot 10–30 soniyaga qayta ishga tushadi."
                ),
                parse_mode=ParseMode.HTML, reply_markup=_confirm_keyboard(staged),
            )
        except TelegramAPIError as exc:
            return _error(f"Chatga yozib bo'lmadi: {exc}", 502, "telegram")
        return _json({"ok": True, "message": "Tasdiqlash so'rovi chatga yuborildi.", "version": staged.version})

    async def admin_github(self, request: web.Request) -> web.Response:
        """GitHub'dan yangilash: {"action": "check"} — faqat tekshiradi; aks holda yuklab, tasdiqni chatga yuboradi."""
        uid: int = request[UID]
        ctx = self.ctx
        if ctx.github is None or ctx.updater is None:
            return _error("GitHub'dan yangilash o'chiq (.env → GITHUB_REPO)", 409, "disabled")
        body = await self._body(request)
        if body.get("action") == "check":
            return _json({"ok": True, "message": await github_check_text(ctx), "current": ctx.updater.version})
        try:
            staged = await stage_from_github(ctx, uid)
        except UpdateCancelled:
            return _error("Bekor qilindi. Joriy versiya o'zgarmadi.", 409, "cancelled")
        except (GitHubError, UpdateError) as exc:
            return _error(str(exc), 409, "github")
        except Exception:
            log.exception("GitHub'dan yangilanishni tayyorlashda xato")
            return _error("GitHub'dan yangilanishni tayyorlab bo'lmadi — serverdagi loglarni tekshiring", 500, "failed")
        try:
            await self.bot.send_message(
                chat_id=uid, text=update_summary(staged), parse_mode=ParseMode.HTML, reply_markup=_confirm_keyboard(staged)
            )
        except TelegramAPIError as exc:
            return _error(f"Chatga yozib bo'lmadi: {exc}", 502, "telegram")
        return _json({
            "ok": True, "message": "Tasdiqlash so'rovi chatga yuborildi.", "version": staged.version,
            "current": staged.current_version, "changed": len(staged.changed),
        })


async def start_webapp(ctx: AppContext, bot: Bot, host: str = "0.0.0.0", port: int | None = None) -> web.AppRunner:
    """Mini App serverini ishga tushiradi; to'xtatish: await runner.cleanup()."""
    webapp = WebApp(ctx, bot)
    runner = web.AppRunner(webapp.app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port or ctx.settings.webapp_port)
    await site.start()
    log.info("Mini App serveri: http://%s:%s (ochiq manzil: %s)", host, port or ctx.settings.webapp_port, ctx.settings.webapp_url)
    return runner


async def stop_webapp(runner: web.AppRunner | None) -> None:
    if runner is not None:
        with contextlib.suppress(Exception):
            await runner.cleanup()

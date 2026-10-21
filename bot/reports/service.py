"""Hisobot xizmati: guruhlardagi lidlar va harakatlarni yozib borish, izoh tahlili, kunlik hisobot va eslatmalar.

Lidlar guruhiga bot hech narsa yozmaydi — faqat o'qiydi. Hisobot rahbarlar guruhiga (bo'lmasa egasiga) boradi;
«📋 Vazifalar» guruhida faqat «Bajardingizmi?» so'rovlari (bot/reports/tasks.py)."""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageReactionUpdated,
    User,
    WebAppInfo,
)

from ..claude import ClaudeError
from . import analysis
from .leads import extract_phones, message_link, parse_lead
from .render import render_report, split_message
from .stats import (
    SCHEDULE_KEY,
    Overview,
    Schedule,
    build_overview,
    current_day,
    in_work_hours,
    latest_due_day,
    period_bounds,
)
from .store import Group, Lead, ReportStore, iso
from .tasks import TaskService, task_body

if TYPE_CHECKING:
    from ..handlers import AppContext

log = logging.getLogger(__name__)

SINCE_KEY = "report:since"  # kuzatish boshlangan vaqt: undan oldingi davrlar uchun hisobot yuborilmaydi
TELEGRAM_SERVICE_ID = 777000  # kanal postlarini muhokama guruhiga avtomatik ko'chiruvchi «Telegram»
TICK_SECONDS = 30
API_BACKOFF = 300  # Claude xatosidan keyin tahlil shuncha soniya kutadi
LATE_AFTER = timedelta(minutes=15)
MAX_LATE = timedelta(hours=36)
REPORT_RETRY = timedelta(minutes=5)  # kunlik hisobot hech qayerga bormasa — shuncha vaqtdan keyin qayta urinish
REMIND_GAP = timedelta(minutes=15)  # operatorlarga eslatmalar orasidagi eng kam vaqt (yangi lidlar yig'ilib boradi)
FLUSH_BATCHES = 5
REMIND_SHOWN = 10
ROLE_LABELS = {"leads": "📥 Lidlar", "leaders": "👔 Rahbarlar", "tasks": "📋 Vazifalar"}
ROLE_HELP = (
    "📥 <b>Lidlar</b> — arizalar va operatorlar izohi tushadigan guruh: statuslar shu yerdan olinadi\n"
    "👔 <b>Rahbarlar</b> — kunlik hisobot shu guruhga yuboriladi\n"
    "📋 <b>Vazifalar</b> — siz «#vazifa @username …» deb vazifa berasiz, bot muddatida «Bajardingizmi?» deb so'raydi"
)


def _norm(emoji: str | None) -> str:
    return (emoji or "").replace("️", "")


def details_param(day: date | None = None) -> str:
    return "hisobot" if day is None else f"hisobot_{day:%Y%m%d}"


BOT_INFO_TTL = 600.0  # soniya: getMe natijasi shuncha vaqt eskirmagan hisoblanadi


class ReportService:
    def __init__(self, ctx: "AppContext") -> None:
        self.ctx = ctx
        self.store = ReportStore(ctx.db)
        self.bot_info: dict[str, Any] = {}  # getMe: username, has_main_web_app, can_read_all
        self._bot_info_at = 0.0
        self._ready = False
        self._task: asyncio.Task | None = None
        self._backoff_until = 0.0
        self._known: dict[str, str] | None = None  # normallashgan emoji → status emoji (kesh)
        self._analysis_lock = asyncio.Lock()  # fon sikli va /hisobot bir to'plamni ikki marta yubormasin
        self._retry_after: datetime | None = None  # kunlik hisobot yuborilmay qolsa — qayta urinish vaqti
        self._remind_after: datetime | None = None
        self._reasons_cache: tuple[str, tuple, list] | None = None  # (davr, xom sabablar, guruhlangan)
        self.last_fallback = False  # oxirgi hisobot rahbarlar guruhiga bormay, egasiga yuborildimi
        self.tasks = TaskService(self)

    # ------------------------------------------------------------------ umumiy
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.ctx.settings.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    async def ensure(self) -> None:
        if self._ready:
            return
        await self.store.seed_statuses()
        if not await self.ctx.db.get_config(SINCE_KEY):
            await self.ctx.db.set_config(SINCE_KEY, iso(datetime.now(timezone.utc)))
        self._ready = True

    async def since(self) -> datetime:
        raw = await self.ctx.db.get_config(SINCE_KEY)
        try:
            return datetime.strptime(raw or "", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)

    async def schedule(self) -> Schedule:
        return Schedule.from_json(await self.ctx.db.get_config(SCHEDULE_KEY))

    async def save_schedule(self, data: dict) -> Schedule:
        schedule = (await self.schedule()).updated(data)
        await self.ctx.db.set_config(SCHEDULE_KEY, schedule.to_json())
        return schedule

    async def known_statuses(self) -> dict[str, str]:
        if self._known is None:
            self._known = {_norm(s.emoji): s.emoji for s in await self.store.statuses()}
        return self._known

    def statuses_changed(self) -> None:
        self._known = None

    def owners(self) -> list[int]:
        return sorted(self.ctx.settings.owner_ids)

    async def _tell_owners(self, bot: Bot, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        for owner_id in self.owners():
            with contextlib.suppress(TelegramAPIError):
                await bot.send_message(owner_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)

    # ------------------------------------------------------------------ guruhlar
    def role_keyboard(self, group: Group) -> InlineKeyboardMarkup:
        toggles = [
            InlineKeyboardButton(
                text=("☑️ " if role in group.roles and group.active else "⬜ ") + label,
                callback_data=f"rg:{group.chat_id}:{role}",
            )
            for role, label in ROLE_LABELS.items()
        ]
        return InlineKeyboardMarkup(inline_keyboard=[
            toggles[:2],
            toggles[2:],
            [
                InlineKeyboardButton(text="✅ Tayyor", callback_data=f"rg:{group.chat_id}:done"),
                InlineKeyboardButton(text="🚫 Kuzatilmasin", callback_data=f"rg:{group.chat_id}:off"),
            ],
        ])

    def role_prompt(self, group: Group, by: User | None = None, *, joined: bool = True) -> str:
        title = html.escape(group.title or str(group.chat_id))
        if joined:
            who = ""
            if by is not None and not self.ctx.is_owner(by.id):
                who = f" ({html.escape(by.full_name)}{' @' + html.escape(by.username) if by.username else ''} qo'shdi)"
            head = f"🤖 Bot <b>«{title}»</b> guruhiga qo'shildi{who}."
        else:
            head = f"⚙️ <b>«{title}»</b> guruhi."
        return (
            f"{head}\nBu guruh nima uchun? Bir nechtasini tanlash mumkin:\n\n" + ROLE_HELP + "\n\n"
            "Bot guruhga faqat «Bajardingizmi?» so'rovlarini yozadi (📋 Vazifalar guruhida); qolganini jim o'qiydi."
        )

    async def ask_roles(self, bot: Bot, group: Group, by: User | None = None, *, force: bool = False) -> None:
        """Egasidan guruh turini so'raydi. force=False — har guruh uchun bir marta (bir vaqtda kelgan xabarlar
        ikkita savol yubormasin); force=True — bot guruhga qayta qo'shilganda."""
        if force:
            await self.store.mark_group_asked(group.chat_id)
        elif not await self.store.claim_ask(group.chat_id):
            return
        await self._tell_owners(bot, self.role_prompt(group, by), self.role_keyboard(group))

    async def check_admin(self, bot: Bot, chat_id: int) -> bool | None:
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
        except TelegramAPIError:
            return None
        is_admin = getattr(member, "status", "") in ("administrator", "creator")
        await self.store.set_group_admin(chat_id, is_admin)
        return is_admin

    async def group_summary(self, bot: Bot, group: Group) -> str:
        title = html.escape(group.title or str(group.chat_id))
        if not group.active or not group.roles:
            return f"🚫 «{title}» kuzatilmaydi. Qayta yoqish: /guruhlar yoki Mini App → Hisobotlar → Sozlamalar."
        roles = ", ".join(ROLE_LABELS[r] for r in group.roles if r in ROLE_LABELS)
        lines = [f"✅ «{title}»: {roles}."]
        if "leads" in group.roles:
            is_admin = await self.check_admin(bot, group.chat_id)
            if is_admin is False:
                lines.append(
                    "⚠️ Bot bu guruhda admin emas — operatorlar reaksiyasini ko'rmaydi. Guruh sozlamalari → "
                    "Administratorlar → botni qo'shing (hech qanday huquq belgilash shart emas)."
                )
                if not self.bot_info.get("can_read_all", True):
                    lines.append("Admin bo'lmaguncha izohlarni ham o'qiy olmaydi.")
            lines.append("Lid kartochkalari, reaksiyalar va izohlar shu paytdan boshlab hisobga olinadi.")
        if "leaders" in group.roles:
            schedule = await self.schedule()
            lines.append(
                f"Kunlik hisobot shu guruhga har kuni {schedule.time} da keladi ({schedule.days_label()}). "
                "Vaqtini Mini App → Hisobotlar → Sozlamalar'da o'zgartirasiz."
            )
        if "tasks" in group.roles:
            lines.append("Vazifa berish: «#vazifa @username nima qilish kerak, qachongacha» — bot ✍️ qo'yadi va muddatida "
                         "ijrochidan «Bajardingizmi?» deb so'raydi.")
            if "leads" not in group.roles and not self.bot_info.get("can_read_all", True):
                is_admin = await self.check_admin(bot, group.chat_id)
                if is_admin is False:
                    lines.append("⚠️ Bot bu guruhda admin emas — «#vazifa» xabarlarini ko'rmaydi. Botni admin qiling "
                                 "(huquqlar shart emas) yoki «/vazifa @username …» buyrug'idan foydalaning.")
        return "\n".join(lines)

    async def on_bot_membership(self, update: ChatMemberUpdated, bot: Bot) -> None:
        chat = update.chat
        if chat.type not in ("group", "supergroup"):
            return
        await self.ensure()
        old, new = update.old_chat_member.status, update.new_chat_member.status
        existing = await self.store.group(chat.id)
        if new in ("left", "kicked"):
            if existing is not None:
                await self.store.upsert_group(chat.id, chat.title or "", active=False)
                if existing.roles:
                    await self._tell_owners(
                        bot, f"👋 Bot «{html.escape(chat.title or str(chat.id))}» guruhidan chiqarildi — u yerda kuzatish to'xtadi."
                    )
            return
        is_admin = new in ("administrator", "creator")
        joined = old in ("left", "kicked")
        group = await self.store.upsert_group(
            chat.id, chat.title or "", added_by=update.from_user.id if joined else None, is_admin=is_admin,
            active=True if joined else None,
        )
        if joined and not group.roles:
            await self.ask_roles(bot, group, update.from_user, force=True)
        elif joined:
            await self._tell_owners(bot, "🔁 Bot guruhga qaytdi.\n" + await self.group_summary(bot, group))
        elif existing is not None and existing.is_admin != is_admin and "leads" in group.roles:
            title = html.escape(group.title)
            if is_admin:
                await self._tell_owners(bot, f"✅ Bot «{title}» guruhida admin bo'ldi — reaksiyalar ham hisobga olinadi.")
            else:
                await self._tell_owners(bot, f"⚠️ Bot «{title}» guruhida adminlikdan olindi — reaksiyalar endi ko'rinmaydi.")

    # ------------------------------------------------------------------ yozib borish
    async def record_message(self, message: Message, bot: Bot) -> None:
        chat = message.chat
        if message.migrate_to_chat_id:
            await self.store.migrate_group(chat.id, message.migrate_to_chat_id)
            return
        if message.migrate_from_chat_id:  # superguruhdagi birinchi xabar — sozlamalar eski guruhdan o'tadi
            await self.store.migrate_group(message.migrate_from_chat_id, chat.id)
            return
        await self.ensure()
        group = await self.store.group(chat.id)
        if group is None or (not group.asked and not group.roles):
            group = await self.store.upsert_group(chat.id, chat.title or "")
            await self.ask_roles(bot, group)
            return
        if not group.active or not group.roles:
            return
        user = message.from_user
        if user is not None and not user.is_bot and user.id != TELEGRAM_SERVICE_ID and message.sender_chat is None:
            await self.store.remember_user(user.id, user.username, user.full_name)
            body = (task_body(message.text or message.caption, self.bot_info.get("username"))
                    if self.ctx.is_owner(user.id) and message.forward_origin is None else None)
            if body is not None:  # egasining vazifasi — lid ham, oddiy xabar ham emas
                await self.store.mark_seen(chat.id, message.message_id)  # unga reaksiya — qo'ng'iroq emas
                if "tasks" in group.roles:
                    await self.tasks.from_message(bot, message, body, group_chat=chat.id)
                else:
                    await self.tasks.maybe_hint(bot, chat.id, group.title, user.id, self.now())
                return
        if "leads" in group.roles and not await self._record_lead_message(message):
            await self.store.mark_seen(chat.id, message.message_id)

    async def _thread_lead(self, message: Message) -> Lead | None:
        chat_id = message.chat.id
        thread = message.message_thread_id
        reply = message.reply_to_message
        if thread:
            lead = await self.store.lead_by_message(chat_id, thread)
            if lead is not None:
                return lead
        if reply is not None:
            lead = await self.store.lead_by_message(chat_id, reply.message_id)
            if lead is not None:
                return lead
            if reply.is_automatic_forward:  # bot qo'shilishidan oldingi post — izoh orqali tanib olinadi
                text = reply.text or reply.caption or ""
                info = parse_lead(text)
                return await self.store.save_lead(
                    chat_id, reply.message_id, source="reply", created_at=iso(reply.date), name=info.name,
                    phones=info.phones, age=info.age, district=info.district, course=info.course, text=text,
                )
        return None

    async def _record_lead_message(self, message: Message) -> bool:
        """Lid posti yoki lid ostidagi izohni yozadi; xabar ikkalasi ham bo'lmasa False."""
        text = message.text or message.caption or ""
        chat_id = message.chat.id
        if message.is_automatic_forward:
            info = parse_lead(text)
            await self.store.save_lead(
                chat_id, message.message_id, source="post", created_at=iso(message.date), name=info.name,
                phones=info.phones, age=info.age, district=info.district, course=info.course, text=text,
            )
            return True
        lead = await self._thread_lead(message)
        if lead is None:
            if message.reply_to_message is None:
                info = parse_lead(text)
                if info.looks_like_lead:  # kartochka guruhga qo'lda tashlangan
                    await self.store.save_lead(
                        chat_id, message.message_id, source="post", created_at=iso(message.date), name=info.name,
                        phones=info.phones, age=info.age, district=info.district, course=info.course, text=text,
                    )
                    return True
                if extract_phones(text) or message.photo or message.document:
                    return True  # o'qib bo'lmagan kartochka bo'lishi mumkin: operator status bossa — lid sanaladi
            return False
        user = message.from_user
        if user is None or user.is_bot or user.id == TELEGRAM_SERVICE_ID or message.sender_chat is not None:
            return False
        analyzable = bool(text.strip())
        if not analyzable:
            text = "[ovozli xabar]" if (message.voice or message.audio or message.video_note) else "[matnsiz xabar]"
        await self.store.add_comment(lead, message.message_id, user.id, text, iso(message.date), analyzable=analyzable)
        return True

    async def record_edit(self, message: Message) -> None:
        group = await self.store.group(message.chat.id)
        if group is None or not group.has("leads"):
            return
        text = message.text or message.caption or ""
        if message.is_automatic_forward or await self.store.lead_by_message(message.chat.id, message.message_id):
            info = parse_lead(text)
            await self.store.save_lead(
                message.chat.id, message.message_id, source="post", created_at=iso(message.date), name=info.name,
                phones=info.phones, age=info.age, district=info.district, course=info.course, text=text,
            )
        elif text.strip():
            await self.store.edit_comment(message.chat.id, message.message_id, text)

    async def record_reaction(self, update: MessageReactionUpdated) -> None:
        group = await self.store.group(update.chat.id)
        if group is None or not group.has("leads"):
            return
        user = update.user
        if user is None or user.is_bot:  # anonim admin (actor_chat) — kimligi noma'lum
            return
        await self.ensure()
        await self.store.remember_user(user.id, user.username, user.full_name)
        known = await self.known_statuses()
        emojis = [e for e in (getattr(r, "emoji", None) for r in update.new_reaction or []) if e]
        status = next((known[_norm(e)] for e in emojis if _norm(e) in known), None)
        lead = await self.store.lead_by_message(update.chat.id, update.message_id)
        if lead is None:
            if status is None or await self.store.is_comment_message(update.chat.id, update.message_id):
                return
            if await self.store.was_seen(update.chat.id, update.message_id):
                return  # bot ko'rgan, lekin lid bo'lmagan xabar (masalan, rahbar e'loni) — qo'ng'iroq emas
            # bot ko'rmagan post (kuzatishdan oldingi yoki boshqa bot yozgan): mazmuni noma'lum, belgi sanaladi
            lead = await self.store.save_lead(update.chat.id, update.message_id, source="stub")
        await self.store.add_reaction(lead, user.id, emojis[0] if emojis else None, status, iso(update.date))

    # ------------------------------------------------------------------ xodimlar (shaxsiy chat)
    async def staff_role(self, user: User) -> str | None:
        """Shaxsiy chatdagi har xabar uchun (egasi va a'zolar ham): ro'yxatdagi xodim ID bilan bog'lanadi,
        operator esa eslatma oladigan bo'ladi (botga yozgani — lichkaga yozish mumkinligi)."""
        roles = await self.store.roles_of(user.id, user.username)
        if not roles:
            return None
        await self.store.bind_user(user.id, user.username)
        if "operator" in roles:
            await self.store.remember_user(user.id, user.username, user.full_name)
            await self.store.set_dm_ok(user.id)
        if "leader" in roles:
            return "leader"
        if "operator" in roles:
            return "operator"
        return None

    async def is_leader(self, user_id: int, username: str | None) -> bool:
        if "leader" not in await self.store.roles_of(user_id, username):
            return False
        await self.store.bind_user(user_id, username)
        return True

    async def staff_message(self, message: Message, role: str) -> None:
        user = message.from_user
        name = html.escape(user.first_name or user.full_name or "do'stim")
        markup = None
        if role == "leader":
            text = (
                f"👋 Salom, {name}!\nSiz rahbar sifatida <b>Hisobotlar</b> bo'limiga kira olasiz: "
                "call center natijalari, operatorlar va kunlik hisobotlar."
            )
            if self.ctx.settings.webapp_url:
                markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="📊 Hisobotlar", web_app=WebAppInfo(url=self.ctx.settings.webapp_url))
                ]])
            else:
                text += "\nKunlik hisobot rahbarlar guruhiga keladi."
        else:
            text = (
                f"👋 Salom, {name}! Siz call center operatori sifatida ro'yxatdasiz.\n"
                "Hech kim belgilamagan lidlar haqida eslatmalar shu yerga keladi."
            )
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)

    # ------------------------------------------------------------------ hisobot
    async def overview(self, day: date | None = None, now: datetime | None = None) -> Overview:
        await self.ensure()
        now = now or self.now()
        schedule = await self.schedule()
        return await build_overview(self.store, schedule, day or current_day(now, schedule), now)

    def set_bot_info(self, me: User) -> None:
        """getMe: username, Main Mini App («📊 Batafsil» tugmasi uchun), guruh xabarlarini o'qish huquqi."""
        self.bot_info = {
            "username": me.username,
            "has_main_web_app": bool(getattr(me, "has_main_web_app", False)),
            "can_read_all": bool(getattr(me, "can_read_all_group_messages", False)),
        }
        self._bot_info_at = time.monotonic()

    async def refresh_bot_info(self, bot: Bot, max_age: float = BOT_INFO_TTL) -> None:
        """BotFather'dagi o'zgarish (Main Mini App yoqilishi) botni qayta ishga tushirmasdan ko'rinsin."""
        if self.bot_info and time.monotonic() - self._bot_info_at < max_age:
            return
        try:
            self.set_bot_info(await bot.get_me())
        except (TelegramAPIError, asyncio.TimeoutError, OSError) as exc:  # tarmoq xatosi — eski ma'lumot qoladi
            log.warning("getMe bajarilmadi: %s", exc)

    def app_button(self, param: str, text: str) -> InlineKeyboardButton | None:
        """Mini App tugmasi: Main Mini App yoqilgan bo'lsa — kerakli bo'limga (startapp), bo'lmasa — oddiy ochish."""
        username = self.bot_info.get("username")
        if self.bot_info.get("has_main_web_app") and username and self.ctx.settings.webapp_url:
            return InlineKeyboardButton(text=text, url=f"https://t.me/{username}?startapp={param}")
        if self.ctx.settings.webapp_url:
            return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=self.ctx.settings.webapp_url))
        return None

    def details_markup(self, day: date | None = None) -> InlineKeyboardMarkup | None:
        username = self.bot_info.get("username")
        if not (self.bot_info.get("has_main_web_app") and username and self.ctx.settings.webapp_url):
            return None
        url = f"https://t.me/{username}?startapp={details_param(day)}"
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Batafsil", url=url)]])

    async def analyze_pending(self, max_batches: int = 1) -> int:
        async with self._analysis_lock:
            if time.monotonic() < self._backoff_until:
                return 0
            done = 0
            statuses = await self.store.statuses()
            for _ in range(max_batches):
                batch = await self.store.pending_comments(analysis.BATCH)
                if not batch:
                    break
                try:
                    done += await analysis.classify_comments(
                        self.ctx.claude, self.store, self.ctx.db, self.ctx.settings.report_model, statuses, batch
                    )
                except ClaudeError as exc:
                    log.warning("Izohlar tahlili to'xtatildi (%s soniyadan keyin qayta): %s", API_BACKOFF, exc.user_message)
                    self._backoff_until = time.monotonic() + API_BACKOFF
                    break
            return done

    async def _grouped_reasons(self, overview: Overview) -> list:
        """Rad sabablarini ma'nosi bo'yicha guruhlash (Claude); sabablar o'zgarmagan bo'lsa — qayta so'ralmaydi."""
        key, raw = overview.end.strftime("%Y-%m-%d %H:%M"), tuple(overview.reasons)
        cached = self._reasons_cache
        if cached is not None and cached[0] == key and cached[1] == raw:
            return cached[2]
        reasons = await analysis.cluster_reasons(self.ctx.claude, self.ctx.db, self.ctx.settings.report_model, overview.reasons)
        self._reasons_cache = (key, raw, reasons)
        return reasons

    async def compose(self, day: date, now: datetime, *, interim: bool, late: bool = False) -> tuple[Overview, str, list]:
        await self.analyze_pending(FLUSH_BATCHES)
        overview = await self.overview(day, now)
        reasons = await self._grouped_reasons(overview) if overview.reasons else []
        text = render_report(overview, reasons, interim_at=now if interim else None, late=late)
        return overview, text, reasons

    async def report_targets(self) -> list[int]:
        return [g.chat_id for g in await self.store.groups_with("leaders")] or self.owners()

    async def _deliver(self, bot: Bot, chat_ids: list[int], text: str,
                       markup: InlineKeyboardMarkup | None) -> tuple[int, list[str]]:
        """Hisobotni chatlarga yuboradi (uzun bo'lsa bo'laklab, tugma oxirgisida): (yetkazilgan chatlar, xatolar)."""
        parts = split_message(text)
        sent, failed = 0, []
        for chat_id in chat_ids:
            try:
                for index, part in enumerate(parts):
                    last = index == len(parts) - 1
                    msg = await bot.send_message(chat_id, part, parse_mode=ParseMode.HTML, reply_markup=markup if last else None)
                    if chat_id < 0:  # guruhdagi hisobotga reaksiya — qo'ng'iroq emas (eng yaxshi urinish)
                        try:
                            await self.store.mark_seen(chat_id, msg.message_id)
                        except Exception:  # noqa: BLE001
                            log.exception("Hisobot xabarini belgilab bo'lmadi")
                sent += 1
            except TelegramAPIError as exc:
                log.warning("Hisobot yuborilmadi (%s): %s", chat_id, exc)
                failed.append(f"{chat_id}: {exc}")
        return sent, failed

    async def send_report(self, bot: Bot, day: date, now: datetime, *, interim: bool = False, late: bool = False,
                          chat_ids: list[int] | None = None) -> int:
        overview, text, reasons = await self.compose(day, now, interim=interim, late=late)
        await self.refresh_bot_info(bot)
        markup = self.details_markup(None if overview.live else day)
        targets = chat_ids or await self.report_targets()
        sent, failed = await self._deliver(bot, targets, text, markup)
        self.last_fallback = False
        owners = [o for o in self.owners() if o not in targets]
        if failed and owners:
            problem = html.escape("\n".join(failed))
            if sent:
                await self._tell_owners(bot, "⚠️ Hisobot ba'zi chatlarga yuborilmadi:\n" + problem)
            else:  # rahbarlar guruhiga umuman bormadi — hisobot yo'qolmasin, egasiga keladi
                head = f"⚠️ Hisobot rahbarlar guruhiga yuborilmadi — sizga yuborildi.\n<i>{problem}</i>\n\n"
                sent, _ = await self._deliver(bot, owners, head + text, markup)
                self.last_fallback = bool(sent)
        if not interim:
            if sent:  # hech qayerga bormagan hisobot «yuborilgan» deb belgilanmaydi — keyinroq qayta uriniladi
                await self.store.save_report(
                    overview.end.strftime("%Y-%m-%d %H:%M"), overview.start.strftime("%Y-%m-%d %H:%M"), text,
                    json.dumps(reasons, ensure_ascii=False),
                )
                self._retry_after = None
            else:
                self._retry_after = now + REPORT_RETRY
                log.warning("Kunlik hisobot hech qayerga yetmadi — %s dan keyin qayta uriniladi", f"{self._retry_after:%H:%M}")
        return sent

    async def check_daily(self, bot: Bot, now: datetime) -> bool:
        schedule = await self.schedule()
        if not schedule.enabled:
            return False
        day = latest_due_day(now, schedule)
        if day is None:
            return False
        start, end = period_bounds(day, schedule, self.tz)
        if await self.store.report_for_day(day.isoformat()):  # vaqt o'zgartirilsa ham bir kunga bitta hisobot
            return False
        if end <= await self.since() or now - end > MAX_LATE:
            return False
        if self._retry_after is not None and now < self._retry_after:
            return False
        if not await self.store.groups_with("leads"):
            return False
        await self.send_report(bot, day, now, late=now - end > LATE_AFTER)
        return True

    async def check_reminders(self, bot: Bot, now: datetime) -> int:
        schedule = await self.schedule()
        if schedule.remind_minutes <= 0 or not in_work_hours(now, schedule):
            return 0
        if self._remind_after is not None and now < self._remind_after:
            return 0  # oldingi eslatmadan beri kam vaqt o'tdi — yangi lidlar keyingisida birga keladi
        recipients = list({p.user_id: p for p in await self.store.people("operator") if p.dm_ok and p.user_id}.values())
        if not recipients or not await self.store.groups_with("leads"):
            return 0
        cutoff = now - timedelta(minutes=schedule.remind_minutes)
        leads = await self.store.unmarked_leads(iso(now - timedelta(hours=24)), iso(cutoff))
        if not leads:
            return 0
        lines = [f"⏳ <b>{len(leads)} ta lid {schedule.remind_minutes} daqiqadan beri belgilanmagan</b>", ""]
        for lead in leads[:REMIND_SHOWN]:
            moment = datetime.strptime(lead.created_at, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(self.tz)
            what = html.escape(" — ".join(x for x in (lead.name or "Ism ko'rsatilmagan", lead.course) if x))
            link = message_link(lead.chat_id, lead.message_id)
            lines.append(f"• {what} · {moment:%H:%M}" + (f' · <a href="{link}">ochish</a>' if link else ""))
        if len(leads) > REMIND_SHOWN:
            lines.append(f"… va yana {len(leads) - REMIND_SHOWN} ta")
        text = "\n".join(lines)
        sent = 0
        for person in recipients:
            try:
                await bot.send_message(person.user_id, text, parse_mode=ParseMode.HTML)
                sent += 1
            except TelegramAPIError as exc:
                log.info("Operatorga eslatma yuborilmadi (%s): %s", person.user_id, exc)
        await self.store.mark_reminded(lead.id for lead in leads)
        self._remind_after = now + REMIND_GAP
        return sent

    async def tick(self, bot: Bot, now: datetime | None = None) -> None:
        await self.ensure()
        now = now or self.now()
        steps = (
            ("izohlar tahlili", lambda: self.analyze_pending(max_batches=2)),
            ("eslatmalar", lambda: self.check_reminders(bot, now)),
            ("kunlik hisobot", lambda: self.check_daily(bot, now)),
            ("vazifalar", lambda: self.tasks.tick(bot, now)),
        )
        for name, step in steps:  # bittasidagi xato qolganlarini (ayniqsa kunlik hisobotni) to'xtatmasin
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Hisobot vazifasida xato (%s)", name)

    # ------------------------------------------------------------------ fon vazifasi
    def start(self, bot: Bot) -> None:
        self._task = asyncio.create_task(self._loop(bot), name="reports")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self, bot: Bot) -> None:
        await asyncio.sleep(3)
        while True:
            try:
                await self.tick(bot)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — bitta xato hisobotlarni to'xtatmasin
                log.exception("Hisobot vazifasida xato")
            await asyncio.sleep(TICK_SECONDS)

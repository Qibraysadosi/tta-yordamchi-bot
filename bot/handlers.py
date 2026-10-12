"""Telegram buyruqlari, xabarlar va suhbat jarayoni."""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User,
)

from . import branding, media
from .access import MIN_PASSWORD_LENGTH, AttemptLimiter, hash_password, verify_password
from .claude import ClaudeError, ClaudeService, TurnResult
from .config import Settings
from .db import Database, UserSettings
from .live import LiveMessage
from .models import MODELS, SELECTABLE_MODELS, label
from .prompt import MONTHS, PromptLoader
from .tgformat import format_answer
from .updater import StagedUpdate, UpdateError, UpdateManager

log = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024  # Telegram Bot API faqat 20 MB gacha faylni beradi
PENDING_TTL = 3600  # izohsiz yuborilgan fayl 1 soat savolni kutadi

COMMANDS = [
    BotCommand(command="start", description="🏠 Bosh menyu"),
    BotCommand(command="new", description="🆕 Yangi suhbat"),
    BotCommand(command="model", description="🤖 Modelni tanlash"),
    BotCommand(command="effort", description="🧠 Fikrlash darajasi"),
    BotCommand(command="qidiruv", description="🔎 Internet qidiruvi: yoqish/o'chirish"),
    BotCommand(command="xarajat", description="💰 Xarajat hisoboti"),
    BotCommand(command="yordam", description="📖 Yordam"),
]
OWNER_COMMANDS = COMMANDS + [
    BotCommand(command="yangilash", description="📦 Botni yangilash (.zip)"),
    BotCommand(command="versiya", description="🏷 Versiya va zaxiralar"),
    BotCommand(command="qaytarish", description="↩️ Oldingi versiyaga qaytish"),
    BotCommand(command="parol", description="🔐 Kirish paroli"),
    BotCommand(command="foydalanuvchilar", description="👥 Foydalanuvchilar"),
    BotCommand(command="kod", description="💾 Botning joriy kodi (.zip)"),
]
UPDATE_WAIT_SECONDS = 15 * 60
PASSWORD_KEY = "password_hash"

EFFORT_CHOICES = [
    ("default", "Standart (modelga qarab)"),
    ("low", "low — tez va arzon"),
    ("medium", "medium"),
    ("high", "high"),
    ("xhigh", "xhigh"),
    ("max", "max — eng chuqur, eng qimmat"),
]

REFUSAL_CATEGORIES = {
    "cyber": " (kiberxavfsizlik mavzusi)",
    "bio": " (biologiya mavzusi)",
    "frontier_llm": " (AI modellar ishlab chiqish mavzusi)",
    "reasoning_extraction": " (fikrlash jarayonini olish)",
    "general_harms": " (xavfli mavzu)",
}

HELP_TEXT = (
    "📖 <b>Qanday foydalanish</b>\n\n"
    "💬 <b>Savol bering</b> — oddiy yozing. Javob yozilayotganini ko'rib turasiz, "
    "⏹ tugmasi bilan to'xtatish mumkin.\n\n"
    "📎 <b>Fayl yuboring</b> — rasm, PDF, Word (.docx), Excel (.xlsx) yoki matn fayl. "
    "Izohsiz yuborsangiz, keyingi savolingiz bilan birga ko'rib chiqaman.\n\n"
    "↩️ <b>Iqtibos</b> — biror xabarga «Ответить» qilib yozsangiz, o'sha matnni ham hisobga olaman.\n\n"
    "<b>Buyruqlar</b>\n"
    "🏠 /start — bosh menyu\n"
    "🆕 /new — yangi suhbat (kontekst tozalanadi, javoblar arzonlashadi)\n"
    "🤖 /model — model: Fable, Opus, Sonnet, Haiku\n"
    "🧠 /effort — fikrlash darajasi\n"
    "🔎 /qidiruv — internet qidiruvini yoqish/o'chirish\n"
    "💰 /xarajat — sarf hisoboti"
)

ADMIN_HELP = (
    "⚙️ <b>Admin panel</b> <i>(faqat siz uchun)</i>\n\n"
    "📦 /yangilash — botni yangilash: buyruqdan keyin .zip yuborasiz\n"
    "🏷 /versiya — joriy versiya va zaxira nusxalar\n"
    "↩️ /qaytarish — oldingi versiyaga qaytish\n"
    "🔐 /parol — kirish parolini o'rnatish yoki o'zgartirish\n"
    "👥 /foydalanuvchilar — parol bilan kirganlar, chiqarib yuborish\n"
    "💾 /kod — botning joriy kodi (.zip): yangi funksiya so'raganda Claude'ga yuborasiz"
)

WELCOME_TEXT = (
    "👋 <b>Salom, {name}!</b>\n"
    "Men — <b>TTA Yordamchi</b>, Claude asosidagi shaxsiy AI yordamchingiz.\n\n"
    "✍️ Savol bering, matn yozdiring, tarjima qildiring\n"
    "📎 Rasm, PDF, Word, Excel yuboring — tahlil qilaman\n"
    "🔎 Yangi ma'lumotni internetdan topaman\n\n"
    "⚙️ <b>Hozir:</b> {settings}\n\n"
    "Boshlash uchun shunchaki savolingizni yozing 👇"
)

LOCKED_TEXT = (
    "🔒 <b>Bu bot parol bilan himoyalangan.</b>\n"
    "Kirish uchun parolni shu chatga yozib yuboring.\n\n"
    "<i>Parolni bot egasidan so'rang.</i>"
)

PASSWORD_HELP = (
    "🔐 <b>Kirish paroli:</b> {status}\n\n"
    "O'rnatish yoki o'zgartirish: <code>/parol YANGI_PAROL</code>\n"
    "O'chirish (yangi odamlar kira olmaydi, kirganlar qoladi): <code>/parol off</code>\n\n"
    "Parolni bilgan odam botga yozib kira oladi. Kirganlar ro'yxati: /foydalanuvchilar"
)

EFFORT_TEXT = (
    "🧠 <b>Fikrlash darajasi (effort)</b>\n\n"
    "Daraja qancha yuqori bo'lsa, Claude shuncha chuqur o'ylaydi — lekin sekinroq va qimmatroq "
    "(fikrlash ham chiqish tokeni sifatida hisoblanadi).\n\n"
    "Standart: Fable — high, Opus — medium, Sonnet — high, Haiku — medium."
)


class Cancelled(Exception):
    """Foydalanuvchi ⏹ tugmasini bosdi."""


class Debouncer:
    """Ketma-ket kelgan xabarlarni (albom, uzun matnning bo'laklari) bitta so'rovga yig'adi."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._buffers: dict[Any, list[Message]] = {}

    async def collect(self, key: Any, message: Message) -> list[Message] | None:
        buffer = self._buffers.get(key)
        if buffer is not None:
            buffer.append(message)
            return None
        self._buffers[key] = [message]
        await asyncio.sleep(self.delay)
        return self._buffers.pop(key)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    claude: ClaudeService
    prompt: PromptLoader
    albums: Debouncer = field(default_factory=lambda: Debouncer(1.2))
    texts: Debouncer = field(default_factory=lambda: Debouncer(0.7))
    locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    active: dict[int, asyncio.Task] = field(default_factory=dict)
    pending: dict[int, tuple[float, list[dict]]] = field(default_factory=dict)
    updater: UpdateManager | None = None
    limiter: AttemptLimiter = field(default_factory=AttemptLimiter)
    prompted: set[int] = field(default_factory=set)  # parol so'ralgan (birinchi xabari kelgan) begonalar
    awaiting_update: dict[int, float] = field(default_factory=dict)
    staged: dict[int, StagedUpdate] = field(default_factory=dict)
    restart_requested: bool = False
    restart_delay: float = 1.0
    dispatcher: Dispatcher | None = None

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.settings.owner_ids

    async def user_settings(self, user_id: int) -> UserSettings:
        return await self.db.get_settings(user_id, self.settings.default_model, self.settings.web_search_default)

    async def request_restart(self) -> None:
        """Polling'ni to'xtatadi; app.run() 75 kodi bilan chiqadi va launcher botni qayta ishga tushiradi."""
        self.restart_requested = True
        if self.dispatcher is not None:
            # Polling keyingi getUpdates so'rovini yuborib ulgursin — shu tugma bosilishi Telegram'da
            # «qabul qilindi» deb belgilanadi va yangi versiyaga qayta kelmaydi
            await asyncio.sleep(self.restart_delay)
            with contextlib.suppress(RuntimeError):
                await self.dispatcher.stop_polling()

    def lock_for(self, user_id: int) -> asyncio.Lock:
        return self.locks.setdefault(user_id, asyncio.Lock())

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.settings.timezone))

    def _fresh_pending(self, user_id: int) -> list[dict]:
        stamp, blocks = self.pending.get(user_id, (0.0, []))
        return blocks if time.monotonic() - stamp <= PENDING_TTL else []

    def add_pending(self, user_id: int, blocks: list[dict]) -> int:
        combined = self._fresh_pending(user_id) + blocks
        self.pending[user_id] = (time.monotonic(), combined)
        return len(combined)

    def take_pending(self, user_id: int) -> list[dict]:
        blocks = self._fresh_pending(user_id)
        self.pending.pop(user_id, None)
        return blocks


class AccessMiddleware(BaseMiddleware):
    """Kirish nazorati: egasi (OWNER_IDS) va parol bilan kirgan a'zolar o'tadi, qolganlardan parol so'raladi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx: AppContext = data["ctx"]
        user = data.get("event_from_user")
        if user is None:
            return None
        if ctx.is_owner(user.id):
            return await handler(event, data)
        member = await ctx.db.member(user.id)
        if member is not None and member.active:
            return await handler(event, data)
        with contextlib.suppress(TelegramAPIError):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Ruxsat yo'q")
            elif isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
                if member is not None:
                    await event.answer("⛔ Bot egasi sizning kirishingizni yopgan.")
                else:
                    await password_gate(event, data["bot"], ctx, user)
        return None


async def password_gate(message: Message, bot: Bot, ctx: AppContext, user: User) -> None:
    stored = await ctx.db.get_config(PASSWORD_KEY)
    if not stored:
        await message.answer(f"⛔ Bu shaxsiy bot.\nSizning Telegram ID'ingiz: {user.id}")
        return
    first_contact = user.id not in ctx.prompted
    ctx.prompted.add(user.id)
    attempt = (message.text or "").strip()
    if not attempt or attempt.startswith("/"):
        await branding.send_banner(bot, ctx.db, message.chat.id, "locked", LOCKED_TEXT)
        return
    wait = ctx.limiter.locked_for(user.id)
    if wait:
        with contextlib.suppress(TelegramAPIError):
            await message.delete()
        await message.answer(f"⏳ Juda ko'p xato urinish. {math.ceil(wait / 60)} daqiqadan keyin qayta urinib ko'ring.")
        return
    correct = await asyncio.to_thread(verify_password, attempt, stored)
    if not correct and first_contact:
        # Birinchi xabar odatda salomlashish — xato urinish deb hisoblamaymiz, avval nima kerakligini aytamiz
        await branding.send_banner(bot, ctx.db, message.chat.id, "locked", LOCKED_TEXT)
        return
    with contextlib.suppress(TelegramAPIError):
        await message.delete()  # parol (yoki uning taxmini) chatda qolmasin
    if not correct:
        left = ctx.limiter.fail(user.id)
        if left:
            await message.answer(f"❌ Parol noto'g'ri. Yana {left} ta urinish qoldi.")
        else:
            await message.answer("⛔ Juda ko'p xato urinish. 30 daqiqadan keyin qayta urinib ko'ring.")
        return
    ctx.limiter.reset(user.id)
    await ctx.db.add_member(user.id, user.full_name, user.username)
    await message.answer("✅ Parol to'g'ri — xush kelibsiz!")
    await send_welcome(bot, ctx, message.chat.id, user)
    who = html.escape(user.full_name or "Nomsiz") + (f" (@{html.escape(user.username)})" if user.username else "")
    for owner_id in ctx.settings.owner_ids:
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(
                chat_id=owner_id,
                text=f"👤 Yangi foydalanuvchi parol bilan kirdi: <b>{who}</b>, ID {user.id}\nRo'yxat: /foydalanuvchilar",
                parse_mode=ParseMode.HTML,
            )


def billing_cycle_start(now: datetime, reset_day: int) -> datetime:
    """Kredit davri boshlanishi: oyning reset_day-kuni (bugun undan oldin bo'lsa — o'tgan oyniki)."""
    start = now.replace(day=reset_day, hour=0, minute=0, second=0, microsecond=0)
    if now.day < reset_day:
        year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        start = start.replace(year=year, month=month)
    return start


def settings_line(s: UserSettings) -> str:
    search = "yoqilgan" if s.web_search else "o'chiq"
    return f"{label(s.active_model)} · effort: {s.effort or 'standart'} · qidiruv: {search}"


# ------------------------------------------------------------------ buyruqlar


def welcome_caption(user: User, s: UserSettings) -> str:
    name = html.escape(user.first_name or user.full_name or "do'stim", quote=False)
    return WELCOME_TEXT.format(name=name, settings=html.escape(settings_line(s), quote=False))


def menu_keyboard(s: UserSettings, owner: bool) -> InlineKeyboardMarkup:
    search = "✅" if s.web_search else "⬜"
    rows = [
        [
            InlineKeyboardButton(text="🤖 Model", callback_data="menu:model"),
            InlineKeyboardButton(text="🧠 Fikrlash", callback_data="menu:effort"),
        ],
        [
            InlineKeyboardButton(text=f"🔎 Qidiruv {search}", callback_data="menu:search"),
            InlineKeyboardButton(text="💰 Xarajat", callback_data="menu:cost"),
        ],
        [
            InlineKeyboardButton(text="🆕 Yangi suhbat", callback_data="menu:new"),
            InlineKeyboardButton(text="📖 Yordam", callback_data="menu:help"),
        ],
    ]
    if owner:
        rows.append([InlineKeyboardButton(text="⚙️ Admin panel", callback_data="menu:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_welcome(bot: Bot, ctx: AppContext, chat_id: int, user: User) -> None:
    s = await ctx.user_settings(user.id)
    await branding.send_banner(
        bot, ctx.db, chat_id, "welcome", welcome_caption(user, s), menu_keyboard(s, ctx.is_owner(user.id))
    )


async def admin_panel_text(ctx: AppContext) -> str:
    has_password = bool(await ctx.db.get_config(PASSWORD_KEY))
    active = sum(1 for m in await ctx.db.members() if m.active)
    version = html.escape(ctx.updater.version) if ctx.updater else "?"
    password = "o'rnatilgan ✅" if has_password else "o'rnatilmagan ⚠️ (/parol)"
    status = f"🏷 Versiya: <b>v{version}</b>\n🔐 Parol: {password}\n👥 Foydalanuvchilar: {active} ta\n\n"
    title, _, commands = ADMIN_HELP.partition("\n\n")
    return f"{title}\n\n{status}{commands}"


async def cmd_start(message: Message, bot: Bot, ctx: AppContext) -> None:
    await send_welcome(bot, ctx, message.chat.id, message.from_user)


async def cmd_help(message: Message, ctx: AppContext) -> None:
    text = HELP_TEXT
    if ctx.is_owner(message.from_user.id):
        text += "\n\n" + await admin_panel_text(ctx)
    await message.answer(text, parse_mode=ParseMode.HTML)


async def cb_menu(callback: CallbackQuery, ctx: AppContext) -> None:
    action = (callback.data or "").partition(":")[2]
    uid = callback.from_user.id
    message = callback.message if isinstance(callback.message, Message) else None
    if message is None:
        await callback.answer()
        return
    s = await ctx.user_settings(uid)
    if action == "model":
        await callback.answer()
        await message.answer(model_text(s), parse_mode=ParseMode.HTML, reply_markup=model_keyboard(s.model))
    elif action == "effort":
        await callback.answer()
        await message.answer(EFFORT_TEXT, parse_mode=ParseMode.HTML, reply_markup=effort_keyboard(s.effort))
    elif action == "search":
        await ctx.db.update_settings(uid, web_search=not s.web_search)
        s = await ctx.user_settings(uid)
        await callback.answer("🔎 Qidiruv yoqildi" if s.web_search else "🔎 Qidiruv o'chirildi")
        caption = welcome_caption(callback.from_user, s)
        keyboard = menu_keyboard(s, ctx.is_owner(uid))
        with contextlib.suppress(TelegramBadRequest):
            if message.photo:
                await message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                await message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif action == "cost":
        await callback.answer()
        text, keyboard = await cost_report(ctx, uid)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif action == "new":
        await ctx.db.new_conversation(uid)
        ctx.pending.pop(uid, None)
        await callback.answer("🆕 Yangi suhbat boshlandi")
        await message.answer("🆕 Yangi suhbat boshlandi — oldingi kontekst unutildi. Savolingizni yozing 👇")
    elif action == "help":
        await callback.answer()
        await message.answer(HELP_TEXT, parse_mode=ParseMode.HTML)
    elif action == "admin":
        if not ctx.is_owner(uid):
            await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
            return
        await callback.answer()
        await message.answer(await admin_panel_text(ctx), parse_mode=ParseMode.HTML)
    else:
        await callback.answer()


async def cmd_new(message: Message, ctx: AppContext) -> None:
    uid = message.from_user.id
    await ctx.user_settings(uid)
    await ctx.db.new_conversation(uid)
    ctx.pending.pop(uid, None)
    await message.answer("🆕 Yangi suhbat boshlandi — oldingi kontekst unutildi.")


def _price(value: float) -> str:
    return f"{value:g}" if value >= 1 else f"{value:.2f}"


def model_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    for model_id in SELECTABLE_MODELS:
        spec = MODELS[model_id]
        mark = "✅ " if model_id == current else ""
        text = f"{mark}{spec.label} — ${_price(spec.rates.input)} / ${_price(spec.rates.output)}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"m:{model_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_text(s: UserSettings) -> str:
    lines = ["🤖 <b>Modelni tanlang</b>", "<i>Narx: 1M token uchun, kirish / chiqish</i>", ""]
    lines += [f"• <b>{MODELS[m].label}</b> — {MODELS[m].note}" for m in SELECTABLE_MODELS]
    if s.model_override:
        lines += [
            "",
            f"↪️ Bu suhbatda hozir zaxira model ishlayapti: {label(s.model_override)}. "
            "Model tanlasangiz, u bekor bo'ladi.",
        ]
    return "\n".join(lines)


async def cmd_model(message: Message, ctx: AppContext) -> None:
    s = await ctx.user_settings(message.from_user.id)
    await message.answer(model_text(s), parse_mode=ParseMode.HTML, reply_markup=model_keyboard(s.model))


async def cb_model(callback: CallbackQuery, ctx: AppContext) -> None:
    model_id = (callback.data or "").split(":", 1)[-1]
    if model_id not in SELECTABLE_MODELS:
        await callback.answer("Noma'lum model")
        return
    uid = callback.from_user.id
    await ctx.user_settings(uid)
    await ctx.db.update_settings(uid, model=model_id, model_override=None)
    s = await ctx.user_settings(uid)
    await callback.answer(f"✅ {label(model_id)}")
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                f"{model_text(s)}\n\n✅ Tanlandi: <b>{label(model_id)}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=model_keyboard(model_id),
            )


def effort_keyboard(current: str | None) -> InlineKeyboardMarkup:
    selected = current or "default"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=("✅ " if value == selected else "") + text, callback_data=f"e:{value}")]
            for value, text in EFFORT_CHOICES
        ]
    )


async def cmd_effort(message: Message, ctx: AppContext) -> None:
    s = await ctx.user_settings(message.from_user.id)
    await message.answer(EFFORT_TEXT, parse_mode=ParseMode.HTML, reply_markup=effort_keyboard(s.effort))


async def cb_effort(callback: CallbackQuery, ctx: AppContext) -> None:
    value = (callback.data or "").split(":", 1)[-1]
    if value not in dict(EFFORT_CHOICES):
        await callback.answer("Noma'lum qiymat")
        return
    effort = None if value == "default" else value
    uid = callback.from_user.id
    await ctx.user_settings(uid)
    await ctx.db.update_settings(uid, effort=effort)
    await callback.answer(f"✅ effort: {effort or 'standart'}")
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(reply_markup=effort_keyboard(effort))


async def cmd_search(message: Message, ctx: AppContext) -> None:
    uid = message.from_user.id
    s = await ctx.user_settings(uid)
    enabled = not s.web_search
    await ctx.db.update_settings(uid, web_search=enabled)
    if enabled:
        text = (
            "🔎 Internet qidiruvi <b>yoqildi</b>. Kerak bo'lganda Claude o'zi qidiradi "
            "(har qidiruv ≈ $0.01 + o'qilgan sahifalar tokeni)."
        )
    else:
        text = "🔎 Internet qidiruvi <b>o'chirildi</b>."
    await message.answer(text, parse_mode=ParseMode.HTML)


async def cost_report(ctx: AppContext, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    s = await ctx.user_settings(user_id)
    now = ctx.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cycle_start = billing_cycle_start(now, ctx.settings.budget_reset_day)
    today, today_n = await ctx.db.spend(user_id, day_start)
    cycle, _ = await ctx.db.spend(user_id, cycle_start)
    total, total_n = await ctx.db.spend(user_id)
    budget = ctx.settings.monthly_budget
    since = f"{cycle_start.day}-{MONTHS[cycle_start.month - 1]}dan"

    lines = [
        "💰 <b>Xarajat hisoboti</b>",
        "",
        f"Bugun: <b>${today:.2f}</b> ({today_n} ta so'rov)",
        f"Joriy davr ({since}): <b>${cycle:.2f}</b>",
        f"Jami: ${total:.2f} ({total_n} ta so'rov)",
    ]
    by_model = await ctx.db.spend_by_model(user_id, cycle_start)
    if by_model:
        lines += ["", "<b>Joriy davr, modellar bo'yicha:</b>"]
        lines += [f"• {label(model)} — ${cost:.2f} ({count} ta)" for model, cost, count in by_model]
    if ctx.is_owner(user_id):
        # Kredit hammaga umumiy — egasi barcha foydalanuvchilar sarfini ko'radi
        all_today, _ = await ctx.db.spend_all(day_start)
        all_cycle, _ = await ctx.db.spend_all(cycle_start)
        cycle_line = f"👥 Barcha foydalanuvchilar, joriy davr: <b>${all_cycle:.2f}</b>"
        if budget > 0:
            cycle_line += f" / ${budget:g} ({all_cycle / budget:.0%})"
        lines += ["", cycle_line, f"👥 Barcha foydalanuvchilar, bugun: ${all_today:.2f}"]
        by_user = await ctx.db.spend_by_user(cycle_start)
        if any(uid != user_id for uid, _, _ in by_user):
            names = {m.user_id: m.display for m in await ctx.db.members()}
            lines.append("<b>Joriy davr, foydalanuvchilar bo'yicha:</b>")
            for uid, cost, count in by_user[:10]:
                who = "Siz" if uid == user_id else html.escape(names.get(uid, f"ID {uid}"))
                lines.append(f"• {who} — ${cost:.2f} ({count} ta)")
    lines += [
        "",
        "<i>Bu botning o'z hisobi (taxminiy). Shu API tashkilotidagi boshqa ilovalar sarfi bu yerda "
        "ko'rinmaydi. Aniq balans: platform.claude.com → Settings → Billing.</i>",
    ]
    toggle = ("✅" if s.show_cost else "⬜") + " Har javob ostida narxni ko'rsatish"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=toggle, callback_data="c:toggle")]])
    return "\n".join(lines), keyboard


async def cmd_cost(message: Message, ctx: AppContext) -> None:
    text, keyboard = await cost_report(ctx, message.from_user.id)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cb_cost_toggle(callback: CallbackQuery, ctx: AppContext) -> None:
    uid = callback.from_user.id
    s = await ctx.user_settings(uid)
    await ctx.db.update_settings(uid, show_cost=not s.show_cost)
    await callback.answer("Saqlandi")
    if isinstance(callback.message, Message):
        text, keyboard = await cost_report(ctx, uid)
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cb_stop(callback: CallbackQuery, ctx: AppContext) -> None:
    task = ctx.active.get(callback.from_user.id)
    if task is not None and not task.done():
        task.cancel()
        await callback.answer("⏹ To'xtatilmoqda…")
    else:
        await callback.answer("Javob allaqachon tugagan")


async def on_unknown_command(message: Message) -> None:
    await message.answer("Bunday buyruq yo'q. /yordam — buyruqlar ro'yxati.")


async def on_unsupported(message: Message) -> None:
    await message.answer(
        "🎙 Ovozli xabar, video va stikerlarni hozircha o'qiy olmayman. Matn, rasm yoki fayl yuboring."
    )


# ------------------------------------------------------------------ admin (faqat egasi)


async def _owner_only(message: Message, ctx: AppContext) -> bool:
    if ctx.is_owner(message.from_user.id):
        return True
    await message.answer("Bu buyruq faqat bot egasi uchun.")
    return False


def _short(text: str, limit: int = 24) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def cmd_password(message: Message, command: CommandObject, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    value = (command.args or "").strip()
    if not value:
        status = "o'rnatilgan ✅" if await ctx.db.get_config(PASSWORD_KEY) else "o'rnatilmagan"
        await message.answer(PASSWORD_HELP.format(status=status), parse_mode=ParseMode.HTML)
        return
    if value.lower() in ("off", "ochir", "o'chir", "yoq", "yo'q"):
        await ctx.db.delete_config(PASSWORD_KEY)
        await message.answer("🔓 Parol o'chirildi: yangi odamlar kira olmaydi. Avval kirganlar qoladi — /foydalanuvchilar")
        return
    with contextlib.suppress(TelegramAPIError):
        await message.delete()  # parol chatda ochiq qolmasin
    if len(value) < MIN_PASSWORD_LENGTH:
        await message.answer(f"⚠️ Parol kamida {MIN_PASSWORD_LENGTH} belgidan iborat bo'lsin.")
        return
    await ctx.db.set_config(PASSWORD_KEY, await asyncio.to_thread(hash_password, value))
    await message.answer(
        "✅ Parol o'rnatildi. Xavfsizlik uchun xabaringizni o'chirdim.\n"
        "Endi parolni bilgan odam botga yozib kira oladi. Kirganlar ro'yxati: /foydalanuvchilar"
    )


async def members_view(ctx: AppContext) -> tuple[str, InlineKeyboardMarkup | None]:
    members = await ctx.db.members()
    cycle_start = billing_cycle_start(ctx.now(), ctx.settings.budget_reset_day)
    spent = {uid: cost for uid, cost, _ in await ctx.db.spend_by_user(cycle_start)}
    has_password = bool(await ctx.db.get_config(PASSWORD_KEY))
    lines = [
        "👥 <b>Foydalanuvchilar</b>",
        "Parol: " + ("o'rnatilgan ✅" if has_password else "o'rnatilmagan — yangi odam kira olmaydi (/parol)"),
        "",
    ]
    active = [m for m in members if m.active]
    removed = [m for m in members if not m.active]
    if not active:
        lines.append("Hozircha parol bilan hech kim kirmagan.")
    lines += [f"• {html.escape(m.display)} — joriy davr ${spent.get(m.user_id, 0.0):.2f}" for m in active]
    if removed:
        lines += ["", "<b>Chiqarilganlar:</b>"] + [f"• {html.escape(m.display)}" for m in removed]
    rows = [
        [InlineKeyboardButton(text=f"❌ Chiqarish: {_short(m.display)}", callback_data=f"rm:{m.user_id}")]
        for m in active
    ]
    rows += [
        [InlineKeyboardButton(text=f"♻️ Qaytarish: {_short(m.display)}", callback_data=f"rs:{m.user_id}")]
        for m in removed
    ]
    return "\n".join(lines), (InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


async def cmd_members(message: Message, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    text, keyboard = await members_view(ctx)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cb_member(callback: CallbackQuery, ctx: AppContext) -> None:
    if not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q")
        return
    action, _, raw_id = (callback.data or "").partition(":")
    try:
        member_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return
    await ctx.db.set_member_active(member_id, action == "rs")
    if action == "rm":
        ctx.pending.pop(member_id, None)
        task = ctx.active.get(member_id)
        if task is not None and not task.done():
            task.cancel()
    await callback.answer("Chiqarildi" if action == "rm" else "Qaytarildi")
    if isinstance(callback.message, Message):
        text, keyboard = await members_view(ctx)
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


def _confirm_keyboard(staged: StagedUpdate) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ O'rnatish", callback_data=f"up:ok:{staged.id}"),
                InlineKeyboardButton(text="❌ Bekor", callback_data=f"up:no:{staged.id}"),
            ]
        ]
    )


def update_summary(staged: StagedUpdate) -> str:
    esc = html.escape
    lines = [
        f"📦 <b>Yangilanish:</b> v{esc(staged.current_version)} → <b>v{esc(staged.version)}</b>",
        f"Fayllar: {len(staged.files)} ta",
    ]
    if staged.version == staged.current_version:
        lines.append("ℹ️ Bu versiya allaqachon o'rnatilgan — qayta o'rnatiladi.")
    if staged.requirements_changed:
        lines.append("📚 Yangi kutubxonalar o'rnatiladi (1–2 daqiqa qo'shimcha)")
    if staged.skipped:
        more = " …" if len(staged.skipped) > 5 else ""
        lines.append(f"⏭ E'tiborsiz qoldirildi: {esc(', '.join(staged.skipped[:5]))}{more}")
    if staged.notes:
        lines += ["", esc(staged.notes[:1000])]
    lines += ["", "O'rnatilsinmi? Bot 10–30 soniyaga qayta ishga tushadi."]
    return "\n".join(lines)


async def cmd_update(message: Message, bot: Bot, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    if message.document is not None:  # .zip izohiga /yangilash yozilgan bo'lsa
        await handle_update_upload(message, bot, ctx)
        return
    ctx.awaiting_update[message.from_user.id] = time.monotonic()
    await message.answer(
        f"📦 Joriy versiya: v{ctx.updater.version}\n"
        "Yangilanish arxivini (.zip) shu chatga yuboring. Bekor qilish: /bekor"
    )


async def cmd_cancel(message: Message, ctx: AppContext) -> None:
    uid = message.from_user.id
    ctx.awaiting_update.pop(uid, None)
    staged = ctx.staged.pop(uid, None)
    if staged is not None and ctx.updater is not None:
        ctx.updater.discard(staged)
    await message.answer("Bekor qilindi.")


def _expecting_update(ctx: AppContext, message: Message) -> bool:
    uid = message.from_user.id
    if not ctx.is_owner(uid) or message.document is None or ctx.updater is None:
        return False
    if (message.caption or "").strip().lower().startswith("/yangilash"):
        return True
    started = ctx.awaiting_update.get(uid)
    return started is not None and time.monotonic() - started <= UPDATE_WAIT_SECONDS


async def handle_update_upload(message: Message, bot: Bot, ctx: AppContext) -> None:
    uid = message.from_user.id
    ctx.awaiting_update.pop(uid, None)
    document = message.document
    name = document.file_name or "arxiv"
    if not name.lower().endswith(".zip"):
        await message.answer("⚠️ Yangilanish .zip arxiv bo'lishi kerak. Qayta urinish: /yangilash")
        return
    if document.file_size and document.file_size > MAX_DOWNLOAD:
        await message.answer("⚠️ Arxiv juda katta (20 MB gacha bo'lsin).")
        return
    status = await message.answer("🔍 Arxiv tekshirilmoqda…")
    try:
        data = await _download(bot, document.file_id)
        staged = await asyncio.to_thread(ctx.updater.stage, data)
    except UpdateError as exc:
        await status.edit_text(f"❌ Yangilanish rad etildi: {exc}\n\nJoriy versiya o'zgarmadi.")
        return
    except TelegramAPIError as exc:
        log.warning("Yangilanish arxivini yuklab bo'lmadi: %s", exc)
        await status.edit_text("❌ Arxivni Telegram'dan yuklab bo'lmadi. Qayta urinib ko'ring: /yangilash")
        return
    except Exception:
        log.exception("Yangilanish arxivini tekshirishda xato")
        await status.edit_text("❌ Arxivni ochib bo'lmadi. Joriy versiya o'zgarmadi.")
        return
    old = ctx.staged.pop(uid, None)
    if old is not None and old.path != staged.path:
        ctx.updater.discard(old)
    ctx.staged[uid] = staged
    await status.edit_text(update_summary(staged), parse_mode=ParseMode.HTML, reply_markup=_confirm_keyboard(staged))


async def cmd_version(message: Message, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    versions = await asyncio.to_thread(ctx.updater.backup_versions)
    lines = [f"🏷 Versiya: <b>v{html.escape(ctx.updater.version)}</b>", ""]
    if versions:
        tz = ZoneInfo(ctx.settings.timezone)
        lines.append("💾 Qaytish mumkin bo'lgan versiyalar (eng yangisi birinchi):")
        for version, created in versions:
            when = f" — {created.astimezone(tz):%d.%m.%Y %H:%M}" if created else ""
            lines.append(f"• v{html.escape(version)}{when}")
        lines.append("\nOldingi versiyaga qaytish: /qaytarish")
    else:
        lines.append("💾 Zaxira nusxa hali yo'q — u birinchi /yangilash da paydo bo'ladi.")
    lines.append("Botning joriy kodi: /kod")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_source(message: Message, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    data = await asyncio.to_thread(ctx.updater.export_source)
    version = re.sub(r"[^0-9A-Za-z._-]", "_", ctx.updater.version)
    await message.answer_document(
        BufferedInputFile(data, filename=f"claude-telegram-bot-v{version}.zip"),
        caption=(
            "📦 Botning joriy kodi (.env va baza kirmagan — maxfiy narsa yo'q).\n"
            "Yangi funksiya kerak bo'lsa, shu faylni Claude'ga yuborib, nima qo'shishni yozing."
        ),
    )


async def cmd_restore(message: Message, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    try:
        staged = await asyncio.to_thread(ctx.updater.stage_restore)
    except UpdateError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    old = ctx.staged.pop(message.from_user.id, None)
    if old is not None and old.path != staged.path:
        ctx.updater.discard(old)
    ctx.staged[message.from_user.id] = staged
    current = html.escape(staged.current_version)
    await message.answer(
        f"↩️ Oldingi versiyaga qaytish: v{current} → <b>v{html.escape(staged.version)}</b>\n"
        f"Hozirgi v{current} o'chiriladi (kerak bo'lsa, uning .zip faylini qayta yuborasiz).\n\n"
        "Tasdiqlaysizmi? Bot 10–30 soniyaga qayta ishga tushadi.",
        parse_mode=ParseMode.HTML,
        reply_markup=_confirm_keyboard(staged),
    )


async def cb_update(callback: CallbackQuery, ctx: AppContext) -> None:
    uid = callback.from_user.id
    if not ctx.is_owner(uid):
        await callback.answer("⛔ Ruxsat yo'q")
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, action, stage_id = parts
    staged = ctx.staged.get(uid)
    message = callback.message if isinstance(callback.message, Message) else None
    if staged is None or staged.id != stage_id:
        await callback.answer("Bu so'rov eskirgan. Qaytadan: /yangilash", show_alert=True)
        return
    if action == "no":
        ctx.staged.pop(uid, None)
        ctx.updater.discard(staged)
        await callback.answer("Bekor qilindi")
        if message:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text("❌ Bekor qilindi. Joriy versiya o'zgarmadi.")
        return
    if any(not task.done() for task in ctx.active.values()):
        await callback.answer("Hozir javob yozilmoqda — tugagach qayta bosing.", show_alert=True)
        return
    ctx.staged.pop(uid, None)
    try:
        await asyncio.to_thread(ctx.updater.apply, staged, chat_id=uid)
    except Exception as exc:  # noqa: BLE001 — har qanday xatoni egasiga ko'rsatamiz
        log.exception("Yangilanishni o'rnatib bo'lmadi")
        await callback.answer()
        if message:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(f"❌ O'rnatishda xato: {exc}\nJoriy versiya o'zgarmadi.")
        return
    await callback.answer("O'rnatilmoqda…")
    if message:
        done = "tiklandi" if staged.is_restore else "o'rnatildi"
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_text(
                f"⏳ v{staged.version} {done}, bot qayta ishga tushmoqda… Natijasini birozdan keyin yozaman."
            )
    await ctx.request_restart()


# ------------------------------------------------------------------ xabarlar


async def _download(bot: Bot, file_id: str) -> bytes:
    buffer = await bot.download(file_id, timeout=120)
    return buffer.getvalue()


async def read_attachment(bot: Bot, message: Message, max_edge: int) -> list[dict]:
    if message.photo:
        data = await _download(bot, message.photo[-1].file_id)
        return [await asyncio.to_thread(media.image_block, data, max_edge)]
    document = message.document
    if document is None:
        return []
    name = document.file_name or "fayl"
    if document.file_size and document.file_size > MAX_DOWNLOAD:
        raise media.MediaError(f"«{name}» juda katta — Telegram botlari faqat 20 MB gacha faylni yuklab oladi.")
    kind = media.classify(name, document.mime_type or "")
    if kind is None:
        raise media.MediaError(
            f"«{name}» turidagi faylni o'qiy olmayman. Rasm, PDF, Word (.docx), Excel (.xlsx) yoki matn fayl yuboring."
        )
    data = await _download(bot, document.file_id)
    return [await asyncio.to_thread(media.build_block, kind, data, name, max_edge)]


async def on_attachment(message: Message, bot: Bot, ctx: AppContext) -> None:
    if _expecting_update(ctx, message):
        await handle_update_upload(message, bot, ctx)
        return
    group = [message]
    if message.media_group_id:
        collected = await ctx.albums.collect(message.media_group_id, message)
        if collected is None:
            return
        group = sorted(collected, key=lambda m: m.message_id)
    uid = message.from_user.id
    blocks: list[dict] = []
    errors: list[str] = []
    for item in group:
        try:
            blocks.extend(await read_attachment(bot, item, ctx.settings.image_max_edge))
        except media.MediaError as exc:
            errors.append(f"⚠️ {exc}")
        except TelegramAPIError as exc:
            log.warning("Faylni yuklab bo'lmadi: %s", exc)
            errors.append("⚠️ Faylni Telegram'dan yuklab bo'lmadi.")
    if errors:
        await message.answer("\n".join(errors))
    if not blocks:
        return
    caption = "\n".join(item.caption for item in group if item.caption).strip()
    if not caption:
        count = ctx.add_pending(uid, blocks)
        await message.answer(
            f"📎 Qabul qilindi ({count} ta fayl). Endi savolingizni yozing — masalan: «qisqacha mazmunini ayt»."
        )
        return
    await run_chat(group[-1], bot, ctx, ctx.take_pending(uid) + blocks + [{"type": "text", "text": caption}])


def _quoted(message: Message, bot_id: int) -> str | None:
    """Foydalanuvchi boshqa xabarga javob qaytarsa (reply), o'sha matnni ham qo'shamiz."""
    if message.quote is not None and message.quote.text:
        return message.quote.text.strip()
    reply = message.reply_to_message
    if reply is not None and (reply.from_user is None or reply.from_user.id != bot_id):
        content = (reply.text or reply.caption or "").strip()
        return content[:4000] or None
    return None


async def on_text(message: Message, bot: Bot, ctx: AppContext) -> None:
    uid = message.from_user.id
    # Telegram uzun matnni bir nechta xabarga bo'lib yuboradi — ularni birlashtiramiz
    collected = await ctx.texts.collect(uid, message)
    if collected is None:
        return
    collected.sort(key=lambda m: m.message_id)
    text = "\n".join(m.text for m in collected if m.text).strip()
    quote = _quoted(collected[0], bot.id)
    if quote:
        text = f"[Iqtibos]\n{quote}\n[/Iqtibos]\n\n{text}"
    if not text:
        return
    await run_chat(collected[-1], bot, ctx, ctx.take_pending(uid) + [{"type": "text", "text": text}])


# ------------------------------------------------------------------ suhbat jarayoni


def refusal_message(result: TurnResult) -> str:
    category = REFUSAL_CATEGORIES.get(result.refusal_category or "", "")
    text = f"🚫 {label(result.model)} bu so'rovga javob bermadi{category}."
    if result.refusal_explanation:
        text += f"\n\nIzoh: {result.refusal_explanation}"
    return text + "\n\nSavolni boshqacha ifodalab ko'ring. Keyingi savollar ham rad etilsa, /new bilan yangi suhbat boshlang."


def _stop_notes(result: TurnResult, long_context: int) -> list[str]:
    notes = []
    if result.stop_reason == "max_tokens":
        notes.append("✂️ Javob uzunlik chegarasiga yetib kesildi. Davomi uchun «davom et» deb yozing.")
    elif result.stop_reason == "pause_turn":
        notes.append("ℹ️ Qidiruv juda uzoq davom etib to'xtatildi — javob to'liq bo'lmasligi mumkin.")
    elif result.stop_reason == "model_context_window_exceeded":
        notes.append("📚 Model kontekst chegarasiga yetdi — /new bosing.")
    if result.prompt_tokens >= long_context:
        notes.append(
            f"📚 Suhbat konteksti ≈{result.prompt_tokens // 1000}k token — har javob qimmatlashmoqda. "
            "Yangi mavzu uchun /new."
        )
    return notes


async def _footer(
    ctx: AppContext, uid: int, s: UserSettings, model: str, effort: str | None, result: TurnResult
) -> str:
    parts = [label(model)]
    if effort:
        parts.append(f"effort {effort}")
    if result.searches:
        parts.append(f"🔎 {result.searches}")
    if s.show_cost:
        now = ctx.now()
        today, _ = await ctx.db.spend(uid, now.replace(hour=0, minute=0, second=0, microsecond=0))
        parts.append(f"💰 ${result.cost:.3f}")
        parts.append(f"bugun ${today:.2f}")
        budget = ctx.settings.monthly_budget
        if budget > 0:
            # Kredit hammaga umumiy, shuning uchun ogohlantirish barcha foydalanuvchilar sarfiga qaraydi
            cycle, _ = await ctx.db.spend_all(billing_cycle_start(now, ctx.settings.budget_reset_day))
            if cycle >= 0.8 * budget:
                parts.append(f"⚠️ kredit {cycle / budget:.0%}")
    return " · ".join(parts)


async def _generate(
    ctx: AppContext,
    uid: int,
    live: LiveMessage,
    *,
    model: str,
    system: str,
    messages: list[dict],
    effort: str | None,
    web_search: bool,
    notes: list[str],
) -> TurnResult:
    async def attempt(search: bool) -> TurnResult:
        live.completed_cost = 0.0
        live.start_usage = None
        task = asyncio.create_task(
            ctx.claude.run_turn(
                model=model, system=system, messages=messages, effort=effort, web_search=search, listener=live
            )
        )
        ctx.active[uid] = task
        try:
            return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if task.cancelled() and not (current is not None and current.cancelling()):
                # ⏹ bosildi: shu paytgacha ketgan xarajatni taxminan yozib qo'yamiz
                numbers, cost = ctx.claude.estimate_partial(model, live.start_usage, live.text[live.text_mark:])
                await ctx.db.log_usage(uid, model, numbers, cost + live.completed_cost, estimated=True)
                raise Cancelled() from None
            task.cancel()
            raise
        finally:
            if ctx.active.get(uid) is task:
                del ctx.active[uid]

    try:
        result = await attempt(web_search)
    except ClaudeError as exc:
        if not (exc.search_problem and web_search):
            raise
        notes.append("🔎 Internet qidiruvi bu safar ishlamadi — javob qidiruvsiz berildi.")
        result = await attempt(False)
    await ctx.db.log_usage(uid, model, result.numbers, result.cost)
    return result


async def run_chat(message: Message, bot: Bot, ctx: AppContext, user_blocks: list[dict]) -> None:
    lock = ctx.lock_for(message.from_user.id)
    if lock.locked():
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(
                chat_id=message.chat.id, text="⏳ Oldingi javob hali yozilmoqda — bu xabar navbatga qo'yildi."
            )
    async with lock:
        await _chat(message, bot, ctx, user_blocks)


async def _chat(message: Message, bot: Bot, ctx: AppContext, user_blocks: list[dict]) -> None:
    uid = message.from_user.id
    s = await ctx.user_settings(uid)
    model = s.active_model
    # Zaxira model ishlayotganda effort yuborilmaydi (u modelning standarti bilan ishlaydi)
    effort = None if s.model_override else s.effort
    notes: list[str] = []

    history = await ctx.db.history(uid, s.conv_id, s.history_start_id)
    limit = ctx.settings.history_max_messages
    if len(history) >= limit:
        # Eskilarini bir yo'la chiqaramiz: boshlanish nuqtasi barqaror qolsa, kesh ishlayveradi
        cut = len(history) - max(limit // 2, 2)
        while cut < len(history) and history[cut].role != "user":
            cut += 1
        new_start = history[cut].id if cut < len(history) else history[-1].id + 1
        await ctx.db.update_settings(uid, history_start_id=new_start)
        history = history[cut:]
        notes.append("ℹ️ Suhbat uzun bo'lgani uchun eng eski xabarlar kontekstdan chiqarildi. Yangi mavzu uchun /new.")

    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": user_blocks})
    system = ctx.prompt.build()

    live = await LiveMessage.start(bot, message.chat.id, message.message_id, label(model))
    try:
        result = await _generate(
            ctx, uid, live, model=model, system=system, messages=messages,
            effort=effort, web_search=s.web_search, notes=notes,
        )
        fallback = ctx.settings.fallback_model
        if result.refused and fallback and fallback != model:
            refused_model, model, effort = model, fallback, None
            category = REFUSAL_CATEGORIES.get(result.refusal_category or "", "")
            live.reset(
                f"⚠️ {label(refused_model)} bu so'rovni rad etdi{category}. {label(model)} bilan qayta urinilmoqda…",
                label(model),
            )
            result = await _generate(
                ctx, uid, live, model=model, system=system, messages=messages,
                effort=effort, web_search=s.web_search, notes=notes,
            )
            if not result.refused:
                await ctx.db.update_settings(uid, model_override=model)
                notes.append(
                    f"↪️ {label(refused_model)} rad etgani uchun javobni {label(model)} berdi. "
                    f"Bu suhbat oxirigacha {label(model)} ishlaydi — /new yoki /model bilan qaytasiz."
                )
        if result.refused:
            await live.fail(refusal_message(result))
            return

        await ctx.db.add_turn(uid, s.conv_id, user_blocks, result.text or "(bo'sh javob)")
        notes.extend(_stop_notes(result, ctx.settings.long_context_tokens))
        footer = await _footer(ctx, uid, s, model, effort, result)
        await live.finish(format_answer(result.text, result.sources, notes, footer))
    except Cancelled:
        partial = live.text.strip()
        if partial:
            await ctx.db.add_turn(uid, s.conv_id, user_blocks, partial + "\n\n[javob foydalanuvchi tomonidan to'xtatildi]")
        notes.append("⏹ Javob to'xtatildi.")
        await live.finish(format_answer(partial, [], notes, None))
    except ClaudeError as exc:
        await live.fail(exc.user_message)
    except Exception:
        log.exception("Kutilmagan xato")
        await live.fail(
            "⚠️ Kutilmagan xato yuz berdi. Qayta urinib ko'ring; takrorlansa, serverdagi loglarni tekshiring "
            "(docker compose logs)."
        )
    finally:
        await live.stop_ticker()


# ------------------------------------------------------------------ yo'naltirish


def build_router() -> Router:
    router = Router(name="claude-bot")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_start, Command("menu", "menyu"))
    router.message.register(cmd_help, Command("yordam", "help"))
    router.message.register(cmd_new, Command("new", "yangi"))
    router.message.register(cmd_model, Command("model"))
    router.message.register(cmd_effort, Command("effort"))
    router.message.register(cmd_search, Command("qidiruv", "search"))
    router.message.register(cmd_cost, Command("xarajat", "cost"))
    # admin (faqat egasi; tekshiruv har bir handler ichida)
    router.message.register(cmd_update, Command("yangilash", "update"))
    router.message.register(cmd_version, Command("versiya", "version"))
    router.message.register(cmd_restore, Command("qaytarish", "rollback"))
    router.message.register(cmd_cancel, Command("bekor", "cancel"))
    router.message.register(cmd_password, Command("parol", "password"))
    router.message.register(cmd_members, Command("foydalanuvchilar", "users"))
    router.message.register(cmd_source, Command("kod", "source"))
    router.message.register(on_unknown_command, F.text.startswith("/"))
    router.message.register(on_attachment, F.photo | F.document)
    router.message.register(on_unsupported, F.voice | F.audio | F.video | F.video_note | F.sticker)
    router.message.register(on_text, F.text)

    router.callback_query.register(cb_model, F.data.startswith("m:"))
    router.callback_query.register(cb_effort, F.data.startswith("e:"))
    router.callback_query.register(cb_cost_toggle, F.data == "c:toggle")
    router.callback_query.register(cb_stop, F.data == "stop")
    router.callback_query.register(cb_menu, F.data.startswith("menu:"))
    router.callback_query.register(cb_member, F.data.startswith("rm:") | F.data.startswith("rs:"))
    router.callback_query.register(cb_update, F.data.startswith("up:"))
    return router

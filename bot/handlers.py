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

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import AiogramError, TelegramBadRequest
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
    WebAppInfo,
)

from . import branding, export, links, media, scheduler, tools, translit
from .access import MIN_PASSWORD_LENGTH, AttemptLimiter, hash_password, verify_password
from .claude import ClaudeError, ClaudeService, TurnResult
from .config import Settings
from .db import Conversation, Database, UserSettings
from .filestore import FileStore
from .live import LiveMessage
from .models import AUTO_MODEL, MODELS, SELECTABLE_MODELS, label
from .modes import DEFAULT_MODE, MODES, load_mode_prompt, mode_label
from .prompt import MONTHS, PromptLoader
from .reports.analysis import USAGE_NAME as REPORT_USAGE_NAME
from .reports.analysis import USAGE_UID as REPORT_USAGE_UID
from .reports.assistant import REPORT_GUIDE, leader_system
from .tgformat import format_answer, split_plain
from .github import GitHubClient, GitHubError, is_newer
from .tools import RawFile, RawStore, ToolContext
from .updater import (
    CALLBACK_GITHUB_CHECK,
    CALLBACK_GITHUB_INSTALL,
    MAX_CHANGED_SHOWN,
    StagedUpdate,
    UpdateError,
    UpdateManager,
    describe_release,
    fetch_release,
)

log = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024  # Telegram Bot API faqat 20 MB gacha faylni beradi
# Haqiqiy buyruq ko'rinishi: «/so'z» yoki «/so'z@bot» — «/etc/nginx…», «// izoh» kabi matn Claude'ga boradi
COMMAND_LIKE = r"^/[A-Za-z0-9_]{1,32}(?:@\w+)?(?:\s|$)"
PENDING_TTL = 3600  # izohsiz yuborilgan fayl 1 soat savolni kutadi

COMMANDS = [
    BotCommand(command="start", description="🏠 Bosh menyu"),
    BotCommand(command="new", description="🆕 Yangi suhbat"),
    BotCommand(command="model", description="🤖 Modelni tanlash"),
    BotCommand(command="effort", description="🧠 Fikrlash darajasi"),
    BotCommand(command="rejim", description="🎯 Ish rejimi: xat, post, reklama, tarjima"),
    BotCommand(command="tarix", description="🗂 Suhbatlar tarixi"),
    BotCommand(command="shablon", description="📌 Tayyor so'rovlar (shablonlar)"),
    BotCommand(command="qidiruv", description="🔎 Internet qidiruvi: yoqish/o'chirish"),
    BotCommand(command="xarajat", description="💰 Xarajat hisoboti"),
    BotCommand(command="kirill", description="🔡 Lotin → kirill (matn yoki javobga reply)"),
    BotCommand(command="ilova", description="📱 Ilova: sozlamalar, tarix, fayllar, xarajat"),
    BotCommand(command="yordam", description="📖 Yordam"),
]
OWNER_COMMANDS = COMMANDS + [
    BotCommand(command="zaxira", description="💾 Bazaning zaxira nusxasini hozir olish"),
    BotCommand(command="yangilash", description="📦 Botni yangilash (.zip yoki GitHub)"),
    BotCommand(command="versiya", description="🏷 Versiya va zaxiralar"),
    BotCommand(command="qaytarish", description="↩️ Oldingi versiyaga qaytish"),
    BotCommand(command="parol", description="🔐 Kirish paroli"),
    BotCommand(command="foydalanuvchilar", description="👥 Foydalanuvchilar"),
    BotCommand(command="kod", description="💾 Botning joriy kodi (.zip)"),
    BotCommand(command="hisobot", description="📊 Call center hisoboti (hozirgi holat)"),
    BotCommand(command="guruhlar", description="👥 Kuzatiladigan guruhlar"),
    BotCommand(command="vazifa", description="📋 Vazifa berish (#vazifa @username …)"),
    BotCommand(command="vazifalar", description="🗂 Ochiq vazifalar"),
    BotCommand(command="kundalik", description="📓 Kundalik: bugungi raqamlar va vazifalar"),
    BotCommand(command="excel", description="📊 Haftalik / oylik Excel hisobot"),
]
UPDATE_WAIT_SECONDS = 15 * 60
BUSY_QUEUED_TEXT = "⏳ Oldingi xabaringiz hali ishlanmoqda — bu xabar navbatga qo'yildi."
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
    "🔗 <b>Havola yuboring</b> — maqola yoki PDF havolasini yuborsangiz, uni o'qib chiqaman.\n\n"
    "📄 <b>Fayl yaratish</b> — «Word qilib ber», «Excel jadval tuz», «taqdimot tayyorla», «PDF» desangiz, "
    "faylni yasab yuboraman. Skaner rasmini Word'ga, Word faylni formatini saqlab tarjimaga, "
    "PDF'larni birlashtirish/bo'lishga, ikki hujjatni solishtirishga ham so'rang.\n\n"
    "🔘 <b>Javob ostidagi tugmalar</b> — qayta yozish, qisqartirish, tarjima, Word fayl, kirill yozuvi.\n\n"
    "<b>Buyruqlar</b>\n"
    "🏠 /start — bosh menyu\n"
    "🆕 /new — yangi suhbat (kontekst tozalanadi, javoblar arzonlashadi)\n"
    "🤖 /model — model: ✨ Avto, Fable, Opus, Sonnet, Haiku\n"
    "🎯 /rejim — ish rejimi: rasmiy xat, Instagram post, Meta reklama, tarjimon, dasturchi\n"
    "🗂 /tarix — eski suhbatlar, qidiruv va davom ettirish\n"
    "📌 /shablon — tez-tez ishlatiladigan so'rovlar\n"
    "🧠 /effort — fikrlash darajasi\n"
    "🔎 /qidiruv — internet qidiruvini yoqish/o'chirish\n"
    "💰 /xarajat — sarf hisoboti\n"
    "🔡 /kirill, /lotin — yozuvni o'girish\n"
    "📱 /ilova — Mini App: sozlamalar, suhbatlar, fayllar galereyasi, xarajat grafigi"
)

APP_TEXT = (
    "📱 <b>TTA Yordamchi ilovasi</b>\n\n"
    "Sozlamalar, suhbatlar tarixi, yaratilgan fayllar, xarajat grafigi — bir ekranda. "
    "Tugmani bosing yoki chatning pastki chap burchagidagi menyu tugmasidan oching."
)
APP_REPORT_TEXT = (
    "\n\n📊 Ochilganda bo'lim tanlanadi: <b>AI yordamchi</b> yoki <b>Hisobotlar</b> (call center natijalari, "
    "operatorlar, guruhlar va hisobot vaqti)."
)
APP_DISABLED_TEXT = (
    "📱 Ilova hali ulanmagan: serverdagi .env faylida WEBAPP_URL ko'rsatilmagan (README → Mini App)."
)

ADMIN_HELP = (
    "⚙️ <b>Admin panel</b> <i>(faqat siz uchun)</i>\n\n"
    "📦 /yangilash — botni yangilash: buyruqdan keyin .zip yuborasiz; /yangilash github — so'nggi versiyani "
    "GitHub'dan o'zi yuklaydi; /yangilash tekshir — yangi versiya bormi\n"
    "🏷 /versiya — joriy versiya va zaxira nusxalar\n"
    "↩️ /qaytarish — oldingi versiyaga qaytish\n"
    "🔐 /parol — kirish parolini o'rnatish yoki o'zgartirish\n"
    "👥 /foydalanuvchilar — parol bilan kirganlar, chiqarib yuborish\n"
    "💾 /zaxira — bazaning zaxira nusxasini hozir olish (har kuni avtomatik ham keladi)\n"
    "💾 /kod — botning joriy kodi (.zip): yangi funksiya so'raganda Claude'ga yuborasiz\n"
    "📊 /hisobot — call center hisoboti: hozirgi holat va rahbarlarga yuborish\n"
    "👥 /guruhlar — kuzatiladigan guruhlar (lidlar, rahbarlar, vazifalar) va ularning turi\n"
    "📋 /vazifa — vazifa berish: «#vazifa @username nima, qachongacha» (guruhda yoki shu yerda)\n"
    "🗂 /vazifalar — ochiq vazifalar va ularning holati\n"
    "📓 /kundalik — bugungi kundalik: call center raqamlari va vazifalar (ish kunlari 20:00 da o'zi keladi)\n"
    "📊 /excel — haftalik yoki oylik Excel hisobot (dushanba va oyning 1-sanasi o'zi keladi)\n"
    "💬 Hisobot savollari — chatda oddiy yozing: «bugun nechta qo'ng'iroq bo'ldi?», «kecha kimlar keladi dedi?», "
    "«Malika vazifani bajardimi?» — AI hisobot ma'lumotidan javob beradi. Rahbarlar ham botga yozib so'raydi "
    "(telefonlar yashirilgan, kuniga LEADER_DAILY_LIMIT ta savol)"
)

MODE_TEXT = (
    "🎯 <b>Ish rejimi</b>\n\n"
    "Rejim botga qanday javob berishni aytadi va /new bosilganda ham saqlanadi.\n\n"
    + "\n".join(f"{m.emoji} <b>{m.label}</b> — {m.hint}" for m in MODES.values())
)

TEMPLATE_HELP = (
    "📌 <b>Shablonlar</b> — tez-tez yuboriladigan so'rovlar.\n\n"
    "Qo'shish: <code>/shablon qo'sh Nomi</code> va keyingi qatorda matn, masalan:\n"
    "<code>/shablon qo'sh Post\n"
    "Shu mavzuda Instagram post yoz: </code>\n\n"
    "O'chirish: <code>/shablon o'chir Nomi</code>\n"
    "Tugmani bossangiz, shablon matni savol sifatida yuboriladi."
)

LEVEL_INDEX = {"simple": 0, "medium": 1, "complex": 2}
BUDGET_LEVELS = (50, 80, 100)
LANGS = {"uz": "o'zbek tiliga (lotin yozuvida)", "ru": "rus tiliga", "en": "ingliz tiliga"}

WELCOME_TEXT = (
    "👋 <b>Salom, {name}!</b>\n"
    "Men — <b>TTA Yordamchi</b>, Claude asosidagi shaxsiy AI yordamchingiz.\n\n"
    "✍️ Savol bering, matn yozdiring, tarjima qildiring\n"
    "📎 Rasm, PDF, Word, Excel yuboring — tahlil qilaman\n"
    "📄 Word, Excel, PowerPoint, PDF fayllar yasab beraman\n"
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
    raw: RawStore = field(default_factory=RawStore)  # yuborilgan fayllarning asl nusxasi (vositalar uchun)
    files: FileStore | None = None  # yaratilgan fayllar galereyasi (Mini App)
    updater: UpdateManager | None = None
    github: GitHubClient | None = None  # GitHub'dan yangilash (GITHUB_REPO); None — o'chiq
    limiter: AttemptLimiter = field(default_factory=AttemptLimiter)
    prompted: set[int] = field(default_factory=set)  # parol so'ralgan (birinchi xabari kelgan) begonalar
    awaiting_update: dict[int, float] = field(default_factory=dict)
    staged: dict[int, StagedUpdate] = field(default_factory=dict)
    github_busy: set[int] = field(default_factory=set)  # GitHub'dan yuklash davom etayotgan egalar
    github_cancelled: set[int] = field(default_factory=set)  # yuklash paytida /bekor bosganlar
    busy: dict[int, str] = field(default_factory=dict)  # qulf nima uchun band: «file» — fayl yuklanmoqda
    staging_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # arxivlar bir vaqtda ochilmasin (data/update)
    restart_requested: bool = False
    restart_delay: float = 1.0
    dispatcher: Dispatcher | None = None
    reports: Any = None  # bot/reports/service.py: ReportService (call center hisobotlari)

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
    """Kirish nazorati: egasi (OWNER_IDS) va parol bilan kirgan a'zolar o'tadi, qolganlardan parol so'raladi.

    Guruh xabarlari hisobot uchun jim yozib boriladi (javob berilmaydi); hisobotga qo'shilgan rahbar va
    operatorlarga shaxsiy chatda AI o'rniga qisqa yo'riqnoma ko'rsatiladi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        ctx: AppContext = data["ctx"]
        if isinstance(event, Message) and event.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            return await handler(event, data)  # faqat hisobot yozuvchisi ishlaydi (bot/reports)
        if isinstance(event, CallbackQuery) and (event.data or "").startswith("tq:"):
            return await handler(event, data)  # «Bajardingizmi?» — ijrochini handler o'zi tekshiradi
        user = data.get("event_from_user")
        if user is None:
            return None
        role = None
        if isinstance(event, Message) and event.chat.type == ChatType.PRIVATE and ctx.reports is not None:
            try:  # ro'yxatdagi xodim botga yozdi: ID bog'lanadi, operator eslatma oladi (egasi/a'zo bo'lsa ham)
                role = await ctx.reports.staff_role(user)
            except Exception:  # noqa: BLE001 — hisobot xatosi suhbatni to'xtatmasin
                log.exception("Xodim rolini aniqlashda xato")
        if ctx.is_owner(user.id):
            return await handler(event, data)
        member = await ctx.db.member(user.id)
        if member is not None and member.active:
            return await handler(event, data)
        if role is not None and not await is_password(event, ctx, user):  # parol yozsa — odatdagidek kiradi
            text = (event.text or "").strip() if isinstance(event, Message) else ""
            if role == "leader" and text and not text.startswith("/"):
                await leader_answer(event, data["bot"], ctx)  # rahbar savoli — AI hisobotdan javob beradi
                return None
            with contextlib.suppress(AiogramError):
                await ctx.reports.staff_message(event, role)
            return None
        with contextlib.suppress(AiogramError):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Ruxsat yo'q")
            elif isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
                if member is not None:
                    await event.answer("⛔ Bot egasi sizning kirishingizni yopgan.")
                else:
                    await password_gate(event, data["bot"], ctx, user)
        return None


async def is_password(message: Message, ctx: AppContext, user: User) -> bool:
    """Xodim (operator, rahbar) botga yozgan matn kirish paroli bo'lsa True — odatdagidek a'zo bo'ladi.

    Parolga o'xshash (kamida MIN_PASSWORD_LENGTH belgi) noto'g'ri matn ham urinishlar chegarasiga kiradi:
    xodim yo'li parolni cheksiz taxmin qilish uchun ishlatilmasin. Bloklansa ham yo'riqnoma xabari chiqaveradi."""
    stored = await ctx.db.get_config(PASSWORD_KEY)
    attempt = (message.text or "").strip()
    if not stored or len(attempt) < MIN_PASSWORD_LENGTH or attempt.startswith("/") or ctx.limiter.locked_for(user.id):
        return False
    if await asyncio.to_thread(verify_password, attempt, stored):
        return True
    ctx.limiter.fail(user.id)
    return False


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
        with contextlib.suppress(AiogramError):
            await message.delete()
        await message.answer(f"⏳ Juda ko'p xato urinish. {math.ceil(wait / 60)} daqiqadan keyin qayta urinib ko'ring.")
        return
    correct = await asyncio.to_thread(verify_password, attempt, stored)
    if not correct and first_contact:
        # Birinchi xabar odatda salomlashish — xato urinish deb hisoblamaymiz, avval nima kerakligini aytamiz
        await branding.send_banner(bot, ctx.db, message.chat.id, "locked", LOCKED_TEXT)
        return
    with contextlib.suppress(AiogramError):
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
        with contextlib.suppress(AiogramError):
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
    line = f"{label(s.active_model)} · effort: {s.effort or 'standart'} · qidiruv: {search}"
    if (s.mode or DEFAULT_MODE) != DEFAULT_MODE:
        line += f" · rejim: {mode_label(s.mode)}"
    return line


# ------------------------------------------------------------------ buyruqlar


def welcome_caption(user: User, s: UserSettings) -> str:
    name = html.escape(user.first_name or user.full_name or "do'stim", quote=False)
    return WELCOME_TEXT.format(name=name, settings=html.escape(settings_line(s), quote=False))


def app_button(url: str | None) -> InlineKeyboardButton | None:
    return InlineKeyboardButton(text="📱 Ilova", web_app=WebAppInfo(url=url)) if url else None


def menu_keyboard(s: UserSettings, owner: bool, webapp_url: str | None = None) -> InlineKeyboardMarkup:
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
            InlineKeyboardButton(text="🎯 Rejim", callback_data="menu:mode"),
            InlineKeyboardButton(text="🗂 Tarix", callback_data="menu:history"),
        ],
        [
            InlineKeyboardButton(text="🆕 Yangi suhbat", callback_data="menu:new"),
            InlineKeyboardButton(text="📖 Yordam", callback_data="menu:help"),
        ],
    ]
    last: list[InlineKeyboardButton] = []
    button = app_button(webapp_url)
    if button is not None:
        last.append(button)
    if owner:
        last.append(InlineKeyboardButton(text="⚙️ Admin panel", callback_data="menu:admin"))
    if last:
        rows.append(last)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_welcome(bot: Bot, ctx: AppContext, chat_id: int, user: User) -> None:
    s = await ctx.user_settings(user.id)
    await branding.send_banner(
        bot, ctx.db, chat_id, "welcome", welcome_caption(user, s),
        menu_keyboard(s, ctx.is_owner(user.id), ctx.settings.webapp_url),
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


async def cmd_app(message: Message, ctx: AppContext) -> None:
    button = app_button(ctx.settings.webapp_url)
    if button is None:
        await message.answer(APP_DISABLED_TEXT)
        return
    text = APP_TEXT + (APP_REPORT_TEXT if ctx.is_owner(message.from_user.id) else "")
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]))


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
        keyboard = menu_keyboard(s, ctx.is_owner(uid), ctx.settings.webapp_url)
        with contextlib.suppress(TelegramBadRequest):
            if message.photo:
                await message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                await message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif action == "cost":
        await callback.answer()
        text, keyboard = await cost_report(ctx, uid)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif action == "mode":
        await callback.answer()
        await message.answer(MODE_TEXT, parse_mode=ParseMode.HTML, reply_markup=mode_keyboard(s.mode))
    elif action == "history":
        await callback.answer()
        text, keyboard = await history_view(ctx, uid)
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
    mark = "✅ " if current == AUTO_MODEL else ""
    rows = [[InlineKeyboardButton(text=f"{mark}✨ Avto — savolga qarab tanlaydi", callback_data=f"m:{AUTO_MODEL}")]]
    for model_id in SELECTABLE_MODELS:
        spec = MODELS[model_id]
        mark = "✅ " if model_id == current else ""
        text = f"{mark}{spec.label} — ${_price(spec.rates.input)} / ${_price(spec.rates.output)}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"m:{model_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_text(s: UserSettings) -> str:
    lines = ["🤖 <b>Modelni tanlang</b>", "<i>Narx: 1M token uchun, kirish / chiqish</i>", ""]
    lines.append("• <b>✨ Avto</b> — oddiy savolga arzon, murakkabiga kuchli model (kreditni tejaydi)")
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
    if model_id not in SELECTABLE_MODELS and model_id != AUTO_MODEL:
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
            names[REPORT_USAGE_UID] = REPORT_USAGE_NAME
            if ctx.reports is not None:
                names.update(await ctx.reports.usage_names())
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


_APOSTROPHES = str.maketrans({c: "'" for c in translit.APOSTROPHES})


def _keyword(text: str) -> str:
    """Buyruq so'zini solishtirish uchun: kichik harf, ’ ʻ ‘ kabi apostroflar → '."""
    return text.strip().lower().translate(_APOSTROPHES)


async def cmd_password(message: Message, command: CommandObject, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    value = (command.args or "").strip()
    if not value:
        status = "o'rnatilgan ✅" if await ctx.db.get_config(PASSWORD_KEY) else "o'rnatilmagan"
        await message.answer(PASSWORD_HELP.format(status=status), parse_mode=ParseMode.HTML)
        return
    if _keyword(value) in ("off", "ochir", "o'chir", "yoq", "yo'q"):
        await ctx.db.delete_config(PASSWORD_KEY)
        await message.answer("🔓 Parol o'chirildi: yangi odamlar kira olmaydi. Avval kirganlar qoladi — /foydalanuvchilar")
        return
    with contextlib.suppress(AiogramError):
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
    lines = [f"📦 <b>Yangilanish:</b> v{esc(staged.current_version)} → <b>v{esc(staged.version)}</b>"]
    if staged.source:
        lines.append(f"Manba: {esc(staged.source)}")
    lines.append(f"Fayllar: {len(staged.files)} ta")
    if staged.changed:
        shown = ", ".join(staged.changed[:MAX_CHANGED_SHOWN])
        more = f" … (+{len(staged.changed) - MAX_CHANGED_SHOWN})" if len(staged.changed) > MAX_CHANGED_SHOWN else ""
        lines.append(f"✏️ O'zgargan fayllar ({len(staged.changed)}): {esc(shown)}{more}")
    elif not staged.is_restore:
        lines.append("ℹ️ Fayllar joriy versiya bilan bir xil.")
    if staged.version == staged.current_version:
        lines.append("ℹ️ Bu versiya allaqachon o'rnatilgan — qayta o'rnatiladi.")
    elif staged.source and not staged.is_restore and not is_newer(staged.version, staged.current_version):
        lines.append("⚠️ GitHub'dagi versiya joriy versiyadan eski — o'rnatsangiz, bot eski versiyaga qaytadi.")
    if staged.requirements_changed:
        lines.append("📚 Yangi kutubxonalar o'rnatiladi (1–2 daqiqa qo'shimcha)")
    if staged.skipped:
        more = " …" if len(staged.skipped) > 5 else ""
        lines.append(f"⏭ E'tiborsiz qoldirildi: {esc(', '.join(staged.skipped[:5]))}{more}")
    if staged.notes:
        lines += ["", esc(staged.notes[:1000])]
    lines += ["", "O'rnatilsinmi? Bot 10–30 soniyaga qayta ishga tushadi."]
    return "\n".join(lines)


GITHUB_OFF_TEXT = (
    "🐙 GitHub'dan yangilash o'chiq: serverdagi .env faylida GITHUB_REPO=off. Yoqish uchun qatorni olib tashlang "
    "(yoki owner/repo yozing) va docker compose up -d --force-recreate (README → GitHub'dan yangilash)."
)
UPDATE_USAGE_TEXT = (
    "Tushunarsiz parametr. Ishlating:\n"
    "/yangilash — .zip arxiv yuborish\n"
    "/yangilash github — so'nggi versiyani GitHub'dan o'rnatish\n"
    "/yangilash tekshir — yangi versiya bormi"
)


class UpdateCancelled(Exception):
    """Egasi yuklash paytida /bekor bosdi."""
GITHUB_INSTALL_WORDS = ("github", "git", "gh")
GITHUB_CHECK_WORDS = ("tekshir", "tekshirish", "check")


def _github_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🐙 GitHub'dan yangilash", callback_data=CALLBACK_GITHUB_INSTALL),
                InlineKeyboardButton(text="🔍 Yangi versiya bormi?", callback_data=CALLBACK_GITHUB_CHECK),
            ]
        ]
    )


async def cmd_update(message: Message, command: CommandObject, bot: Bot, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    if message.document is not None:  # .zip izohiga /yangilash yozilgan bo'lsa
        await handle_update_upload(message, bot, ctx)
        return
    args = (command.args or "").strip().lower()
    if args in GITHUB_INSTALL_WORDS:
        await github_update(bot, message.chat.id, message.from_user.id, ctx)
        return
    if args in GITHUB_CHECK_WORDS:
        await message.answer(await github_check_text(ctx))
        return
    if args:
        await message.answer(UPDATE_USAGE_TEXT)
        return
    ctx.awaiting_update[message.from_user.id] = time.monotonic()
    text = f"📦 Joriy versiya: v{ctx.updater.version}\nYangilanish arxivini (.zip) shu chatga yuboring. Bekor qilish: /bekor"
    if ctx.github is not None:
        text += f"\n\n🐙 Yoki GitHub'dan ({ctx.github.repo}): /yangilash github — so'nggi versiyani o'zim yuklayman."
        await message.answer(text, reply_markup=_github_keyboard())
        return
    await message.answer(text)


async def github_check_text(ctx: AppContext) -> str:
    """/yangilash tekshir: GitHub'da yangi versiya bormi — faqat ma'lumot, yuklamaydi."""
    if ctx.github is None:
        return GITHUB_OFF_TEXT
    current = ctx.updater.version
    try:
        release = await ctx.github.latest()
    except GitHubError as exc:
        return f"❌ GitHub tekshirilmadi: {exc}"
    if is_newer(release.version, current):
        return (
            f"🆕 Yangi versiya bor: v{release.version} (joriy v{current})\n{release.label}\n"
            "O'rnatish: /yangilash github"
        )
    if release.version == current:
        return f"✅ Siz eng so'nggi versiyadasiz: v{current}\n{release.label}"
    return f"ℹ️ GitHub'dagi versiya (v{release.version}) joriy v{current} dan eski.\n{release.label}"


async def stage_from_github(ctx: AppContext, uid: int, progress: Callable[[str], Awaitable[None]] | None = None) -> StagedUpdate:
    """GitHub'dan so'nggi arxivni yuklab, .zip kabi tekshiradi va tasdiq uchun saqlaydi (ctx.staged)."""
    if ctx.github is None or ctx.updater is None:
        raise GitHubError(GITHUB_OFF_TEXT)
    if uid in ctx.github_busy:
        raise GitHubError("GitHub'dan yuklash hali davom etmoqda — biroz kuting.")
    ctx.github_busy.add(uid)
    ctx.github_cancelled.discard(uid)
    try:
        release, data = await fetch_release(ctx.github)
        if uid in ctx.github_cancelled:
            raise UpdateCancelled
        if progress is not None:
            await progress(f"🔍 {release.label} tekshirilmoqda…")
        async with ctx.staging_lock:
            staged = await asyncio.to_thread(ctx.updater.stage, data)
        if uid in ctx.github_cancelled:
            ctx.updater.discard(staged)
            raise UpdateCancelled
    finally:
        ctx.github_busy.discard(uid)
        ctx.github_cancelled.discard(uid)
    describe_release(staged, release)
    old = ctx.staged.pop(uid, None)
    if old is not None and old.path != staged.path:
        ctx.updater.discard(old)
    ctx.staged[uid] = staged
    return staged


async def github_update(bot: Bot, chat_id: int, uid: int, ctx: AppContext) -> None:
    """/yangilash github: yuklash → tekshirish → xulosa va ✅ O'rnatish / ❌ Bekor tugmalari."""
    if ctx.github is None:
        await bot.send_message(chat_id, GITHUB_OFF_TEXT)
        return
    status = await bot.send_message(chat_id, f"⬇️ GitHub'dan yuklanmoqda ({ctx.github.repo})…")

    async def progress(text: str) -> None:
        with contextlib.suppress(TelegramBadRequest):
            await status.edit_text(text)

    try:
        staged = await stage_from_github(ctx, uid, progress)
    except UpdateCancelled:
        await progress("❌ Bekor qilindi. Joriy versiya o'zgarmadi.")
        return
    except (GitHubError, UpdateError) as exc:
        await progress(f"❌ GitHub'dan yangilab bo'lmadi: {exc}\n\nJoriy versiya o'zgarmadi.")
        return
    except Exception:
        log.exception("GitHub'dan yangilanishni tayyorlashda xato")
        await progress("❌ GitHub'dan yangilanishni tayyorlab bo'lmadi. Joriy versiya o'zgarmadi.")
        return
    summary, keyboard = update_summary(staged), _confirm_keyboard(staged)
    try:
        await status.edit_text(summary, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except TelegramBadRequest:
        # holat xabari o'chirilgan bo'lishi mumkin — tasdiq tugmalari baribir egasiga yetsin
        await bot.send_message(chat_id, summary, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cb_github(callback: CallbackQuery, bot: Bot, ctx: AppContext) -> None:
    """🐙 / 🔍 / ⬇️ tugmalari (faqat egasi)."""
    uid = callback.from_user.id
    if not ctx.is_owner(uid):
        await callback.answer("⛔ Ruxsat yo'q")
        return
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else uid
    if callback.data == CALLBACK_GITHUB_CHECK:
        await callback.answer("Tekshirilmoqda…")
        await bot.send_message(chat_id, await github_check_text(ctx))
        return
    await callback.answer()
    await github_update(bot, chat_id, uid, ctx)


async def cmd_cancel(message: Message, ctx: AppContext) -> None:
    uid = message.from_user.id
    ctx.awaiting_update.pop(uid, None)
    if uid in ctx.github_busy:
        ctx.github_cancelled.add(uid)  # yuklash tugagach natija tashlab yuboriladi
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
        async with ctx.staging_lock:
            staged = await asyncio.to_thread(ctx.updater.stage, data)
    except UpdateError as exc:
        await status.edit_text(f"❌ Yangilanish rad etildi: {exc}\n\nJoriy versiya o'zgarmadi.")
        return
    except AiogramError as exc:
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
        async with ctx.staging_lock:
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
        async with ctx.staging_lock:  # boshqa arxiv ochilayotganda staging papkasi o'chib ketmasin
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



# ------------------------------------------------------------------ rejim, tarix, shablon, yozuv, tugmalar


def mode_keyboard(current: str | None) -> InlineKeyboardMarkup:
    selected = current or DEFAULT_MODE
    rows = [
        [InlineKeyboardButton(text=("✅ " if m.key == selected else "") + f"{m.emoji} {m.label}", callback_data=f"mode:{m.key}")]
        for m in MODES.values()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_mode(message: Message, ctx: AppContext) -> None:
    s = await ctx.user_settings(message.from_user.id)
    await message.answer(MODE_TEXT, parse_mode=ParseMode.HTML, reply_markup=mode_keyboard(s.mode))


async def cb_mode(callback: CallbackQuery, ctx: AppContext) -> None:
    key = (callback.data or "").partition(":")[2]
    if key not in MODES:
        await callback.answer("Noma'lum rejim")
        return
    uid = callback.from_user.id
    await ctx.user_settings(uid)
    await ctx.db.update_settings(uid, mode=None if key == DEFAULT_MODE else key)
    await callback.answer(f"✅ Rejim: {mode_label(key)}")
    if isinstance(callback.message, Message):
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(
                f"{MODE_TEXT}\n\n✅ Tanlandi: <b>{mode_label(key)}</b>. Endi savolingizni yozing.",
                parse_mode=ParseMode.HTML,
                reply_markup=mode_keyboard(key),
            )


# Mini App bilan bir xil qisqartmalar (iyun/iyul farqlanadi)
MONTHS_SHORT = ("yan", "fev", "mar", "apr", "may", "iyn", "iyl", "avg", "sen", "okt", "noy", "dek")


def _when(stamp: str, tz: ZoneInfo) -> str:
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    except ValueError:
        return stamp
    return f"{moment.day}-{MONTHS_SHORT[moment.month - 1]} {moment:%H:%M}"


HISTORY_PAGE = 8


async def history_view(
    ctx: AppContext, uid: int, page: int = 0, query: str | None = None
) -> tuple[str, InlineKeyboardMarkup | None]:
    s = await ctx.user_settings(uid)
    tz = ZoneInfo(ctx.settings.timezone)
    if query:
        items: list[Conversation] = await ctx.db.search_conversations(uid, query)
        total, page = len(items), 0
        title = f"🔍 <b>«{html.escape(query)}» bo'yicha suhbatlar</b>"
    else:
        items, total = await ctx.db.conversations(uid, HISTORY_PAGE, page * HISTORY_PAGE)
        title = "🗂 <b>Suhbatlar tarixi</b>"
    if not items:
        hint = "Hech narsa topilmadi." if query else "Hali suhbatlar yo'q — savol yozing."
        return f"{title}\n\n{hint}", None
    lines = [title, ""]
    rows = []
    for c in items:
        current = c.conv_id == s.conv_id
        mark = "✅ " if current else ""
        lines.append(f"{mark}<b>{html.escape(c.title, quote=False)}</b> — {_when(c.updated_at, tz)}, {c.turns} ta javob")
        rows.append(
            [InlineKeyboardButton(text=f"{'✅' if current else '▶️'} {_short(c.title, 40)}", callback_data=f"h:o:{c.conv_id}")]
        )
    lines += ["", "Davom ettirish uchun suhbatni bosing. Qidiruv: <code>/tarix so'z</code>"]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Yangiroq", callback_data=f"h:p:{page - 1}"))
    if (page + 1) * HISTORY_PAGE < total:
        nav.append(InlineKeyboardButton(text="Eskiroq ➡️", callback_data=f"h:p:{page + 1}"))
    if nav:
        rows.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_history(message: Message, command: CommandObject, ctx: AppContext) -> None:
    text, keyboard = await history_view(ctx, message.from_user.id, query=(command.args or "").strip() or None)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cb_history(callback: CallbackQuery, ctx: AppContext) -> None:
    parts = (callback.data or "").split(":")
    uid = callback.from_user.id
    message = callback.message if isinstance(callback.message, Message) else None
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await callback.answer()
        return
    if parts[1] == "p":
        await callback.answer()
        text, keyboard = await history_view(ctx, uid, page=max(int(parts[2]), 0))
    else:
        await ctx.user_settings(uid)
        conversation = await ctx.db.switch_conversation(uid, int(parts[2]))
        if conversation is None:
            await callback.answer("Bu suhbat topilmadi", show_alert=True)
            return
        ctx.pending.pop(uid, None)
        await callback.answer("↩️ Suhbat davom etmoqda")
        text, keyboard = await history_view(ctx, uid)
        text += f"\n\n↩️ Davom etmoqda: <b>{html.escape(conversation.title, quote=False)}</b>. Savolingizni yozing."
    if message:
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


TEMPLATES_SHOWN = 25  # ro'yxat Telegram'ning 4096 belgilik chegarasiga sig'sin


async def templates_view(ctx: AppContext, uid: int) -> tuple[str, InlineKeyboardMarkup | None]:
    items = await ctx.db.templates(uid)
    if not items:
        return TEMPLATE_HELP, None
    lines = ["📌 <b>Shablonlar</b> — tugmani bossangiz, matn savol sifatida yuboriladi.", ""]
    rows = []
    for t in items[:TEMPLATES_SHOWN]:
        lines.append(f"<b>{html.escape(t.name, quote=False)}</b>: {html.escape(_short(t.text, 80), quote=False)}")
        rows.append(
            [
                InlineKeyboardButton(text=f"▶️ {_short(t.name, 28)}", callback_data=f"tpl:u:{t.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"tpl:d:{t.id}"),
            ]
        )
    if len(items) > TEMPLATES_SHOWN:
        more = f"… va yana {len(items) - TEMPLATES_SHOWN} ta"
        lines.append(more + (" — to'liq ro'yxat Mini App'da" if ctx.settings.webapp_url else ""))
    lines += ["", "Qo'shish: <code>/shablon qo'sh Nomi</code> + keyingi qatorda matn"]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_templates(message: Message, command: CommandObject, ctx: AppContext) -> None:
    uid = message.from_user.id
    args = (command.args or "").strip()
    first, _, rest = args.partition("\n")
    verb, _, name = first.strip().partition(" ")
    verb = _keyword(verb)
    if verb in ("qo'sh", "qosh", "add", "+"):
        name, body = name.strip(), rest.strip()
        if not name or not body:
            await message.answer(
                "Shablon nomi va matni kerak:\n<code>/shablon qo'sh Nomi\nmatn…</code>", parse_mode=ParseMode.HTML
            )
            return
        await ctx.db.add_template(uid, name[:40], body[:4000])
        await message.answer(f"✅ Shablon saqlandi: <b>{html.escape(name[:40])}</b>", parse_mode=ParseMode.HTML)
        return
    if verb in ("o'chir", "ochir", "del", "delete", "-"):
        target = _keyword(name)
        match = next((t for t in await ctx.db.templates(uid) if _keyword(t.name) == target), None)
        if match is None:
            await message.answer("Bunday shablon yo'q. Ro'yxat: /shablon")
            return
        await ctx.db.delete_template(uid, match.id)
        await message.answer(f"🗑 O'chirildi: <b>{html.escape(match.name)}</b>", parse_mode=ParseMode.HTML)
        return
    text, keyboard = await templates_view(ctx, uid)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def cb_template(callback: CallbackQuery, bot: Bot, ctx: AppContext) -> None:
    parts = (callback.data or "").split(":")
    uid = callback.from_user.id
    message = callback.message if isinstance(callback.message, Message) else None
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer()
        return
    template = await ctx.db.template(int(parts[2]))
    if template is None or template.user_id != uid:
        await callback.answer("Shablon topilmadi", show_alert=True)
        return
    if parts[1] == "d":
        await ctx.db.delete_template(uid, template.id)
        await callback.answer("🗑 O'chirildi")
        if message:
            text, keyboard = await templates_view(ctx, uid)
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    await callback.answer(f"▶️ {template.name}")
    if message:
        sent = await message.answer(f"📌 <b>{html.escape(template.name)}</b>\n{html.escape(template.text)}", parse_mode=ParseMode.HTML)
        await run_chat_for(
            uid, message.chat.id, sent.message_id, bot, ctx, [{"type": "text", "text": template.text}], with_pending=True
        )


def _source_text(message: Message, args: str | None) -> str | None:
    text = (args or "").strip()
    if text:
        return text
    reply = message.reply_to_message
    if reply is not None:
        return (reply.text or reply.caption or "").strip() or None
    return None


async def _send_long(message: Message, text: str) -> None:
    for part in split_plain(text):  # emoji 2 birlik: 4000 belgi 4096 dan oshib ketmasin
        try:
            await message.answer(part, parse_mode=None)
        except AiogramError as exc:
            log.warning("Uzun matn bo'lagi yuborilmadi: %s", exc)
            return


async def cmd_cyrillic(message: Message, command: CommandObject, ctx: AppContext) -> None:
    text = _source_text(message, command.args)
    if not text:
        await message.answer("Matnni buyruqdan keyin yozing yoki biror xabarga reply qiling: /kirill Salom dunyo")
        return
    await _send_long(message, translit.to_cyrillic(text))


async def cmd_latin(message: Message, command: CommandObject, ctx: AppContext) -> None:
    text = _source_text(message, command.args)
    if not text:
        await message.answer("Matnni buyruqdan keyin yozing yoki biror xabarga reply qiling: /lotin Салом дунё")
        return
    await _send_long(message, translit.to_latin(text))


def answer_keyboard(answer_id: int, regenerate: bool = True) -> InlineKeyboardMarkup:
    first = []
    if regenerate:
        first.append(InlineKeyboardButton(text="🔄 Qayta", callback_data=f"ans:re:{answer_id}"))
    first += [
        InlineKeyboardButton(text="✂️ Qisqa", callback_data=f"ans:short:{answer_id}"),
        InlineKeyboardButton(text="🌐 Tarjima", callback_data=f"ans:tr:{answer_id}"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            first,
            [
                InlineKeyboardButton(text="📄 Word", callback_data=f"ans:word:{answer_id}"),
                InlineKeyboardButton(text="🔡 Кирилл", callback_data=f"ans:cyr:{answer_id}"),
            ],
        ]
    )


def translate_keyboard(answer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data=f"ans:tr:{answer_id}:uz"),
                InlineKeyboardButton(text="🇷🇺 Rus", callback_data=f"ans:tr:{answer_id}:ru"),
                InlineKeyboardButton(text="🇬🇧 Ingliz", callback_data=f"ans:tr:{answer_id}:en"),
            ],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"ans:kb:{answer_id}")],
        ]
    )


async def quick_task(bot: Bot, ctx: AppContext, uid: int, chat_id: int, reply_to: int, instruction: str, source: str) -> None:
    """Arzon model bilan bir martalik ish (qisqartirish, tarjima) — suhbat tarixiga yozilmaydi."""
    model = ctx.settings.quick_model
    system = (
        "Sen matn ustida aniq topshiriqni bajaradigan yordamchisan. Faqat natijani ber: kirish so'zlari, "
        "izoh va savollarsiz. Matn formatini (ro'yxat, sarlavha, qalin) saqla."
    )
    messages = [{"role": "user", "content": [{"type": "text", "text": f"{instruction}\n\n<matn>\n{source}\n</matn>"}]}]
    async with ctx.lock_for(uid):
        if not await still_allowed(ctx, uid):
            return
        live = await LiveMessage.start(bot, chat_id, reply_to, label(model))
        notes: list[str] = []
        try:
            result = await _generate(
                ctx, uid, live, model=model, system=system, messages=messages, effort=None, web_search=False, notes=notes
            )
            if result.refused:
                await live.fail(refusal_message(result))
                return
            s = await ctx.user_settings(uid)
            answer_id = await ctx.db.save_quick_answer(uid, result.text)
            footer = await _footer(ctx, uid, s, model, None, result)
            await live.finish(format_answer(result.text, [], notes, footer), answer_keyboard(answer_id, regenerate=False))
        except Cancelled:
            await live.finish(format_answer(live.text.strip(), [], ["⏹ To'xtatildi."], None))
        except ClaudeError as exc:
            await live.fail(exc.user_message)
        except Exception:
            log.exception("Kutilmagan xato (tez ish)")
            await live.fail("⚠️ Kutilmagan xato yuz berdi. Qayta urinib ko'ring; takrorlansa, serverdagi loglarni tekshiring.")
        finally:
            await live.stop_ticker()


async def cb_answer(callback: CallbackQuery, bot: Bot, ctx: AppContext) -> None:
    parts = (callback.data or "").split(":")
    uid = callback.from_user.id
    message = callback.message if isinstance(callback.message, Message) else None
    if len(parts) < 3 or not parts[2].isdigit() or message is None:
        await callback.answer()
        return
    action, answer_id = parts[1], int(parts[2])
    stored = await ctx.db.answer(answer_id)
    if stored is None or stored[0] != uid:
        await callback.answer("Bu javob topilmadi (ehtimol eski)", show_alert=True)
        return
    _, conv_id, text = stored
    if action == "kb":
        await callback.answer()
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_reply_markup(reply_markup=answer_keyboard(answer_id, regenerate=conv_id != 0))
        return
    if action == "tr" and len(parts) == 3:
        await callback.answer()
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_reply_markup(reply_markup=translate_keyboard(answer_id))
        return
    if ctx.lock_for(uid).locked():
        await callback.answer("Oldingi xabar hali ishlanmoqda — tugashini kuting", show_alert=True)
        return
    if action == "re":
        s = await ctx.user_settings(uid)
        if s.conv_id != conv_id:
            await callback.answer("Bu javob boshqa suhbatdan — avval /tarix orqali o'sha suhbatga qayting", show_alert=True)
            return
        turn = await ctx.db.last_turn(uid, conv_id, answer_id)
        if turn is None:
            await callback.answer("Faqat suhbatning oxirgi javobini qayta olish mumkin", show_alert=True)
            return
        await callback.answer("🔄 Qayta yozilmoqda…")
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_reply_markup(reply_markup=None)
        # Eski savol-javob faqat yangi javob saqlanganda o'chadi
        new_id = await run_chat_for(uid, message.chat.id, message.message_id, bot, ctx, turn[1], replace=answer_id)
        if new_id is None:  # yangi javob chiqmadi — eski javob tarixda qoldi, tugmalari qaytadi
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_reply_markup(reply_markup=answer_keyboard(answer_id))
    elif action == "short":
        await callback.answer("✂️ Qisqartirilmoqda…")
        await quick_task(
            bot, ctx, uid, message.chat.id, message.message_id,
            "Quyidagi matnni mazmunini saqlab, eng ko'pi bilan 3–4 jumlaga yoki 3–5 bandli ro'yxatga qisqartir.", text,
        )
    elif action == "tr" and len(parts) == 4 and parts[3] in LANGS:
        await callback.answer("🌐 Tarjima qilinmoqda…")
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_reply_markup(reply_markup=answer_keyboard(answer_id, regenerate=conv_id != 0))
        await quick_task(
            bot, ctx, uid, message.chat.id, message.message_id,
            f"Quyidagi matnni {LANGS[parts[3]]} tarjima qil. Faqat tarjimani ber.", text,
        )
    elif action == "word":
        await callback.answer("📄 Word tayyorlanmoqda…")
        data = await asyncio.to_thread(export.answer_to_docx, text)
        name = f"javob-{answer_id}.docx"
        sent = await message.answer_document(
            BufferedInputFile(data, filename=name), caption="📄 Javob Word hujjat ko'rinishida"
        )
        if ctx.files is not None:
            document = getattr(sent, "document", None)
            await ctx.files.save(uid, conv_id, name, data, document.file_id if document is not None else None)
    elif action == "cyr":
        await callback.answer("🔡 Kirill yozuvi")
        await _send_long(message, translit.to_cyrillic(text))
    else:
        await callback.answer()


async def cmd_backup(message: Message, bot: Bot, ctx: AppContext) -> None:
    if not await _owner_only(message, ctx):
        return
    ok = await scheduler.send_backup(
        bot, ctx.db, ctx.settings.db_path, [message.chat.id], ctx.settings.timezone, reason="Zaxira nusxa (qo'lda)"
    )
    if not ok:
        await message.answer("❌ Zaxira nusxa olinmadi — serverdagi loglarni tekshiring.")


# ------------------------------------------------------------------ xabarlar


async def _download(bot: Bot, file_id: str) -> bytes:
    buffer = await bot.download(file_id, timeout=120)
    return buffer.getvalue()


async def read_attachment(bot: Bot, message: Message, max_edge: int) -> tuple[list[dict], list[RawFile]]:
    """Xabardagi rasm/faylni Claude bloklariga aylantiradi; asl baytlar vositalar (PDF, tarjima) uchun qaytadi."""
    if message.photo:
        data = await _download(bot, message.photo[-1].file_id)
        block = await asyncio.to_thread(media.image_block, data, max_edge)
        return [block], [RawFile(f"rasm-{message.message_id}.jpg", "image", data)]
    document = message.document
    if document is None:
        return [], []
    name = document.file_name or "fayl"
    if document.file_size and document.file_size > MAX_DOWNLOAD:
        raise media.MediaError(f"«{name}» juda katta — Telegram botlari faqat 20 MB gacha faylni yuklab oladi.")
    kind = media.classify(name, document.mime_type or "")
    if kind is None:
        raise media.MediaError(
            f"«{name}» turidagi faylni o'qiy olmayman. Rasm, PDF, Word (.docx), Excel (.xlsx) yoki matn fayl yuboring."
        )
    data = await _download(bot, document.file_id)
    return [await asyncio.to_thread(media.build_block, kind, data, name, max_edge)], [RawFile(name, kind, data)]


def _attachment_name(message: Message) -> str:
    if message.document is not None:
        return message.document.file_name or "fayl"
    return "rasm"


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
    caption = "\n".join(item.caption for item in group if item.caption).strip()
    # Qulf yuklashdan oldin olinadi: keyin kelgan xabar shu fayldan oldin javob olib ketmasin
    async with await _user_lock(ctx, bot, uid, message.chat.id, quiet_behind_file=not caption):
        blocks: list[dict] = []
        raws: list[RawFile] = []
        errors: list[str] = []
        ctx.busy[uid] = "file"
        try:
            for item in group:
                try:
                    item_blocks, item_raws = await read_attachment(bot, item, ctx.settings.image_max_edge)
                    blocks.extend(item_blocks)
                    raws.extend(item_raws)
                except media.MediaError as exc:
                    errors.append(f"⚠️ {exc}")
                except (AiogramError, aiohttp.ClientError, TimeoutError) as exc:
                    log.warning("Faylni yuklab bo'lmadi: %s", exc)
                    errors.append(f"⚠️ «{_attachment_name(item)}» ni Telegram'dan yuklab bo'lmadi — qayta yuboring.")
                except Exception:  # noqa: BLE001 — buzuq fayl (masalan, Excel ichidagi XML) javobsiz qolmasin
                    log.exception("Faylni o'qishda xato")
                    errors.append(f"⚠️ «{_attachment_name(item)}» ni o'qib bo'lmadi — fayl buzilgan bo'lishi mumkin.")
        finally:
            ctx.busy.pop(uid, None)
        if errors:
            await message.answer("\n".join(errors))
        if not blocks:
            return
        ctx.raw.add(uid, raws)
        if not caption:
            count = ctx.add_pending(uid, blocks)
            await message.answer(
                f"📎 Qabul qilindi ({count} ta fayl). Endi savolingizni yozing — masalan: «qisqacha mazmunini ayt»."
            )
            return
        pending = ctx.take_pending(uid)
        await _chat(
            uid, message.chat.id, group[-1].message_id, bot, ctx,
            pending + blocks + [{"type": "text", "text": caption}], restore=pending + blocks,
        )


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
    # Qulf havolalarni o'qishdan oldin olinadi: xabarlar kelgan tartibda javob oladi
    async with await _user_lock(ctx, bot, uid, message.chat.id):
        link_blocks: list[dict] = []
        if ctx.settings.link_reading and links.find_links(text):
            with contextlib.suppress(AiogramError):
                await bot.send_chat_action(message.chat.id, "typing")
            link_blocks, errors = await links.read_links(text, ctx.settings.link_max_bytes)
            if errors:
                with contextlib.suppress(AiogramError):
                    await message.answer("\n".join(errors) + "\n\nSavolga havolasiz javob beraman.")
        pending = ctx.take_pending(uid)
        await _chat(
            uid, message.chat.id, collected[-1].message_id, bot, ctx,
            pending + link_blocks + [{"type": "text", "text": text}], restore=pending,
        )


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
    elif result.stop_reason == "tool_use":
        notes.append("ℹ️ Bir javobda fayl amallari chegarasiga yetildi — davomini alohida so'rang.")
    if result.prompt_tokens >= long_context:
        notes.append(
            f"📚 Suhbat konteksti ≈{result.prompt_tokens // 1000}k token — har javob qimmatlashmoqda. "
            "Yangi mavzu uchun /new."
        )
    return notes


async def _footer(
    ctx: AppContext, uid: int, s: UserSettings, model: str, effort: str | None, result: TurnResult,
    shown_model: str | None = None, tool_ctx: ToolContext | None = None,
) -> str:
    parts = [shown_model or label(model)]
    if effort:
        parts.append(f"effort {effort}")
    if result.searches:
        parts.append(f"🔎 {result.searches}")
    if tool_ctx is not None and tool_ctx.sent:
        parts.append(f"📎 {len(tool_ctx.sent)}")
    if s.show_cost:
        now = ctx.now()
        today, _ = await ctx.db.spend(uid, now.replace(hour=0, minute=0, second=0, microsecond=0))
        parts.append(f"💰 ${result.cost + (tool_ctx.cost if tool_ctx is not None else 0.0):.3f}")
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
    tool_ctx: ToolContext | None = None,
) -> TurnResult:
    async def runner(name: str, args: dict) -> str:
        return await tools.run(tool_ctx, name, args)

    async def attempt(search: bool) -> TurnResult:
        live.completed_cost = 0.0
        live.start_usage = None
        task = asyncio.create_task(
            ctx.claude.run_turn(
                model=model, system=system, messages=messages, effort=effort, web_search=search, listener=live,
                tools=tools.specs(tool_ctx) if tool_ctx is not None else None,
                tool_runner=runner if tool_ctx is not None else None,
            )
        )
        ctx.active[uid] = task
        try:
            return await task
        except ClaudeError as exc:
            # Xatogacha tugagan qadamlar (vosita, pause_turn) ham pul — yozib qo'yamiz
            if exc.cost:
                await ctx.db.log_usage(uid, model, exc.numbers, exc.cost)
            raise
        except Exception:
            if live.completed_cost:
                await ctx.db.log_usage(uid, model, {}, live.completed_cost, estimated=True)
            raise
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


async def _user_lock(
    ctx: AppContext, bot: Bot, uid: int, chat_id: int, *, quiet_behind_file: bool = False
) -> asyncio.Lock:
    """Foydalanuvchi qulfi; band bo'lsa, xabar navbatga qo'yilgani aytiladi (qulf qayta kirmaydi).

    quiet_behind_file — izohsiz fayl boshqa fayl yuklanishini kutsa, xabar yuborilmaydi («📎 Qabul qilindi» keladi)."""
    lock = ctx.lock_for(uid)
    if lock.locked():
        if ctx.busy.get(uid) == "file":
            if quiet_behind_file:
                return lock
            text = "⏳ Oldingi fayl hali yuklanmoqda — bu xabar undan keyin ko'riladi."
        else:
            text = BUSY_QUEUED_TEXT
        with contextlib.suppress(AiogramError):
            await bot.send_message(chat_id=chat_id, text=text)
    return lock


async def still_allowed(ctx: AppContext, uid: int) -> bool:
    """Egasi yoki faol a'zo: navbatda turgan xabar uchun kirish qayta tekshiriladi (a'zo chiqarilgan bo'lishi mumkin)."""
    if ctx.is_owner(uid):
        return True
    member = await ctx.db.member(uid)
    if member is None or not member.active:
        log.info("Kirishi yopilgan foydalanuvchining navbatdagi xabari o'tkazib yuborildi (%s)", uid)
        return False
    return True


async def run_chat_for(
    uid: int, chat_id: int, reply_to: int, bot: Bot, ctx: AppContext, user_blocks: list[dict],
    *, with_pending: bool = False, replace: int | None = None,
) -> int | None:
    """Navbat bilan javob; with_pending — kutayotgan fayllar qulf ichida qo'shiladi. Yangi javob id'si yoki None."""
    async with await _user_lock(ctx, bot, uid, chat_id):
        pending = ctx.take_pending(uid) if with_pending else []
        return await _chat(uid, chat_id, reply_to, bot, ctx, pending + user_blocks, restore=pending, replace=replace)


async def pick_model(ctx: AppContext, uid: int, user_blocks: list[dict]) -> str:
    """✨ Avto: savol murakkabligiga qarab arzon / o'rta / kuchli model."""
    text = "\n".join(b.get("text", "") for b in user_blocks if b.get("type") == "text")
    has_files = any(b.get("type") in ("image", "document") for b in user_blocks)
    level, numbers, cost = await ctx.claude.classify(ctx.settings.quick_model, text, has_files)
    if cost:
        await ctx.db.log_usage(uid, ctx.settings.quick_model, numbers, cost)
    return ctx.settings.auto_models[LEVEL_INDEX[level]]


async def economy_mode(ctx: AppContext) -> float | None:
    """Kredit sarfi tejamkor rejim chegarasiga yetgan bo'lsa — joriy foiz, aks holda None."""
    budget, percent = ctx.settings.monthly_budget, ctx.settings.budget_economy_percent
    if budget <= 0 or percent <= 0:
        return None
    cycle, _ = await ctx.db.spend_all(billing_cycle_start(ctx.now(), ctx.settings.budget_reset_day))
    share = cycle / budget
    return share if share * 100 >= percent else None


async def check_budget(ctx: AppContext, bot: Bot) -> None:
    """Kredit 50 / 80 / 100% ga yetganda egasiga bir marta (har davrda) xabar beradi."""
    budget = ctx.settings.monthly_budget
    if budget <= 0:
        return
    now = ctx.now()
    start = billing_cycle_start(now, ctx.settings.budget_reset_day)
    cycle, _ = await ctx.db.spend_all(start)
    share = cycle / budget * 100
    key = start.strftime("%Y-%m-%d")
    for level in BUDGET_LEVELS:
        if share < level or await ctx.db.alert_sent(key, level):
            continue
        await ctx.db.mark_alert(key, level)
        icon = "🔴" if level >= 100 else "🟠" if level >= 80 else "🟡"
        text = (
            f"{icon} Kredit sarfi {level}% ga yetdi: <b>${cycle:.2f}</b> / ${budget:g} "
            f"(davr {start.day}-{MONTHS[start.month - 1]}dan).\n"
        )
        if level >= 100:
            text += "Oylik kredit tugagan bo'lishi mumkin — Console'da balansni tekshiring."
        elif ctx.settings.budget_economy_percent:
            text += f"{ctx.settings.budget_economy_percent}% dan keyin hamma tejamkor modelga o'tadi ({label(ctx.settings.economy_model)})."
        for owner_id in ctx.settings.owner_ids:
            with contextlib.suppress(AiogramError):
                await bot.send_message(owner_id, text, parse_mode=ParseMode.HTML)


async def _report_tools(ctx: AppContext, uid: int):
    """Hisobot vositalari: egasiga to'liq, rahbarga (parol bilan kirgan bo'lsa ham) telefonlarsiz; boshqalarga — yo'q.
    Hisobot hali ishlatilmayotgan bo'lsa (lidlar guruhi ham, vazifa ham yo'q) — vositalar qo'shilmaydi."""
    reports = ctx.reports
    if reports is None:
        return None
    try:
        owner = ctx.is_owner(uid)
        if not owner and "leader" not in await reports.store.roles_of(uid, None):
            return None
        if not await reports.store.groups_with("leads") and not await reports.tasks.store.has_any():
            return None
    except Exception:  # noqa: BLE001 — hisobot xatosi oddiy suhbatni to'xtatmasin
        log.exception("Hisobot vositalarini tayyorlashda xato")
        return None
    return reports.ai_tools(masked=not owner)


class _Silent:
    """Oqimsiz javob (rahbar savoli): oraliq matn ko'rsatilmaydi — faqat «yozmoqda…» va yakuniy javob."""

    async def on_message_start(self, model: str, usage: Any) -> None: ...
    async def on_thinking(self) -> None: ...
    async def on_search(self, query: str | None) -> None: ...
    async def on_text(self, text: str) -> None: ...
    async def on_tool(self, status: str) -> None: ...
    async def on_iteration_done(self, cost: float) -> None: ...


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    while True:
        with contextlib.suppress(AiogramError):
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        await asyncio.sleep(4.5)


async def _send_chunks(bot: Bot, chat_id: int, chunks: list[tuple[str | None, str]]) -> None:
    for html_text, plain in chunks:
        try:
            try:
                await bot.send_message(chat_id, html_text or plain, parse_mode=ParseMode.HTML if html_text else None)
            except TelegramBadRequest:
                if html_text is None:
                    raise
                await bot.send_message(chat_id, plain, parse_mode=None)  # HTML rad etilsa — oddiy matn
        except AiogramError as exc:
            log.warning("Javob yuborilmadi (%s): %s", chat_id, exc)
            return


async def leader_answer(message: Message, bot: Bot, ctx: AppContext) -> None:
    """Rahbar botga savol yozdi: AI faqat hisobot va vazifalar bo'yicha javob beradi (telefonlar yashirilgan).

    Model — bot standarti (DEFAULT_MODEL; ✨ Avto bo'lsa savolga qarab, tejamkor rejimda — tejamkor model);
    xarajat rahbarning ID'si bilan yoziladi; kuniga LEADER_DAILY_LIMIT ta savol."""
    reports = ctx.reports
    uid, chat_id = message.from_user.id, message.chat.id
    question = (message.text or "").strip()[:4000]
    limit = ctx.settings.leader_daily_limit
    memory = reports.leader_memory
    if not memory.allowed(uid, ctx.now().date(), limit):
        with contextlib.suppress(AiogramError):
            await message.answer(f"⏳ Bugungi savollar chegarasi ({limit} ta) tugadi — ertaga yana so'rang.")
        return
    async with await _user_lock(ctx, bot, uid, chat_id):
        model = ctx.settings.default_model
        if await economy_mode(ctx) is not None:
            model = ctx.settings.economy_model
        elif model == AUTO_MODEL:
            model = await pick_model(ctx, uid, [{"type": "text", "text": question}])
        if model == AUTO_MODEL:
            model = ctx.settings.economy_model
        report_tools = reports.ai_tools(masked=True)
        messages = memory.history(uid) + [{"role": "user", "content": question}]
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        try:
            result = await ctx.claude.run_turn(
                model=model, system=leader_system(ctx.now()), messages=messages, effort=None, web_search=False,
                listener=_Silent(), tools=report_tools.specs(), tool_runner=report_tools.run,
            )
        except ClaudeError as exc:
            if exc.cost:
                await ctx.db.log_usage(uid, model, exc.numbers, exc.cost)
            with contextlib.suppress(AiogramError):
                await message.answer(exc.user_message)
            return
        except Exception:
            log.exception("Rahbar savoliga javobda xato")
            with contextlib.suppress(AiogramError):
                await message.answer("⚠️ Javob tayyorlab bo'lmadi. Birozdan keyin qayta urinib ko'ring.")
            return
        finally:
            typing.cancel()
        await ctx.db.log_usage(uid, model, result.numbers, result.cost)
        memory.count(uid, ctx.now().date())
        if result.refused:
            with contextlib.suppress(AiogramError):
                await message.answer("🚫 Bu savolga javob berilmadi. Hisobot yoki vazifalar haqida boshqacha so'rab ko'ring.")
            return
        answer = result.text.strip() or "Javob bo'sh qaytdi — savolni boshqacha yozib ko'ring."
        memory.add(uid, question, answer)
        await _send_chunks(bot, chat_id, format_answer(answer, [], [], None))
    await check_budget(ctx, bot)


async def _chat(
    uid: int, chat_id: int, reply_to: int, bot: Bot, ctx: AppContext, user_blocks: list[dict],
    *, restore: list[dict] | None = None, replace: int | None = None,
) -> int | None:
    """Javob yozadi va saqlaydi; yangi javob id'si yoki None (xato, rad, bo'sh ⏹).
    restore — vaqtinchalik xatoda kutish navbatiga qaytadigan fayllar; replace — 🔄 almashtiriladigan javob."""
    if not await still_allowed(ctx, uid):  # navbatda turganida kirishi yopilgan bo'lsa — javob yozilmaydi
        return None
    s = await ctx.user_settings(uid)
    replaced = await ctx.db.last_turn(uid, s.conv_id, replace) if replace is not None else None
    if replace is not None and replaced is None:
        with contextlib.suppress(AiogramError):
            await bot.send_message(chat_id=chat_id, text="🔄 Bu javob endi suhbatning oxirgisi emas — qayta yozilmadi.")
        return None
    model = s.active_model
    # Zaxira model ishlayotganda effort yuborilmaydi (u modelning standarti bilan ishlaydi)
    effort = None if s.model_override else s.effort
    notes: list[str] = []
    shown_model: str | None = None
    share = await economy_mode(ctx)
    if share is not None:  # zaxira model (rad etilgandan keyin) ham — tejamkor rejim hammaga
        if model != ctx.settings.economy_model:
            model = ctx.settings.economy_model
            shown_model = f"💡 Tejamkor → {label(model)}"
            notes.append(f"💡 Kredit {share:.0%} sarflangani uchun tejamkor model ishlatildi: {label(model)}.")
    elif model == AUTO_MODEL:
        model = await pick_model(ctx, uid, user_blocks)
        shown_model = f"✨ Avto → {label(model)}"
    if model == AUTO_MODEL:  # tejamkor model avto bilan bir xil bo'lsa ham API'ga «auto» ketmasin
        model = ctx.settings.economy_model

    history = await ctx.db.history(uid, s.conv_id, s.history_start_id)
    if replaced is not None:  # 🔄: eski savol-javob so'rovga kirmaydi, o'chirish — yangi javob saqlanganda
        history = [m for m in history if m.id < replaced[0]]
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
    report_tools = await _report_tools(ctx, uid)
    extra = load_mode_prompt(ctx.settings.modes_dir, s.mode) + "\n\n" + tools.TOOLS_GUIDE
    if report_tools is not None:
        extra += "\n\n" + REPORT_GUIDE
    system = ctx.prompt.build(extra=extra)

    live = await LiveMessage.start(bot, chat_id, reply_to, label(model))
    delivered = False
    answer_id: int | None = None

    async def log_tool_usage(tool_model: str, numbers: dict, cost: float) -> None:
        await ctx.db.log_usage(uid, tool_model, numbers, cost)

    async def remember_file(name: str, data: bytes, file_id: str | None) -> None:
        if ctx.files is not None:
            await ctx.files.save(uid, s.conv_id, name, data, file_id)

    tool_ctx = ToolContext(
        bot=bot, chat_id=chat_id, reply_to=reply_to, uid=uid, files=ctx.raw.recent(uid), model=model,
        claude=ctx.claude, live=live, log_usage=log_tool_usage, on_file=remember_file, reports=report_tools,
    )

    async def save_turn(answer: str) -> int:
        if replace is not None:
            await ctx.db.pop_last_turn(uid, s.conv_id, replace)
        answer_id = await ctx.db.add_turn(uid, s.conv_id, user_blocks, answer)
        if restore:  # shu navbat yangi fayllarni ishlatdi — keyingilari yangi to'plam
            ctx.raw.close_batch(uid)
        return answer_id

    try:
        result = await _generate(
            ctx, uid, live, model=model, system=system, messages=messages,
            effort=effort, web_search=s.web_search, notes=notes, tool_ctx=tool_ctx,
        )
        fallback = ctx.settings.fallback_model
        if result.refused and fallback and fallback != model:
            refused_model, model, effort = model, fallback, None
            category = REFUSAL_CATEGORIES.get(result.refusal_category or "", "")
            live.reset(
                f"⚠️ {label(refused_model)} bu so'rovni rad etdi{category}. {label(model)} bilan qayta urinilmoqda…",
                label(model),
            )
            tool_ctx.model = model
            shown_model = None  # javob ostida haqiqatda javob bergan model ko'rinsin
            result = await _generate(
                ctx, uid, live, model=model, system=system, messages=messages,
                effort=effort, web_search=s.web_search, notes=notes, tool_ctx=tool_ctx,
            )
            if not result.refused:
                await ctx.db.update_settings(uid, model_override=model)
                notes.append(
                    f"↪️ {label(refused_model)} rad etgani uchun javobni {label(model)} berdi. "
                    f"Bu suhbat oxirigacha {label(model)} ishlaydi — /new yoki /model bilan qaytasiz."
                )
        if result.refused:
            await live.fail(refusal_message(result))
            return None

        text = result.text
        if not text.strip() and tool_ctx.sent:
            text = "📎 Fayl yuborildi: " + ", ".join(tool_ctx.sent)
        # Javob tayyor va to'langan: saqlash yoki pastki qator xato bersa ham foydalanuvchi uni ko'radi
        answer_id: int | None = None
        try:
            answer_id = await save_turn(text or "(bo'sh javob)")
        except Exception:  # noqa: BLE001
            log.exception("Javob tarixga saqlanmadi")
            notes.append("⚠️ Javob suhbat tarixiga saqlanmadi — keyingi savolda bu javob hisobga olinmaydi.")
        notes.extend(_stop_notes(result, ctx.settings.long_context_tokens))
        try:
            footer = await _footer(ctx, uid, s, model, effort, result, shown_model, tool_ctx)
        except Exception:  # noqa: BLE001
            log.exception("Javob ostidagi qator tayyorlanmadi")
            footer = label(model)
        markup = answer_keyboard(answer_id) if answer_id is not None else None
        await live.finish(format_answer(text, result.sources, notes, footer), markup)
        delivered = True
    except Cancelled:
        partial = live.text.strip()
        if partial:
            answer_id = await save_turn(partial + "\n\n[javob foydalanuvchi tomonidan to'xtatildi]")
            notes.append("⏹ Javob to'xtatildi.")
            await live.finish(format_answer(partial, [], notes, None), answer_keyboard(answer_id))
            return answer_id
        notes.append("⏹ Javob to'xtatildi.")
        await live.finish(format_answer(partial, [], notes, None))
    except ClaudeError as exc:
        message = exc.user_message
        if restore and exc.retryable:
            # Vaqtinchalik xato: fayllar yo'qolmasin — keyingi savolga qo'shiladi
            ctx.add_pending(uid, restore)
            message += "\n\n📎 Fayl(lar) saqlab qolindi — savolni qayta yozing."
        await live.fail(message)
    except Exception:
        log.exception("Kutilmagan xato")
        await live.fail(
            "⚠️ Kutilmagan xato yuz berdi. Qayta urinib ko'ring; takrorlansa, serverdagi loglarni tekshiring "
            "(docker compose logs)."
        )
    finally:
        await live.stop_ticker()
    if delivered:
        try:  # kredit ogohlantirishi — javob ko'rsatilgandan keyin, xatosi javobni buzmaydi
            await check_budget(ctx, bot)
        except Exception:  # noqa: BLE001
            log.exception("Kredit tekshiruvida xato")
        return answer_id
    return None


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
    router.message.register(cmd_mode, Command("rejim", "mode"))
    router.message.register(cmd_history, Command("tarix", "history"))
    router.message.register(cmd_templates, Command("shablon", "shablonlar", "template"))
    router.message.register(cmd_cyrillic, Command("kirill", "cyr"))
    router.message.register(cmd_latin, Command("lotin", "lat"))
    router.message.register(cmd_backup, Command("zaxira", "backup"))
    router.message.register(cmd_app, Command("ilova", "app"))
    # admin (faqat egasi; tekshiruv har bir handler ichida)
    router.message.register(cmd_update, Command("yangilash", "update"))
    router.message.register(cmd_version, Command("versiya", "version"))
    router.message.register(cmd_restore, Command("qaytarish", "rollback"))
    router.message.register(cmd_cancel, Command("bekor", "cancel"))
    router.message.register(cmd_password, Command("parol", "password"))
    router.message.register(cmd_members, Command("foydalanuvchilar", "users"))
    router.message.register(cmd_source, Command("kod", "source"))
    router.message.register(on_unknown_command, F.text.regexp(COMMAND_LIKE))
    router.message.register(on_attachment, F.photo | F.document)
    router.message.register(on_unsupported, F.voice | F.audio | F.video | F.video_note | F.sticker)
    router.message.register(on_text, F.text)

    router.callback_query.register(cb_model, F.data.startswith("m:"))
    router.callback_query.register(cb_effort, F.data.startswith("e:"))
    router.callback_query.register(cb_cost_toggle, F.data == "c:toggle")
    router.callback_query.register(cb_stop, F.data == "stop")
    router.callback_query.register(cb_menu, F.data.startswith("menu:"))
    router.callback_query.register(cb_mode, F.data.startswith("mode:"))
    router.callback_query.register(cb_history, F.data.startswith("h:"))
    router.callback_query.register(cb_template, F.data.startswith("tpl:"))
    router.callback_query.register(cb_answer, F.data.startswith("ans:"))
    router.callback_query.register(cb_member, F.data.startswith("rm:") | F.data.startswith("rs:"))
    router.callback_query.register(cb_update, F.data.startswith("up:"))
    router.callback_query.register(cb_github, F.data.in_({CALLBACK_GITHUB_INSTALL, CALLBACK_GITHUB_CHECK}))
    return router

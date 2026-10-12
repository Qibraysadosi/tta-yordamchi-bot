"""Botni yig'ish va ishga tushirish."""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommandScopeChat, MenuButtonDefault, MenuButtonWebApp, User, WebAppInfo

from . import branding, scheduler, webapp
from .claude import ClaudeService
from .config import ConfigError, Settings, load_settings
from .db import Database
from .filestore import FileStore
from .github import GitHubClient
from .handlers import COMMANDS, OWNER_COMMANDS, AccessMiddleware, AppContext, build_router
from .models import label
from .prompt import PromptLoader
from .updater import UpdateManager

UPDATE_CHECK_DEFAULT_TIME = "03:30"  # BACKUP_TIME o'chiq bo'lsa, GitHub tekshiruvi shu vaqtda

log = logging.getLogger("bot")

APP_DIR = Path(__file__).resolve().parent.parent  # /app: bot/, prompts/, VERSION, launcher.py

# Fayl yaratish vositalari uchun kutubxonalar (modul nomi → pip paketi); yo'qligi botni to'xtatmaydi
OPTIONAL_LIBRARIES = {"fpdf": "fpdf2", "pptx": "python-pptx", "pypdf": "pypdf", "matplotlib": "matplotlib"}


def missing_libraries() -> list[str]:
    return [package for module, package in OPTIONAL_LIBRARIES.items() if importlib.util.find_spec(module) is None]


def build_dispatcher(ctx: AppContext) -> Dispatcher:
    dp = Dispatcher(ctx=ctx)
    access = AccessMiddleware()
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_router(build_router())
    ctx.dispatcher = dp
    return dp


def build_claude(settings: Settings) -> ClaudeService:
    return ClaudeService(
        api_key=settings.anthropic_api_key,
        max_tokens=settings.max_tokens,
        web_search_tool=settings.web_search_tool,
        web_search_max_uses=settings.web_search_max_uses,
        cache_ttl=settings.cache_ttl,
        search_city=settings.search_city,
        search_country=settings.search_country,
        timezone=settings.timezone,
    )


async def connect(bot: Bot, owner_ids: frozenset[int] = frozenset(), attempts: int = 6) -> User:
    """Telegram'ga ulanish: tarmoq vaqtincha uzilgan bo'lsa, bir necha marta qayta urinadi."""
    me: User | None = None
    for attempt in range(1, attempts + 1):
        try:
            await bot.set_my_commands(COMMANDS)
            me = await bot.get_me()
            break
        except TelegramUnauthorizedError as exc:
            raise ConfigError("TELEGRAM_BOT_TOKEN noto'g'ri — @BotFather'dan olingan tokenni tekshiring") from exc
        except TelegramNetworkError as exc:
            if attempt == attempts:
                raise
            log.warning("Telegram'ga ulanib bo'lmadi (%s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(5 * attempt)
    if me is None:
        raise RuntimeError("unreachable")
    for owner_id in sorted(owner_ids):
        # Admin buyruqlari faqat egasining menyusida ko'rinadi
        with contextlib.suppress(TelegramAPIError):
            await bot.set_my_commands(OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=owner_id))
    return me


async def apply_menu_button(bot: Bot, webapp_url: str | None) -> None:
    """Chatning pastki chap burchagidagi menyu tugmasi: Mini App ulangan bo'lsa — «Ilova», aks holda buyruqlar."""
    with contextlib.suppress(TelegramAPIError):
        if webapp_url:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Ilova", web_app=WebAppInfo(url=webapp_url)))
        else:
            await bot.set_chat_menu_button(menu_button=MenuButtonDefault())


async def announce_update(bot: Bot, ctx: AppContext) -> None:
    """Yangilanish yoki avtomatik tiklash natijasini egasiga yozadi."""
    if ctx.updater is None:
        return
    notice = ctx.updater.finalize()
    if notice is None:
        return
    log.info("Yangilanish natijasi: %s", notice.text.splitlines()[0])
    text = notice.text
    missing = missing_libraries()
    if missing:
        text += (
            f"\n\n⚠️ Kutubxonalar o'rnatilmadi: {', '.join(missing)} — fayl yaratish (PDF, taqdimot, grafik) "
            "ishlamaydi. Serverda: docker compose up -d --build"
        )
    targets = [notice.chat_id] if notice.chat_id else sorted(ctx.settings.owner_ids)
    for chat_id in targets:
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(chat_id=chat_id, text=text)


def start_backup_job(bot: Bot, ctx: AppContext) -> scheduler.DailyJob | None:
    """Kunlik zaxira: belgilangan vaqtda egasiga bot.db nusxasi; o'tkazib yuborilgan bo'lsa — hozir."""
    at = ctx.settings.backup_time
    if not at:
        return None
    owners = sorted(ctx.settings.owner_ids)

    async def run_backup(reason: str = "Kunlik zaxira") -> None:
        await scheduler.send_backup(bot, ctx.db, ctx.settings.db_path, owners, ctx.settings.timezone, reason=reason)

    async def catch_up() -> None:
        if await scheduler.missed_backup(ctx.db, ctx.settings.timezone, at):
            await run_backup("Kunlik zaxira (kechikkan)")

    asyncio.create_task(catch_up(), name="backup:catch-up")
    job = scheduler.DailyJob("backup", at, ctx.settings.timezone, run_backup)
    job.start()
    return job


def start_update_check_job(bot: Bot, ctx: AppContext) -> scheduler.DailyJob | None:
    """Har kuni (zaxira vaqtida) GitHub'da yangi versiya bormi — bo'lsa egasiga bir marta xabar."""
    if ctx.github is None or not ctx.settings.update_check or ctx.updater is None:
        return None
    at = ctx.settings.backup_time or UPDATE_CHECK_DEFAULT_TIME
    owners = sorted(ctx.settings.owner_ids)

    async def run_check() -> None:
        await scheduler.check_update(bot, ctx.db, ctx.github, ctx.updater.version, owners, ctx.settings.timezone)

    async def catch_up() -> None:
        if await scheduler.missed_update_check(ctx.db, ctx.settings.timezone, at):
            await run_check()

    asyncio.create_task(catch_up(), name="update-check:catch-up")
    job = scheduler.DailyJob("update-check", at, ctx.settings.timezone, run_check)
    job.start()
    return job


async def run() -> bool:
    """Botni ishlatadi. True — qayta ishga tushirish so'ralgan (yangilanish o'rnatildi)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = load_settings()
    db = await Database.open(settings.db_path)
    claude = build_claude(settings)
    data_dir = settings.db_path.resolve().parent
    ctx = AppContext(
        settings=settings,
        db=db,
        claude=claude,
        prompt=PromptLoader(settings.system_prompt_path, settings.timezone),
        updater=UpdateManager(app_dir=APP_DIR, data_dir=data_dir),
        files=FileStore(db, data_dir / "files", settings.files_max_bytes),
        github=GitHubClient(settings.github_repo, settings.github_token, settings.github_branch) if settings.github_repo else None,
    )
    bot = Bot(settings.telegram_token, default=DefaultBotProperties(link_preview_is_disabled=True))
    dp = build_dispatcher(ctx)
    try:
        me = await connect(bot, settings.owner_ids)
        log.info(
            "Bot ishga tushdi: @%s · v%s · standart model: %s · egalari: %s",
            me.username,
            ctx.updater.version,
            label(settings.default_model),
            ", ".join(str(i) for i in sorted(settings.owner_ids)),
        )
        if ctx.github is not None:
            log.info(
                "GitHub'dan yangilash: %s (token: %s, kunlik tekshiruv: %s)",
                ctx.github.repo, "bor" if ctx.github.has_token else "yo'q", "on" if settings.update_check else "off",
            )
        await announce_update(bot, ctx)
        if missing_libraries():
            log.warning("Kutubxonalar yo'q: %s (pip install -r requirements.txt)", ", ".join(missing_libraries()))
        try:
            await branding.apply_profile(bot, db)  # tavsif va profil rasmi (faqat o'zgarganda)
        except Exception:  # noqa: BLE001 — ko'rinish botning ishlashiga xalaqit bermasin
            log.exception("Bot profilini o'rnatishda xato")
        await apply_menu_button(bot, settings.webapp_url)
        server = None
        if settings.webapp_url:
            try:
                server = await webapp.start_webapp(ctx, bot)
            except OSError:
                log.exception("Mini App serveri ishga tushmadi (port %s band?)", settings.webapp_port)
        jobs = [job for job in (start_backup_job(bot, ctx), start_update_check_job(bot, ctx)) if job is not None]
        try:
            await dp.start_polling(bot)
        finally:
            for job in jobs:
                await job.stop()
            await webapp.stop_webapp(server)
    finally:
        await db.close()
        await bot.session.close()
        with contextlib.suppress(Exception):
            await claude.client.close()
    return ctx.restart_requested

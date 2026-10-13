"""Belgilangan vaqtda ishlaydigan vazifalar: kunlik zaxira va GitHub'da yangi versiya tekshiruvi
(keyingi bosqichlarda eslatma, dayjest)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .db import Database
from .github import GitHubClient, GitHubError, Release, is_newer
from .updater import CALLBACK_GITHUB_INSTALL

log = logging.getLogger(__name__)
BACKUP_STAMP_KEY = "backup:last"
UPDATE_CHECK_KEY = "update:last_check"  # GitHub oxirgi marta tekshirilgan sana
UPDATE_NOTIFIED_KEY = "update:notified"  # egasiga aytilgan versiya (har versiya uchun bir marta)
NOTES_PREVIEW = 600


class DailyJob:
    """Har kuni HH:MM da (mahalliy vaqt) bitta vazifani bajaradi; o'tkazib yuborilgan kun — ishga tushganda."""

    def __init__(self, name: str, at: str, timezone: str, run: Callable[[], Awaitable[None]], catch_up: bool = True):
        self.name = name
        self.hour, self.minute = (int(x) for x in at.split(":"))
        self.tz = ZoneInfo(timezone)
        self.run = run
        self.catch_up = catch_up
        self._task: asyncio.Task | None = None
        self.catch_up_task: asyncio.Task | None = None  # ishga tushganda o'tkazib yuborilgan ishni bajaradi

    def next_run(self, now: datetime) -> datetime:
        planned = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        return planned if planned > now else planned + timedelta(days=1)

    def start(self, catch_up: Callable[[], Awaitable[None]] | None = None) -> None:
        self._task = asyncio.create_task(self._loop(), name=f"job:{self.name}")
        if catch_up is not None and self.catch_up:
            self.catch_up_task = asyncio.create_task(self._safe(catch_up), name=f"job:{self.name}:catch-up")

    async def stop(self) -> None:
        for task in (self.catch_up_task, self._task):
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _safe(self, run: Callable[[], Awaitable[None]]) -> None:
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — bitta xato jadvalni to'xtatmasin
            log.exception("Kechikkan vazifa xato bilan tugadi: %s", self.name)

    async def _loop(self) -> None:
        while True:
            now = datetime.now(self.tz)
            wait = (self.next_run(now) - now).total_seconds()
            await asyncio.sleep(wait)
            await self._safe(self.run)


async def make_backup(db: Database, db_path: Path, label: str) -> tuple[bytes, str]:
    """Bazaning izchil nusxasi (VACUUM INTO) — baytlar va fayl nomi."""
    target = db_path.with_name(f"backup-{label}.db")
    target.unlink(missing_ok=True)
    try:
        await db.vacuum_into(target)
        return target.read_bytes(), target.name
    finally:
        target.unlink(missing_ok=True)


async def send_backup(bot: Bot, db: Database, db_path: Path, chat_ids: list[int], timezone: str, *, reason: str) -> bool:
    now = datetime.now(ZoneInfo(timezone))
    try:
        data, name = await make_backup(db, db_path, now.strftime("%Y%m%d-%H%M"))
    except Exception:  # noqa: BLE001
        log.exception("Zaxira nusxa olishda xato")
        return False
    caption = (
        f"💾 {reason}: {now:%d.%m.%Y %H:%M}\n"
        f"Hajmi: {len(data) / 1024:.0f} KB. Tiklash: faylni serverdagi data/bot.db o'rniga qo'ying."
    )
    sent = False
    for chat_id in chat_ids:
        try:
            await bot.send_document(chat_id, BufferedInputFile(data, filename=name), caption=caption)
            sent = True
        except TelegramAPIError as exc:
            log.warning("Zaxira yuborilmadi (%s): %s", chat_id, exc)
    if sent:
        await db.set_config(BACKUP_STAMP_KEY, now.strftime("%Y-%m-%d"))
    return sent


async def _missed_today(db: Database, timezone: str, at: str, key: str) -> bool:
    """Bugungi vaqt (HH:MM) o'tgan, lekin kunlik ish bajarilmagan bo'lsa — True (bot o'chiq turgan bo'lishi mumkin)."""
    now = datetime.now(ZoneInfo(timezone))
    hour, minute = (int(x) for x in at.split(":"))
    if (now.hour, now.minute) < (hour, minute):
        return False
    last = await db.get_config(key)
    return last != now.strftime("%Y-%m-%d")


async def missed_backup(db: Database, timezone: str, at: str) -> bool:
    """Bugungi zaxira vaqti o'tgan, lekin zaxira yuborilmagan bo'lsa — True."""
    return await _missed_today(db, timezone, at, BACKUP_STAMP_KEY)


async def missed_update_check(db: Database, timezone: str, at: str) -> bool:
    """Bugungi GitHub tekshiruvi vaqti o'tgan, lekin tekshirilmagan bo'lsa — True."""
    return await _missed_today(db, timezone, at, UPDATE_CHECK_KEY)


def update_notice(release: Release, current_version: str) -> tuple[str, InlineKeyboardMarkup]:
    """Yangi versiya haqida xabar matni va tugmalar (oddiy matn — release izohi HTML'ni buzmasin)."""
    text = f"🆕 GitHub'da yangi versiya: v{release.version} (joriy v{current_version})\n{release.label}"
    if release.notes:
        notes = release.notes[:NOTES_PREVIEW] + ("…" if len(release.notes) > NOTES_PREVIEW else "")
        text += f"\n\n{notes}"
    text += "\n\nO'rnatish: tugmani bosing yoki /yangilash github — tasdiqdan keyin bot 10–30 soniyaga qayta ishga tushadi."
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬇️ Yuklab o'rnatish", callback_data=CALLBACK_GITHUB_INSTALL),
                InlineKeyboardButton(text="🐙 GitHub'da ko'rish", url=release.html_url),
            ]
        ]
    )
    return text, keyboard


async def check_update(
    bot: Bot, db: Database, github: GitHubClient, current_version: str, chat_ids: list[int], timezone: str
) -> Release | None:
    """GitHub'da joriy versiyadan yangisi bormi — bo'lsa egasiga bir marta (har versiya uchun) xabar beradi.

    Qaytaradi: topilgan yangi versiya yoki None (yangisi yo'q / GitHub'ga ulanib bo'lmadi).
    """
    try:
        release = await github.latest()
    except GitHubError as exc:
        log.warning("GitHub tekshiruvi o'tmadi: %s", exc)
        return None
    await db.set_config(UPDATE_CHECK_KEY, datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d"))
    if not is_newer(release.version, current_version):
        return None
    if await db.get_config(UPDATE_NOTIFIED_KEY) == release.version:
        return release  # bu versiya haqida allaqachon aytilgan
    text, keyboard = update_notice(release, current_version)
    sent = False
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
            sent = True
        except TelegramAPIError as exc:
            log.warning("Yangi versiya haqida xabar yuborilmadi (%s): %s", chat_id, exc)
    if sent:
        await db.set_config(UPDATE_NOTIFIED_KEY, release.version)
    return release

"""Belgilangan vaqtda ishlaydigan vazifalar: hozircha kunlik zaxira (keyingi bosqichlarda eslatma, dayjest)."""
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
from aiogram.types import BufferedInputFile

from .db import Database

log = logging.getLogger(__name__)
BACKUP_STAMP_KEY = "backup:last"


class DailyJob:
    """Har kuni HH:MM da (mahalliy vaqt) bitta vazifani bajaradi; o'tkazib yuborilgan kun — ishga tushganda."""

    def __init__(self, name: str, at: str, timezone: str, run: Callable[[], Awaitable[None]], catch_up: bool = True):
        self.name = name
        self.hour, self.minute = (int(x) for x in at.split(":"))
        self.tz = ZoneInfo(timezone)
        self.run = run
        self.catch_up = catch_up
        self._task: asyncio.Task | None = None

    def next_run(self, now: datetime) -> datetime:
        planned = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        return planned if planned > now else planned + timedelta(days=1)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name=f"job:{self.name}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            now = datetime.now(self.tz)
            wait = (self.next_run(now) - now).total_seconds()
            await asyncio.sleep(wait)
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — bitta xato jadvalni to'xtatmasin
                log.exception("Rejalashtirilgan vazifa xato bilan tugadi: %s", self.name)


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


async def missed_backup(db: Database, timezone: str, at: str) -> bool:
    """Bugungi zaxira vaqti o'tgan, lekin zaxira yuborilmagan bo'lsa — True (bot o'chiq turgan bo'lishi mumkin)."""
    now = datetime.now(ZoneInfo(timezone))
    hour, minute = (int(x) for x in at.split(":"))
    if (now.hour, now.minute) < (hour, minute):
        return False
    last = await db.get_config(BACKUP_STAMP_KEY)
    return last != now.strftime("%Y-%m-%d")

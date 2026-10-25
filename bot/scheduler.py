"""Belgilangan vaqtda ishlaydigan vazifalar: avtomatik zaxira (standart — har dushanba, BACKUP_DAY) va kunlik
GitHub'da yangi versiya tekshiruvi."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import secrets
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .config import BACKUP_DAYS
from .db import Database
from .github import GitHubClient, GitHubError, Release, is_newer
from .updater import CALLBACK_GITHUB_INSTALL

log = logging.getLogger(__name__)
BACKUP_STAMP_KEY = "backup:last"
UPDATE_CHECK_KEY = "update:last_check"  # GitHub oxirgi marta tekshirilgan sana
UPDATE_NOTIFIED_KEY = "update:notified"  # egasiga aytilgan versiya (har versiya uchun bir marta)
NOTES_PREVIEW = 600
BACKUP_MAX_BYTES = 49 * 1024 * 1024  # Telegram bot fayl yuklash chegarasi 50 MB
ATTACHMENT_PLACEHOLDER = "[ilova (PDF/rasm) zaxiraga kiritilmagan]"


def schedule_label(at: str, weekday: int | None) -> str:
    """«har dushanba 03:30 da» yoki «har kuni 03:30 da»."""
    return f"har {'kuni' if weekday is None else BACKUP_DAYS[weekday]} {at} da"


class DailyJob:
    """Har kuni (weekday berilsa — haftada bir marta, o'sha kuni) HH:MM da (mahalliy vaqt) bitta vazifani bajaradi;
    o'tkazib yuborilgani — ishga tushganda (catch_up)."""

    def __init__(self, name: str, at: str, timezone: str, run: Callable[[], Awaitable[None]], catch_up: bool = True,
                 weekday: int | None = None):
        self.name = name
        self.hour, self.minute = (int(x) for x in at.split(":"))
        self.tz = ZoneInfo(timezone)
        self.run = run
        self.catch_up = catch_up
        self.weekday = weekday  # 0 — dushanba … 6 — yakshanba; None — har kuni
        self._task: asyncio.Task | None = None
        self.catch_up_task: asyncio.Task | None = None  # ishga tushganda o'tkazib yuborilgan ishni bajaradi

    def next_run(self, now: datetime) -> datetime:
        planned = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if self.weekday is None:
            return planned if planned > now else planned + timedelta(days=1)
        planned += timedelta(days=(self.weekday - now.weekday()) % 7)
        return planned if planned > now else planned + timedelta(days=7)

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
            # mutlaq vaqt bilan: yozgi/qishki vaqt almashadigan kuni ham soat to'g'ri, ikki marta ishlamaydi
            wait = max(0.0, self.next_run(now).timestamp() - now.timestamp())
            await asyncio.sleep(wait)
            await self._safe(self.run)


def _strip_attachments(node) -> int:
    """Base64 ilovalar o'rniga qisqa belgi qo'yadi (joyida); nechta almashtirilganini qaytaradi."""
    if isinstance(node, list):
        return sum(_strip_attachments(item) for item in node)
    if not isinstance(node, dict):
        return 0
    source = node.get("source")
    if isinstance(source, dict) and source.get("type") == "base64":
        if node.get("type") == "document":  # hujjat bloki qoladi — tiklangan suhbat Claude'ga yaroqli bo'lsin
            node["source"] = {"type": "text", "media_type": "text/plain", "data": ATTACHMENT_PLACEHOLDER}
        else:  # rasm — matn bloki bilan almashadi
            node.clear()
            node.update(type="text", text=ATTACHMENT_PLACEHOLDER)
        return 1
    return sum(_strip_attachments(value) for value in node.values())


def slim_backup(path: Path) -> int:
    """Zaxira NUSXASIDAN (asl bazadan emas) PDF/rasm baytlarini olib tashlaydi; nechta ilova olinganini qaytaradi.

    Qatorlar bittadan o'qiladi: ilovalar ko'paygan sari xotira o'smasin (hammasini birdan o'qish — OOM)."""
    conn = sqlite3.connect(path)
    try:
        ids = [row[0] for row in conn.execute("SELECT id FROM messages WHERE content LIKE '%\"base64\"%' ORDER BY id")]
        removed = 0
        for row_id in ids:
            row = conn.execute("SELECT content FROM messages WHERE id = ?", (row_id,)).fetchone()
            if row is None:
                continue
            try:
                value = json.loads(row[0])
            except ValueError:
                continue
            count = _strip_attachments(value)
            if count:
                conn.execute("UPDATE messages SET content = ? WHERE id = ?", (json.dumps(value, ensure_ascii=False), row_id))
                removed += count
        conn.commit()
        if removed:
            conn.execute("VACUUM")
        return removed
    finally:
        conn.close()


def _zip_backup(data: bytes, name: str) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, data)
    return buffer.getvalue(), name.removesuffix(".db") + ".zip"


async def make_backup(db: Database, db_path: Path, label: str) -> tuple[bytes, str]:
    """Bazaning izchil nusxasi (VACUUM INTO) — baytlar va fayl nomi.

    Nusxadan ilovalar (base64 PDF/rasm) olib tashlanadi; baribir katta bo'lsa — .zip qilinadi.
    """
    name = f"backup-{label}.db"
    target = db_path.with_name(f".{name}.{secrets.token_hex(4)}")  # bir daqiqada ikki nusxa bir-birini o'chirmasin
    try:
        await db.vacuum_into(target)
        await asyncio.to_thread(slim_backup, target)
        data = target.read_bytes()
    finally:
        target.unlink(missing_ok=True)
    if len(data) > BACKUP_MAX_BYTES:
        data, name = await asyncio.to_thread(_zip_backup, data, name)
    return data, name


async def _notify_backup_failed(bot: Bot, chat_ids: list[int], text: str) -> None:
    for chat_id in chat_ids:
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(chat_id, text)


async def send_backup(bot: Bot, db: Database, db_path: Path, chat_ids: list[int], timezone: str, *, reason: str) -> bool:
    now = datetime.now(ZoneInfo(timezone))
    try:
        data, name = await make_backup(db, db_path, now.strftime("%Y%m%d-%H%M"))
    except Exception as exc:  # noqa: BLE001
        log.exception("Zaxira nusxa olishda xato")
        await _notify_backup_failed(bot, chat_ids, f"⚠️ {reason}: zaxira nusxa olinmadi ({type(exc).__name__}). "
                                                   "/zaxira bilan qayta urinib ko'ring; takrorlansa — server loglari.")
        return False
    size_mb = len(data) / (1024 * 1024)
    if len(data) > BACKUP_MAX_BYTES:
        log.warning("Zaxira juda katta: %.1f MB", size_mb)
        await _notify_backup_failed(
            bot, chat_ids,
            f"⚠️ {reason}: zaxira Telegram'ga sig'madi ({size_mb:.0f} MB, chegara 50 MB). "
            f"Bazani serverdan o'zingiz ko'chirib oling: {db_path}",
        )
        return False
    restore_hint = "zip ichidagi faylni" if name.endswith(".zip") else "faylni"
    caption = (
        f"💾 {reason}: {now:%d.%m.%Y %H:%M}\nHajmi: {len(data) / 1024:.0f} KB.\n"
        f"Tiklash: docker compose stop → data/bot.db-wal va data/bot.db-shm ni o'chiring → {restore_hint} "
        "data/bot.db o'rniga qo'ying → docker compose start (aks holda baza buziladi)."
    )
    sent = False
    for chat_id in chat_ids:
        try:
            await bot.send_document(chat_id, BufferedInputFile(data, filename=name), caption=caption)
            sent = True
        except TelegramAPIError as exc:
            log.warning("Zaxira yuborilmadi (%s): %s", chat_id, exc)
            await _notify_backup_failed(bot, [chat_id], f"⚠️ {reason}: zaxira yuborilmadi ({exc}).")
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


async def missed_backup(db: Database, timezone: str, at: str, weekday: int | None = None) -> bool:
    """Oxirgi rejali zaxira vaqti (bugun yoki — haftalik bo'lsa — shu/o'tgan haftaning o'sha kuni) o'tgan, lekin
    o'shandan beri zaxira yuborilmagan bo'lsa — True (bot o'chiq turgan bo'lishi mumkin)."""
    if weekday is None:
        return await _missed_today(db, timezone, at, BACKUP_STAMP_KEY)
    now = datetime.now(ZoneInfo(timezone))
    hour, minute = (int(x) for x in at.split(":"))
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    planned -= timedelta(days=(now.weekday() - weekday) % 7)
    if planned > now:
        planned -= timedelta(days=7)
    last = await db.get_config(BACKUP_STAMP_KEY) or ""
    return last < planned.strftime("%Y-%m-%d")


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

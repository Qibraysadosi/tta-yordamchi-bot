"""Botning ko'rinishi: profil rasmi, tavsiflar va bannerlar (bot/assets/ dagi rasmlar)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InputProfilePhotoStatic, Message

from .db import Database

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"
AVATAR = ASSETS / "avatar.jpg"

# Bo'sh chatda «Bu bot nima qila oladi?» oynasida ko'rinadi (≤ 512 belgi)
DESCRIPTION = (
    "👋 TTA Yordamchi — Claude sun'iy intellekti asosidagi shaxsiy yordamchi.\n\n"
    "💬 Savollarga javob beradi, matn yozadi va tarjima qiladi\n"
    "📎 Rasm, PDF, Word va Excel fayllarni o'qiydi\n"
    "🔎 Yangi ma'lumotni internetdan topadi\n"
    "🧠 Fable 5.1, Opus 5.5, Sonnet 5.5 va Haiku 5.5 modellari\n\n"
    "🔒 Bot parol bilan ishlaydi: «Start» ni bosing va parolni yuboring."
)
# Bot profilida va havolani ulashganda ko'rinadi (≤ 120 belgi)
SHORT_DESCRIPTION = "Claude asosidagi shaxsiy AI yordamchi: savol-javob, fayllar, internet qidiruvi. 🔒 Parol bilan kirish."

PROFILE_TIMEOUT = 60


@lru_cache(maxsize=None)
def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def apply_profile(bot: Bot, db: Database) -> list[str]:
    """Tavsif, qisqa tavsif va profil rasmini faqat o'zgargan bo'lsa o'rnatadi.

    Har birining izi bazada saqlanadi: bot qayta ishga tushganda profil rasmi qayta yuklanmaydi
    (aks holda profil rasmlari tarixida nusxalar ko'payib ketardi), egasi BotFather'da qo'lda
    o'zgartirgan narsa ham keyingi o'zgarishgacha saqlanib qoladi.
    """
    steps: list[tuple[str, str, Any]] = [
        ("description", _text_digest(DESCRIPTION), lambda: bot.set_my_description(description=DESCRIPTION)),
        (
            "short_description",
            _text_digest(SHORT_DESCRIPTION),
            lambda: bot.set_my_short_description(short_description=SHORT_DESCRIPTION),
        ),
    ]
    if AVATAR.exists():
        steps.append(
            (
                "photo",
                _digest(AVATAR),
                lambda: bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(AVATAR))),
            )
        )
    applied: list[str] = []
    for name, digest, call in steps:
        key = f"profile:{name}"
        if await db.get_config(key) == digest:
            continue
        try:
            await asyncio.wait_for(call(), PROFILE_TIMEOUT)
        except (TelegramAPIError, asyncio.TimeoutError) as exc:
            log.warning("Bot profili (%s) o'rnatilmadi: %s", name, exc)
            continue
        await db.set_config(key, digest)
        applied.append(name)
    if applied:
        log.info("Bot profili yangilandi: %s", ", ".join(applied))
    return applied


async def send_banner(
    bot: Bot,
    db: Database,
    chat_id: int,
    name: str,
    caption: str,
    reply_markup: Any = None,
) -> Message:
    """bot/assets/<name>.jpg ni izoh bilan yuboradi. Telegram file_id keshlanadi — rasm bir marta yuklanadi."""
    path = ASSETS / f"{name}.jpg"
    if not path.exists():
        return await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    key = f"file_id:{name}:{_digest(path)}"
    cached = await db.get_config(key)
    if cached:
        try:
            return await bot.send_photo(
                chat_id, photo=cached, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        except TelegramBadRequest as exc:
            if "parse" in str(exc).lower():
                raise
            log.info("Keshdagi rasm yaroqsiz (%s) — qayta yuklanadi", exc)
    try:
        message = await bot.send_photo(
            chat_id, photo=FSInputFile(path), caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
    except TelegramBadRequest as exc:
        if "parse" in str(exc).lower():
            raise
        log.warning("Banner yuborilmadi (%s) — matn bilan davom etamiz", exc)
        return await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    if message.photo:
        await db.set_config(key, message.photo[-1].file_id)
    return message

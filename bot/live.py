"""Telegram'dagi "jonli" xabar: Claude yozayotgan javobni real vaqtda ko'rsatadi."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

from .tgformat import preview

log = logging.getLogger(__name__)

STOP_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⏹ To'xtatish", callback_data="stop")]]
)


class LiveMessage:
    INTERVAL = 1.6  # Telegram xabarni tahrirlash chastotasi (soniya)
    TYPING_EVERY = 4.5

    def __init__(self, bot: Bot, chat_id: int, message_id: int, model_label: str) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.model_label = model_label
        self.status = f"🧠 {model_label} o'ylayapti…"
        self.text = ""
        self.text_mark = 0  # oldingi qadamlarda yozilgan matn uzunligi (narx taxmini uchun)
        self.started = time.monotonic()
        self.start_usage: Any = None
        self.completed_cost = 0.0
        self._shown: str | None = None
        self._ticker: asyncio.Task | None = None

    @classmethod
    async def start(cls, bot: Bot, chat_id: int, reply_to: int, model_label: str) -> "LiveMessage":
        text = f"🧠 {model_label} o'ylayapti…"
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=None,
            reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True),
            reply_markup=STOP_KEYBOARD,
        )
        live = cls(bot, chat_id, msg.message_id, model_label)
        live._shown = text
        live._ticker = asyncio.create_task(live._tick())
        return live

    # ---- StreamListener ----
    async def on_message_start(self, model: str, usage: Any) -> None:
        self.start_usage = usage

    async def on_thinking(self) -> None:
        if not self.text:
            self.status = f"🧠 {self.model_label} o'ylayapti…"

    async def on_search(self, query: str | None) -> None:
        self.status = f"🔎 Qidirilmoqda: «{query}»" if query else "🔎 Internetdan qidirilmoqda…"

    async def on_text(self, text: str) -> None:
        self.text = text

    async def on_iteration_done(self, cost: float) -> None:
        self.completed_cost += cost
        self.start_usage = None
        self.text_mark = len(self.text)

    # ---- boshqaruv ----
    def reset(self, status: str, model_label: str) -> None:
        """Yangi urinish (masalan, zaxira model) oldidan holatni tozalash."""
        self.model_label = model_label
        self.status = status
        self.text = ""
        self.text_mark = 0
        self.started = time.monotonic()
        self.start_usage = None
        self.completed_cost = 0.0

    def _render(self) -> str:
        if self.text.strip():
            return preview(self.text) + " ▌"
        return f"{self.status} {int(time.monotonic() - self.started)} s"

    async def _tick(self) -> None:
        last_typing = 0.0
        while True:
            await asyncio.sleep(self.INTERVAL)
            now = time.monotonic()
            if now - last_typing >= self.TYPING_EVERY:
                last_typing = now
                with contextlib.suppress(TelegramAPIError):
                    await self.bot.send_chat_action(chat_id=self.chat_id, action=ChatAction.TYPING)
            rendered = self._render()
            if rendered != self._shown:
                await self._edit_preview(rendered)

    async def _edit_preview(self, text: str) -> None:
        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=self.chat_id,
                message_id=self.message_id,
                parse_mode=None,  # oraliq matn — oddiy matn, Markdown belgilari xato bermasin
                reply_markup=STOP_KEYBOARD,
            )
            self._shown = text
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except TelegramBadRequest as exc:
            if "not modified" in str(exc).lower():
                self._shown = text
            else:
                log.debug("Oraliq tahrir o'tmadi: %s", exc)
        except TelegramAPIError as exc:
            log.debug("Oraliq tahrir o'tmadi: %s", exc)

    async def stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
            self._ticker = None

    async def finish(self, chunks: list[tuple[str | None, str]], reply_markup: Any = None) -> None:
        """Yakuniy javob: birinchi bo'lak jonli xabar o'rniga, qolganlari yangi xabar bo'lib ketadi.
        reply_markup (javob tugmalari) oxirgi bo'lakka qo'yiladi."""
        await self.stop_ticker()
        last = len(chunks) - 1
        for index, (html_text, plain) in enumerate(chunks):
            markup = reply_markup if index == last else None
            if index == 0 and await self._final_edit(html_text, plain, markup):
                continue
            await self._send(html_text, plain, markup)

    async def fail(self, text: str) -> None:
        await self.finish([(None, text)])

    async def _final_edit(self, html_text: str | None, plain: str, reply_markup: Any = None) -> bool:
        for _ in range(3):
            try:
                if html_text is not None:
                    await self.bot.edit_message_text(
                        text=html_text, chat_id=self.chat_id, message_id=self.message_id, parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                else:
                    await self.bot.edit_message_text(
                        text=plain, chat_id=self.chat_id, message_id=self.message_id, parse_mode=None,
                        reply_markup=reply_markup,
                    )
                return True
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
            except TelegramBadRequest as exc:
                low = str(exc).lower()
                if "not modified" in low:
                    return True
                if html_text is not None and "not found" not in low:
                    log.info("HTML rad etildi, oddiy matn yuboriladi: %s", exc)
                    html_text = None
                    continue
                return False
            except TelegramAPIError as exc:
                log.warning("Yakuniy tahrir o'tmadi: %s", exc)
                return False
        return False

    async def _send(self, html_text: str | None, plain: str, reply_markup: Any = None) -> None:
        for _ in range(3):
            try:
                if html_text is not None:
                    await self.bot.send_message(
                        chat_id=self.chat_id, text=html_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
                    )
                else:
                    await self.bot.send_message(
                        chat_id=self.chat_id, text=plain, parse_mode=None, reply_markup=reply_markup
                    )
                return
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
            except TelegramBadRequest as exc:
                if html_text is None:
                    log.warning("Xabar yuborilmadi: %s", exc)
                    return
                log.info("HTML rad etildi, oddiy matn yuboriladi: %s", exc)
                html_text = None
            except TelegramAPIError as exc:
                log.warning("Xabar yuborilmadi: %s", exc)
                return

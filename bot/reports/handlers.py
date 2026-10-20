"""Hisobot uchun Telegram yangilanishlari: guruhlar (jim yozib borish), bot a'zoligi, reaksiyalar
va egasining buyruqlari (/hisobot, /guruhlar)."""
from __future__ import annotations

import contextlib
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageReactionUpdated,
)

from .render import split_message
from .service import ROLE_LABELS, ReportService
from .stats import current_day

log = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
NO_GROUPS_TEXT = (
    "👥 <b>Kuzatiladigan guruhlar</b>\n\n"
    "Hali guruh yo'q. Botni lidlar guruhiga (arizalar va operatorlar izohi tushadigan) va rahbarlar guruhiga "
    "qo'shing — bot sizdan guruh turini so'raydi. Lidlar guruhida botni admin qiling (huquqlar shart emas), "
    "aks holda reaksiyalarni ko'rmaydi."
)


def _service(ctx) -> ReportService | None:
    return getattr(ctx, "reports", None)


async def on_my_chat_member(update: ChatMemberUpdated, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is not None:
        await service.on_bot_membership(update, bot)


async def on_group_message(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    try:
        await service.record_message(message, bot)
    except Exception:  # noqa: BLE001 — guruhdagi xato hech kimga ko'rinmasin, faqat logda
        log.exception("Guruh xabarini yozishda xato (chat %s)", message.chat.id)


async def on_group_edit(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    try:
        await service.record_edit(message)
    except Exception:  # noqa: BLE001
        log.exception("Tahrirlangan xabarni yozishda xato (chat %s)", message.chat.id)


async def on_reaction(update: MessageReactionUpdated, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    try:
        await service.record_reaction(update)
    except Exception:  # noqa: BLE001
        log.exception("Reaksiyani yozishda xato (chat %s)", update.chat.id)


async def cb_group_role(callback: CallbackQuery, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    try:
        chat_id, action = int(parts[1]), parts[2]
    except (IndexError, ValueError):
        await callback.answer()
        return
    group = await service.store.group(chat_id)
    if group is None:
        await callback.answer("Guruh topilmadi", show_alert=True)
        return
    message = callback.message if isinstance(callback.message, Message) else None
    if action in ROLE_LABELS:
        roles = set(group.roles) if group.active else set()
        roles ^= {action}
        await service.store.set_group_roles(chat_id, roles)
        await service.store.set_group_active(chat_id, True)
        group = await service.store.group(chat_id)
        await callback.answer(("☑️ " if action in roles else "⬜ ") + ROLE_LABELS[action])
        if message is not None and group is not None:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_reply_markup(reply_markup=service.role_keyboard(group))
        return
    if action == "off":
        await service.store.set_group_roles(chat_id, ())
        await service.store.set_group_active(chat_id, False)
    else:
        await service.store.mark_group_asked(chat_id)
    group = await service.store.group(chat_id)
    await callback.answer("Saqlandi")
    if group is not None and message is not None:
        text = await service.group_summary(bot, group)
        with contextlib.suppress(TelegramBadRequest):
            await message.edit_text(text, parse_mode=ParseMode.HTML)


def _report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Rahbarlarga yuborish", callback_data="rp:send"),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data="rp:refresh"),
    ]])


async def cmd_report(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    if not await service.store.groups_with("leads"):
        await message.answer(NO_GROUPS_TEXT, parse_mode=ParseMode.HTML)
        return
    wait = await message.answer("📊 Hisobot tayyorlanmoqda…")
    now = service.now()
    _, text, _ = await service.compose(current_day(now, await service.schedule()), now, interim=True)
    with contextlib.suppress(TelegramAPIError):
        await wait.delete()
    parts = split_message(text)
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        await message.answer(part, parse_mode=ParseMode.HTML, reply_markup=_report_keyboard() if last else None)


async def cb_report(callback: CallbackQuery, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    action = (callback.data or "").partition(":")[2]
    now = service.now()
    day = current_day(now, await service.schedule())
    if action == "send":
        await callback.answer("Yuborilmoqda…")
        sent = await service.send_report(bot, day, now, interim=True)
        targets = await service.store.groups_with("leaders")
        if not sent:
            note = "⚠️ Oraliq hisobot yuborilmadi: Telegram bilan aloqa yo'q. Birozdan keyin qayta urinib ko'ring."
        elif service.last_fallback:
            note = "⚠️ Rahbarlar guruhiga yuborib bo'lmadi — hisobot sizga yuborildi (yuqorida)."
        else:
            where = "rahbarlar guruhiga" if targets else "sizga (rahbarlar guruhi belgilanmagan)"
            note = f"📤 Oraliq hisobot {where} yuborildi ({sent} ta chat)."
        if isinstance(callback.message, Message):
            with contextlib.suppress(TelegramAPIError):
                await callback.message.answer(note)
        return
    await callback.answer("Yangilanmoqda…")
    _, text, _ = await service.compose(day, now, interim=True)
    if isinstance(callback.message, Message):
        parts = split_message(text)
        with contextlib.suppress(TelegramBadRequest):
            if len(parts) == 1:
                await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=_report_keyboard())
            else:  # bitta xabarga sig'maydi — yangidan yuboriladi
                for index, part in enumerate(parts):
                    last = index == len(parts) - 1
                    await callback.message.answer(part, parse_mode=ParseMode.HTML,
                                                  reply_markup=_report_keyboard() if last else None)


async def cmd_groups(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    groups = await service.store.groups()
    if not groups:
        await message.answer(NO_GROUPS_TEXT, parse_mode=ParseMode.HTML)
        return
    lines = ["👥 <b>Kuzatiladigan guruhlar</b>", ""]
    rows = []
    for group in groups:
        roles = ", ".join(ROLE_LABELS[r] for r in group.roles if r in ROLE_LABELS) if group.active else ""
        state = roles or "kuzatilmaydi"
        admin = "" if not group.has("leads") else (" · admin ✅" if group.is_admin else " · admin emas ⚠️")
        title = html.escape(group.title or str(group.chat_id))
        lines.append(f"• <b>{title}</b> — {state}{admin}")
        rows.append([InlineKeyboardButton(text=f"⚙️ {group.title[:30] or group.chat_id}", callback_data=f"rgo:{group.chat_id}")])
    lines += ["", "Turini o'zgartirish uchun guruhni tanlang."]
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def cb_group_open(callback: CallbackQuery, ctx) -> None:
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    try:
        chat_id = int((callback.data or "").partition(":")[2])
    except ValueError:
        await callback.answer()
        return
    group = await service.store.group(chat_id)
    await callback.answer()
    if group is not None and isinstance(callback.message, Message):
        await callback.message.answer(
            service.role_prompt(group, joined=False), parse_mode=ParseMode.HTML, reply_markup=service.role_keyboard(group)
        )


def build_router() -> Router:
    router = Router(name="reports")
    router.my_chat_member.register(on_my_chat_member)
    router.message.register(on_group_message, F.chat.type.in_(GROUP_TYPES))
    router.edited_message.register(on_group_edit, F.chat.type.in_(GROUP_TYPES))
    router.message_reaction.register(on_reaction)
    private = F.chat.type == ChatType.PRIVATE
    router.message.register(cmd_report, Command("hisobot", "report"), private)
    router.message.register(cmd_groups, Command("guruhlar", "groups"), private)
    router.callback_query.register(cb_group_role, F.data.startswith("rg:"))
    router.callback_query.register(cb_group_open, F.data.startswith("rgo:"))
    router.callback_query.register(cb_report, F.data.startswith("rp:"))
    return router

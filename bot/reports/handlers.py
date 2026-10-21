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
from .tasks import DONE, OPEN, is_explicit, preset_due, task_body

log = logging.getLogger(__name__)

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
TASK_HELP = (
    "📋 <b>Vazifa berish</b>\n\n"
    "«#vazifa @username nima qilish kerak, qachongacha» — shu yerda yoki «📋 Vazifalar» guruhida.\n"
    "Misollar:\n"
    "• <code>#vazifa @malika_op ertaga 15:00 gacha oylik hisobotni topshirsin</code>\n"
    "• <code>Vazifa: @sardor_op har dushanba 10:00 da lidlar ro'yxatini tekshirsin</code>\n"
    "• Guruhda odamning xabariga reply qilib: <code>#vazifa jumagacha shartnomani tayyorlang</code>\n\n"
    "Muddat aytilmasa — keyingi ish kuni, sizdan tugma bilan so'rayman. Muddatida guruhda ijrochidan "
    "«Bajardingizmi?» deb so'rayman. Ro'yxat: /vazifalar"
)
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
        fallback = service.last_fallback
        targets = await service.store.groups_with("leaders")
        if not sent:
            note = "⚠️ Oraliq hisobot yuborilmadi: Telegram bilan aloqa yo'q. Birozdan keyin qayta urinib ko'ring."
        elif fallback:
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


# --------------------------------------------------------------------------- vazifalar
async def is_owner_task(message: Message, ctx) -> bool:
    """Lichkadagi «#vazifa …» / «Vazifa: @… …» — faqat egasidan (boshqalarniki va uzatilgan matn — AI suhbatiga)."""
    user = message.from_user
    return (user is not None and ctx.is_owner(user.id) and task_body(message.text or message.caption) is not None
            and is_explicit(message))


async def on_private_task(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is not None:
        await service.tasks.from_message(bot, message, task_body(message.text or message.caption) or "", group_chat=None)


async def cmd_task(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Vazifani faqat bot egasi beradi.")
        return
    body = task_body(message.text or message.caption) or ""
    if not body:
        await message.answer(TASK_HELP, parse_mode=ParseMode.HTML)
        return
    await service.tasks.from_message(bot, message, body, group_chat=None)


async def cmd_tasks(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    text = await service.tasks.list_text(service.now())
    button = service.app_button("vazifalar", "📋 Mini App'da ochish")
    markup = InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None
    for index, part in enumerate(parts := split_message(text)):
        await message.answer(part, parse_mode=ParseMode.HTML, reply_markup=markup if index == len(parts) - 1 else None)


async def cb_task_answer(callback: CallbackQuery, bot: Bot, ctx) -> None:
    """«Bajardingizmi?» tugmalari: faqat ijrochi (yoki egasi) bosa oladi."""
    service = _service(ctx)
    parts = (callback.data or "").split(":")
    if service is None or len(parts) != 3 or not parts[1].isdigit():
        await callback.answer()
        return
    tasks = service.tasks
    task = await tasks.store.get(int(parts[1]))
    message = callback.message if isinstance(callback.message, Message) else None
    if task is None:
        await callback.answer("Vazifa topilmadi", show_alert=True)
        return
    if not tasks.may_answer(task, callback.from_user):
        await callback.answer(f"Bu savol {task.who} uchun", show_alert=True)
        return
    pressed = (message.chat.id, message.message_id) if message is not None else None
    toast, footer = await tasks.answer(bot, task.id, callback.from_user, parts[2] == "y", service.now(), pressed)
    await callback.answer(toast)
    task = await tasks.store.get(task.id) or task
    if message is not None:
        with contextlib.suppress(TelegramBadRequest):
            if footer is None:  # allaqachon yopilgan — tugmalar olinadi
                await message.edit_reply_markup(reply_markup=None)
            else:
                await message.edit_text(tasks.poll_text(task, footer), parse_mode=ParseMode.HTML)


async def cb_task_owner(callback: CallbackQuery, bot: Bot, ctx) -> None:
    """Egasining tugmalari: muddat, bekor qilish, yopish, qayta so'rash."""
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or not parts[1].isdigit():
        await callback.answer()
        return
    tasks = service.tasks
    task = await tasks.store.get(int(parts[1]))
    message = callback.message if isinstance(callback.message, Message) else None
    if task is None:
        await callback.answer("Vazifa topilmadi", show_alert=True)
        return
    action, now = parts[2], service.now()
    schedule = await service.schedule()
    if action in ("d", "due", "ask") and task.status != OPEN:
        await callback.answer("Vazifa allaqachon yopilgan", show_alert=True)
        return
    if action == "d":
        due = preset_due(parts[3] if len(parts) > 3 else "", now, schedule)
        if due is None:
            await callback.answer("Bu tugma eskirgan — muddatni Mini App'da o'zgartiring", show_alert=True)
            return
        task = await tasks.set_due(task, due, callback.from_user.id)
        await callback.answer("Muddat saqlandi")
        if message is not None:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(await tasks.confirm_text(task), parse_mode=ParseMode.HTML,
                                        reply_markup=tasks.owner_keyboard(task, now, presets=False, schedule=schedule))
        return
    if action == "due":
        await callback.answer()
        if message is not None:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_reply_markup(reply_markup=tasks.owner_keyboard(task, now, presets=True, schedule=schedule))
        return
    if action == "rs":
        if task.repeat:
            await tasks.stop_repeat(task, callback.from_user.id)
        await callback.answer("Takror to'xtatildi — keyingi nusxalar bo'lmaydi")
        if message is not None:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(f"{message.html_text}\n\n⏹ <i>Takror to'xtatildi</i>", parse_mode=ParseMode.HTML)
        return
    if action == "ask":
        ok = await tasks.ask_now(bot, task, now)
        await callback.answer("So'rov guruhga yuborildi" if ok else "Yuborib bo'lmadi — sababi sizga yozildi", show_alert=not ok)
        return
    if action in ("x", "ok"):
        if task.status == OPEN:
            if action == "x":
                await tasks.cancel(bot, task, callback.from_user.id, now)
            else:
                await tasks.close(bot, task, callback.from_user.id, now)
        done = (await tasks.store.get(task.id) or task).status
        note = {DONE: "✅ <i>Bajarildi deb yopildi</i>"}.get(done, "🗑 <i>Bekor qilindi</i>")
        await callback.answer("Saqlandi")
        if message is not None:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(f"{message.html_text}\n\n{note}", parse_mode=ParseMode.HTML)
        return
    await callback.answer()


def build_router() -> Router:
    router = Router(name="reports")
    router.my_chat_member.register(on_my_chat_member)
    router.message.register(on_group_message, F.chat.type.in_(GROUP_TYPES))
    router.edited_message.register(on_group_edit, F.chat.type.in_(GROUP_TYPES))
    router.message_reaction.register(on_reaction)
    private = F.chat.type == ChatType.PRIVATE
    router.message.register(cmd_report, Command("hisobot", "report"), private)
    router.message.register(cmd_groups, Command("guruhlar", "groups"), private)
    router.message.register(cmd_task, Command("vazifa", "task"), private)
    router.message.register(cmd_tasks, Command("vazifalar", "tasks"), private)
    router.message.register(on_private_task, private, is_owner_task)
    router.callback_query.register(cb_task_answer, F.data.startswith("tq:"))
    router.callback_query.register(cb_task_owner, F.data.startswith("tk:"))
    router.callback_query.register(cb_group_role, F.data.startswith("rg:"))
    router.callback_query.register(cb_group_open, F.data.startswith("rgo:"))
    router.callback_query.register(cb_report, F.data.startswith("rp:"))
    return router

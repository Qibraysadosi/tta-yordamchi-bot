"""Hisobot uchun Telegram yangilanishlari: guruhlar (jim yozib borish), bot a'zoligi, reaksiyalar
va egasining buyruqlari (/hisobot, /guruhlar, /vazifa, /vazifalar, /kundalik, /excel, /xodimlar, /vaucher,
/tavsiya)."""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import AiogramError, TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    MessageReactionUpdated,
)

from . import staff
from .advisor import INTERIM_REUSE, Advice, AdviceError
from .archive import parse_key
from .render import split_message
from .service import ROLE_LABELS, ReportService
from .stats import display_day, latest_due_day, period_bounds
from .tasks import DONE, OPEN, is_explicit, preset_due, task_body
from .vouchers import IMPORT_LIMIT, IMPORT_WAIT, VoucherError

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


def _shown_day(now, schedule, tz) -> tuple:
    """/hisobot uchun kun: hisobot vaqtidan yarim tungacha — bugungi (tugagan) davr; (kun, davr hali davom etyaptimi)."""
    day = display_day(now, schedule)
    return day, now < period_bounds(day, schedule, tz)[1]


def _report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Rahbarlarga yuborish", callback_data="rp:send"),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data="rp:refresh"),
    ]])


async def cmd_report(message: Message, bot: Bot, ctx) -> None:
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
    day, live = _shown_day(now, await service.schedule(), service.tz)
    overview, text, reasons = await service.compose(day, now, interim=live)
    card = await service.card(overview, reasons, interim=live, now=now)
    with contextlib.suppress(TelegramAPIError):
        await wait.delete()
    if card is not None:
        try:
            await service.send_card(bot, message.chat.id, card, _report_keyboard())
            return
        except TelegramBadRequest as exc:  # rasm o'tmasa — matn
            log.warning("Hisobot rasmi yuborilmadi: %s", exc)
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
    day, live = _shown_day(now, await service.schedule(), service.tz)
    if action == "send":
        await callback.answer("Yuborilmoqda…")
        # tugagan, lekin hali yuborilmagan kun (19:00–19:02) — shu yuborish kunlik hisobot bo'ladi (takror kelmaydi)
        record = not live and not await service.store.report_for_day(day.isoformat())
        sent = await service.send_report(bot, day, now, interim=live, record=record)
        fallback = service.last_fallback
        targets = await service.store.groups_with("leaders")
        if not sent:
            note = "⚠️ Oraliq hisobot yuborilmadi: Telegram bilan aloqa yo'q. Birozdan keyin qayta urinib ko'ring."
        elif fallback:
            note = "⚠️ Rahbarlar guruhiga yuborib bo'lmadi — hisobot sizga yuborildi (yuqorida)."
        else:
            where = "rahbarlar guruhiga" if targets else "sizga (rahbarlar guruhi belgilanmagan)"
            note = f"📤 {'Oraliq hisobot' if live else 'Hisobot'} {where} yuborildi ({sent} ta chat)."
        if isinstance(callback.message, Message):
            with contextlib.suppress(TelegramAPIError):
                await callback.message.answer(note)
        return
    await callback.answer("Yangilanmoqda…")
    overview, text, reasons = await service.compose(day, now, interim=live)
    card = await service.card(overview, reasons, interim=live, now=now)
    if card is not None and isinstance(callback.message, Message):
        message = callback.message
        if message.photo and card.fits():  # o'sha xabarning rasmi va izohi almashtiriladi
            media = InputMediaPhoto(media=BufferedInputFile(card.png, filename="hisobot.png"), caption=card.caption,
                                    parse_mode=ParseMode.HTML)
            try:
                await message.edit_media(media=media, reply_markup=_report_keyboard())
                return
            except TelegramBadRequest as exc:
                log.info("Hisobot rasmi yangilanmadi, yangisi yuboriladi: %s", exc)
        with contextlib.suppress(TelegramBadRequest):
            await service.send_card(bot, message.chat.id, card, _report_keyboard())
            return
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
        reads = group.has("leads") or group.has("vouchers")  # hamma xabarni o'qishi kerak
        admin = "" if not reads else (" · admin ✅" if group.is_admin else " · admin emas ⚠️")
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
def _bot_username(ctx) -> str | None:
    service = _service(ctx)
    return service.bot_info.get("username") if service is not None else None


async def is_owner_task(message: Message, ctx) -> bool:
    """Lichkadagi «#vazifa …» / «Vazifa: @… …» — faqat egasidan (boshqalarniki va uzatilgan matn — AI suhbatiga)."""
    user = message.from_user
    return (user is not None and ctx.is_owner(user.id) and is_explicit(message)
            and task_body(message.text or message.caption, _bot_username(ctx)) is not None)


async def on_private_task(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is not None:
        body = task_body(message.text or message.caption, _bot_username(ctx)) or ""
        await service.tasks.from_message(bot, message, body, group_chat=None)


async def cmd_task(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Vazifani faqat bot egasi beradi.")
        return
    body = task_body(message.text or message.caption, _bot_username(ctx)) or ""
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
        fresh = await tasks.store.get(task.id) or task
        if message is not None:
            old = message.reply_markup.inline_keyboard if message.reply_markup else []
            alert = any((b.callback_data or "").endswith(":ask") for row in old for b in row)
            markup = None
            if fresh.status == OPEN:  # boshqa tugmalar qoladi (⏹ siz)
                markup = tasks.alert_keyboard(fresh) if alert else tasks.owner_keyboard(fresh, now, presets=False,
                                                                                         schedule=schedule)
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(f"{message.html_text}\n\n⏹ <i>Takror to'xtatildi</i>", parse_mode=ParseMode.HTML,
                                        reply_markup=markup)
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
        note = {DONE: "✅ <i>Bajarildi deb yopildi</i>"}.get(
            done, "🗑 <i>Bekor qilindi" + (" — takror ham to'xtatildi" if task.repeat else "") + "</i>")
        await callback.answer("Saqlandi")
        if message is not None:
            with contextlib.suppress(TelegramBadRequest):
                await message.edit_text(f"{message.html_text}\n\n{note}", parse_mode=ParseMode.HTML)
        return
    await callback.answer()


# --------------------------------------------------------------------------- kundalik va Excel (v1.9)
async def cmd_journal(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    wait = await message.answer("📓 Kundalik tayyorlanmoqda…")
    now = service.now()
    text = await service.archive.journal_text(now, interim=True)
    with contextlib.suppress(TelegramAPIError):
        await wait.delete()
    schedule = await service.schedule()
    if schedule.journal_enabled:
        text += f"\n\n<i>Kundalik har ish kuni {schedule.journal_time} da o'zi keladi.</i>"
    await service.refresh_bot_info(bot)
    await service.archive.send_text(bot, message.chat.id, text, service.archive.journal_markup(now.date()))


async def cmd_excel(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    if not await service.store.groups_with("leads"):
        await message.answer(NO_GROUPS_TEXT, parse_mode=ParseMode.HTML)
        return
    schedule = await service.schedule()
    auto = []
    if schedule.weekly_enabled:
        auto.append(f"har dushanba {schedule.excel_time} da — o'tgan hafta")
    if schedule.monthly_enabled:
        auto.append(f"oyning 1-sanasi {schedule.excel_time} da — o'tgan oy")
    text = ("📊 <b>Excel hisobot</b>\nQaysi davr? Fayl sizga to'liq (telefon raqamlari bilan) keladi.\n\n"
            + ("Avtomatik: " + "; ".join(auto) + ". Rahbarlar guruhiga — telefonlari yashirilgan nusxa."
               if auto else "Avtomatik yuborish o'chiq (Mini App → Hisobotlar → Sozlamalar)."))
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=service.archive.excel_keyboard(service.now()))


async def cb_excel(callback: CallbackQuery, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    period = parse_key((callback.data or "").partition(":")[2])
    if period is None:
        await callback.answer("Bu tugma eskirgan — /excel ni qayta yozing", show_alert=True)
        return
    await callback.answer("Tayyorlanmoqda…")
    kind, first, last = period
    chat_id = callback.from_user.id
    with contextlib.suppress(TelegramAPIError):
        await bot.send_chat_action(chat_id, "upload_document")
    try:
        await service.archive.send_to(bot, chat_id, kind, first, last, service.now(), masked=False)
    except Exception:  # noqa: BLE001 — xato egasiga ko'rinsin, tafsiloti logda
        log.exception("Excel tayyorlanmadi yoki yuborilmadi (%s)", callback.data)
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(chat_id, "⚠️ Excel faylni tayyorlab bo'lmadi. Birozdan keyin qayta urinib ko'ring.")


# --------------------------------------------------------------------------- xodimlar (v1.11)
STAFF_WAIT = 15 * 60  # /xodimlar'dan keyin ro'yxat shuncha soniya ichida alohida xabarda ham qabul qilinadi


async def _send_parts(message: Message, text: str) -> None:
    for part in split_message(text):
        await message.answer(part, parse_mode=ParseMode.HTML)


async def _apply_staff(message: Message, bot: Bot, service: ReportService, text: str) -> None:
    await service.ensure()
    entries = staff.parse(text)
    if not entries:
        await _send_parts(message, await staff.list_text(service.store))
        return
    result = await staff.apply(service, bot, entries)
    await _send_parts(message, staff.outcome_text(result, skipped=max(0, len(entries) - staff.MAX_LINES)))


async def cmd_staff(message: Message, bot: Bot, ctx) -> None:
    """/xodimlar — ro'yxat; «/xodimlar» + qatorlar (yoki keyingi xabarda qatorlar) — qo'shish / o'zgartirish."""
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    body = staff.command_body(message.text or message.caption or "")
    if body:
        service.staff_waiting.pop(message.from_user.id, None)
        await _apply_staff(message, bot, service, body)
        return
    service.staff_waiting[message.from_user.id] = time.monotonic()
    await service.ensure()
    await _send_parts(message, await staff.list_text(service.store))


def _waiting(service: ReportService, user_id: int) -> bool:
    started = service.staff_waiting.get(user_id)
    return started is not None and time.monotonic() - started <= STAFF_WAIT


async def is_staff_list(message: Message, ctx) -> bool:
    """/xodimlar'dan keyingi xabar ro'yxat bo'lsa — qabul qilinadi; boshqa narsa bo'lsa, kutish tugaydi va xabar
    odatdagidek (AI suhbatiga) ketadi. Uzatilgan (forward) xabar — is_staff_forward."""
    service = _service(ctx)
    user = message.from_user
    if service is None or user is None or user.id not in service.staff_waiting or not ctx.is_owner(user.id):
        return False
    if _waiting(service, user.id) and staff.looks_like_list(message.text or message.caption or ""):
        return True
    if message.forward_origin is not None and _waiting(service, user.id):
        return False  # xodimning xabari uzatildi — is_staff_forward (kutish davom etadi)
    service.staff_waiting.pop(user.id, None)
    return False


async def on_staff_list(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    service.staff_waiting.pop(message.from_user.id, None)
    await _apply_staff(message, bot, service, message.text or message.caption or "")


async def is_owner_contact(message: Message, ctx) -> bool:
    user = message.from_user
    return message.contact is not None and user is not None and ctx.is_owner(user.id) and _service(ctx) is not None


async def is_staff_forward(message: Message, ctx) -> bool:
    """/xodimlar'dan keyin xodimning biror xabari uzatildi (username'i va telefoni yo'q xodim uchun)."""
    service = _service(ctx)
    user = message.from_user
    return (service is not None and user is not None and message.forward_origin is not None and ctx.is_owner(user.id)
            and _waiting(service, user.id))


async def _ask_role(message: Message, service: ReportService, user_id: int, username: str | None,
                    full_name: str, default_name: str) -> None:
    store = service.store
    username = username or (await store.users()).get(user_id, (None, ""))[0]
    current = await staff.listed_as(store, user_id, username)
    name = next((p.name for p in current if p.name), "") or default_name
    choices = await staff.contact_choices(store, user_id, username, full_name, name)
    service.contact_choices[user_id] = choices
    text = await staff.contact_prompt(store, user_id, username, full_name, name)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=staff.contact_keyboard(user_id, choices))


async def on_contact(message: Message, ctx) -> None:
    """Egasi xodim kontaktini ulashdi (username'i yo'q xodim ham): rol tugmalari bilan so'raladi."""
    service = _service(ctx)
    contact = message.contact
    if service is None or contact is None:
        return
    full_name = " ".join(x for x in (contact.first_name, contact.last_name) if x)
    if not contact.user_id:
        await message.answer(
            f"📇 «{html.escape(full_name or contact.phone_number)}» kontaktida Telegram ID yo'q — bot uni taniy olmaydi "
            "(raqam Telegram'da ro'yxatdan o'tmagan yoki kontakt telefoningizda saqlanmagan).\n"
            "Xodimning @username'ini /xodimlar bilan yozing yoki /xodimlar'dan keyin uning xabarini uzating.",
            parse_mode=ParseMode.HTML)
        return
    await service.ensure()
    await _ask_role(message, service, contact.user_id, None, full_name,
                    staff.contact_name(contact.first_name, contact.last_name))


async def on_staff_forward(message: Message, bot: Bot, ctx) -> None:
    """Uzatilgan xabar: ro'yxat matni bo'lsa — qo'llanadi, bo'lmasa — yuboruvchisi (xodim) rol so'rab qo'shiladi."""
    service = _service(ctx)
    if service is None:
        return
    text = message.text or message.caption or ""
    if staff.looks_like_list(text):
        service.staff_waiting.pop(message.from_user.id, None)
        await _apply_staff(message, bot, service, text)
        return
    origin = message.forward_origin
    sender = getattr(origin, "sender_user", None)
    if sender is None or sender.is_bot:
        hidden = getattr(origin, "sender_user_name", None)
        await message.answer(
            (f"🔒 «{html.escape(hidden)}» xabarlarini uzatganda akkaunti yashiriladi (Telegram maxfiylik sozlamasi) — "
             "bot uni taniy olmaydi." if hidden else "Bu xabar odamdan emas (kanal yoki bot) — xodimning o'z xabarini uzating.")
            + "\nBoshqa yo'l: u lidga belgi qo'ygach Mini App → Hisobotlar → Operatorlar → «Ro'yxatda yo'q» → bosing → "
            "«Kimning akkaunti».", parse_mode=ParseMode.HTML)
        return
    await service.ensure()
    username = (sender.username or "").lower() or None
    await _ask_role(message, service, sender.id, username, sender.full_name, staff.contact_name(sender.first_name, sender.last_name))


async def is_contact_rename(message: Message, ctx) -> bool:
    """«✏️ Boshqa ism» bosilgandan keyingi qisqa matn — kontakt uchun ism."""
    service = _service(ctx)
    user = message.from_user
    if service is None or user is None or user.id not in service.contact_rename or not ctx.is_owner(user.id):
        return False
    target, started = service.contact_rename[user.id]
    value = " ".join((message.text or "").split())
    if (time.monotonic() - started > STAFF_WAIT or not value or value.startswith("/") or len(value) > staff.NAME_LIMIT
            or "\n" in (message.text or "") or "@" in value or message.forward_origin is not None
            or target not in service.contact_choices):
        service.contact_rename.pop(user.id, None)
        return False
    return True


async def on_contact_rename(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    target, _ = service.contact_rename.pop(message.from_user.id)
    choices = service.contact_choices[target]
    choices.name = " ".join((message.text or "").split())
    text = await staff.contact_prompt(service.store, target, choices.username, choices.full_name, choices.name)
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=staff.contact_keyboard(target, choices))


async def cb_staff_contact(callback: CallbackQuery, bot: Bot, ctx) -> None:
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer()
        return
    user_id, code = int(parts[1]), parts[2]
    choices = service.contact_choices.get(user_id)
    if choices is None:  # bot qayta ishga tushgan — tanlovlar xotirada yo'q
        await callback.answer("Bu tugma eskirgan — kontaktni yoki xabarni qayta yuboring", show_alert=True)
        return
    if code == "nm":
        service.contact_rename[callback.from_user.id] = (user_id, time.monotonic())
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer("✏️ Hisobotdagi ismini yozing (masalan, <b>Mastura</b>):", parse_mode=ParseMode.HTML)
        return
    entry = staff.choice_entry(user_id, choices.username, choices, code)
    if entry is None:
        await callback.answer()
        return
    result = await staff.apply(service, bot, [entry])
    await callback.answer("Saqlandi")
    if isinstance(callback.message, Message):
        head = (callback.message.html_text or "").split("\n")[0]
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_text(head + "\n" + "\n".join(result.lines) + "\nRo'yxat: /xodimlar",
                                             parse_mode=ParseMode.HTML, reply_markup=None)


# ---------------------------------------------------------------------- v1.14: vaucherlar
EXPORT_TOO_BIG = (
    "⚠️ Fayl 20 MB dan katta — Telegram botlar bundan kattasini yuklab ololmaydi. Eksportni bo'lib qiling: "
    "Export chat history → «From / To» sanalari (masalan, oyma-oy) yoki rasmlarsiz (faqat JSON) eksport qiling."
)


async def cmd_voucher(message: Message, ctx) -> None:
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    await service.ensure()
    service.vouchers.waiting[message.from_user.id] = time.monotonic()  # keyingi ZIP — eksport sifatida tekshiriladi
    await message.answer(await service.vouchers.overview_text(service.now()), parse_mode=ParseMode.HTML)


async def is_voucher_export(message: Message, ctx) -> bool:
    """Egasi Telegram Desktop eksportini yubordi (ZIP yoki result.json): nomidan («ChatExport…», «result.json») yoki
    /vaucher'dan keyin 15 daqiqa ichida. Yangilanish kutilayotgan bo'lsa (/yangilash) — tegilmaydi."""
    service = _service(ctx)
    user, document = message.from_user, message.document
    if service is None or user is None or document is None or not ctx.is_owner(user.id):
        return False
    if user.id in getattr(ctx, "awaiting_update", {}) or (message.caption or "").strip().lower().startswith("/yangilash"):
        return False
    name = (document.file_name or "").lower()
    if not name.endswith((".zip", ".json")):
        return False
    if name == "result.json" or name.startswith("chatexport"):
        return True
    started = service.vouchers.waiting.get(user.id)
    return started is not None and time.monotonic() - started <= IMPORT_WAIT


async def on_voucher_export(message: Message, bot: Bot, ctx) -> None:
    service = _service(ctx)
    service.vouchers.waiting.pop(message.from_user.id, None)
    document = message.document
    if (document.file_size or 0) > IMPORT_LIMIT:
        await message.answer(EXPORT_TOO_BIG)
        return
    status = await message.answer("📥 Eksport o'qilmoqda…")
    try:
        await service.ensure()
        buffer = await bot.download(document.file_id, timeout=180)
        result = await service.vouchers.import_export(buffer.read(), document.file_name or "", message.chat.id)
    except VoucherError as exc:
        await status.edit_text(f"⚠️ {exc}")
        return
    except AiogramError as exc:
        log.warning("Eksportni yuklab bo'lmadi: %s", exc)
        await status.edit_text("⚠️ Faylni Telegram'dan yuklab bo'lmadi — qayta yuboring.")
        return
    except Exception:  # noqa: BLE001
        log.exception("Vaucher eksportini o'qishda xato")
        await status.edit_text("⚠️ Eksportni o'qishda xato. Telegram Desktop'da JSON formatida qayta eksport qilib ko'ring.")
        return
    lines = [f"📥 <b>«{html.escape(result.title)}»</b>: eksportda {result.total} ta xabar — {result.new} tasi yangi"
             + (f", {result.known} tasi avval bor edi" if result.known else "") + "."]
    if result.photos or result.photos_missing:
        line = f"🖼 Rasmlar: {result.photos} ta"
        if result.photos_missing:
            line += (f"; {result.photos_missing} tasi eksportga kirmagan (Export → «Photos» belgisi bilan qayta eksport "
                     "qilsangiz, ular ham o'qiladi)")
        lines.append(line)
    if result.queued:
        minutes = max(1, round(result.queued * 4 / 60))
        cost = f"~${result.estimate_usd:.2f}" if result.estimate_usd >= 0.01 else "<$0.01"
        lines.append(f"🤖 AI {result.queued} ta postni o'qiydi — taxminan {minutes} daqiqa, {cost}. Tugagach xabar beraman.")
    else:
        lines.append("Tahlil qilinadigan yangi xabar yo'q.")
    await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
    service.vouchers.kick(bot)


# ---------------------------------------------------------------------- v1.15: kunlik tavsiya
ADVICE_ON = ("yoq", "yoqish", "on")
ADVICE_OFF = ("o'chir", "ochir", "o'chirish", "off")
ADVICE_NOW = ("bugun", "hozir", "today", "now")
ADVICE_TIME_RE = re.compile(r"([01]?\d|2[0-3])[:.]([0-5]\d)")
ADVICE_REDO_DAYS = 14  # «🔄 Qayta tahlil» — shuncha kundan eski tavsiya uchun ishlamaydi
ADVICE_HELP = (
    "💡 <b>Kunlik tavsiya</b>\n"
    "AI kun natijalari, lidlar, operator izohlari, guruh yozishmalari, vaucher va vazifalarni ko'rib chiqib, nimani "
    "yaxshilash kerakligini yozadi: reklama, call center, vaucher, vazifalar.\n\n"
    "/tavsiya — so'nggi tugagan hisobot kuni bo'yicha\n"
    "/tavsiya bugun — hozirgi holat bo'yicha (kun tugamasdan)\n"
    "/tavsiya 20:30 — har kuni keladigan vaqtni o'zgartirish\n"
    "/tavsiya o'chir · /tavsiya yoq — avtomatik yuborish\n\n"
    "Tavsiya xabariga «Ответить» qilib savol yozsangiz, AI shu tavsiya bo'yicha davom ettiradi."
)


def _advice_markup(advice: Advice) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Qayta tahlil qilish", callback_data=f"adv:re:{advice.day.isoformat()}")
    ]])


async def _typing(bot: Bot, chat_id: int) -> None:
    while True:
        with contextlib.suppress(AiogramError):
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        await asyncio.sleep(4.5)


async def _advice_generate(bot: Bot, chat_id: int, service: ReportService, day: date) -> None:
    """Hozir tahlil qilib, natijani shu chatga yuboradi (holat xabari bilan)."""
    advisor = service.advisor
    status = await bot.send_message(
        chat_id, f"🧠 {advisor.model_label()} kun ma'lumotlarini tahlil qilmoqda… Odatda 1–3 daqiqa.")
    typing = asyncio.create_task(_typing(bot, chat_id))

    async def ready(advice: Advice) -> None:
        typing.cancel()
        with contextlib.suppress(TelegramAPIError):
            await status.delete()
        await advisor.deliver(bot, [chat_id], advice, markup=_advice_markup(advice))

    try:
        await advisor.request(bot, day, service.now(), on_ready=ready)
    except AdviceError as exc:
        text = f"⚠️ Tavsiya tayyorlanmadi: {html.escape(str(exc))}"
        if exc.retryable:
            text += "\nBirozdan keyin qayta urinib ko'ring: /tavsiya"
        with contextlib.suppress(TelegramAPIError):
            await status.edit_text(text, parse_mode=ParseMode.HTML)
        return
    except Exception:  # noqa: BLE001
        log.exception("Tavsiya tayyorlashda xato")
        with contextlib.suppress(TelegramAPIError):
            await status.edit_text("⚠️ Tavsiya tayyorlashda xato. Birozdan keyin qayta urinib ko'ring: /tavsiya")
        return
    finally:
        typing.cancel()


async def cmd_advice(message: Message, command: CommandObject, bot: Bot, ctx) -> None:
    """/tavsiya — saqlangan tavsiya yoki hozir tahlil; «bugun» — oraliq; vaqt, yoq / o'chir — jadval."""
    service = _service(ctx)
    if service is None:
        return
    if not ctx.is_owner(message.from_user.id):
        await message.answer("Bu buyruq faqat bot egasi uchun.")
        return
    await service.ensure()
    advisor = service.advisor
    arg = " ".join((command.args or "").lower().split())
    if arg in ADVICE_OFF:
        await service.save_schedule({"advice_enabled": False})
        await message.answer("🔕 Kunlik tavsiya o'zi kelmaydi. Kerak bo'lganda: /tavsiya · qayta yoqish: /tavsiya yoq")
        return
    moment = ADVICE_TIME_RE.fullmatch(arg)
    if arg in ADVICE_ON or moment:
        changes: dict = {"advice_enabled": True}
        if moment:
            changes["advice_time"] = f"{int(moment.group(1)):02d}:{moment.group(2)}"
        schedule = await service.save_schedule(changes)
        await message.answer(f"✅ Kunlik tavsiya: {advisor.describe(schedule)}.")
        return
    schedule = await service.schedule()
    if arg and arg not in ADVICE_NOW:
        await message.answer(f"{ADVICE_HELP}\n\nHozir: {html.escape(advisor.describe(schedule))}.", parse_mode=ParseMode.HTML)
        return
    if not await service.store.groups_with("leads"):
        await message.answer(NO_GROUPS_TEXT, parse_mode=ParseMode.HTML)
        return
    if advisor.busy:  # jadvaldagi yoki boshqa tahlil — tugagach o'zi keladi
        await message.answer("⏳ Tahlil ketmoqda — tayyor bo'lishi bilan shu yerga keladi.")
        return
    now = service.now()
    day = display_day(now, schedule) if arg in ADVICE_NOW else (latest_due_day(now, schedule) or display_day(now, schedule))
    live = now < period_bounds(day, schedule, service.tz)[1]
    saved = await advisor.store.get(day.isoformat())
    if saved is not None and (_fresh(saved) if live else not saved.interim):
        await advisor.deliver(bot, [message.chat.id], saved, markup=_advice_markup(saved))  # saqlangani — bepul
        return
    await _advice_generate(bot, message.chat.id, service, day)


def _fresh(advice: Advice) -> bool:
    """Oraliq tavsiya hozirgina tayyorlangan (ketma-ket bosilganda qayta tahlil qilinmaydi)."""
    try:
        created = datetime.strptime(advice.created_at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - created < INTERIM_REUSE


async def cb_advice(callback: CallbackQuery, bot: Bot, ctx) -> None:
    """«🔄 Qayta tahlil qilish» — o'sha kun uchun yangidan (pullik) tahlil."""
    service = _service(ctx)
    if service is None or not ctx.is_owner(callback.from_user.id):
        await callback.answer("⛔ Faqat bot egasi uchun", show_alert=True)
        return
    try:
        day = datetime.strptime((callback.data or "").split(":")[2], "%Y-%m-%d").date()
    except (IndexError, ValueError):
        await callback.answer()
        return
    await service.ensure()
    now = service.now()
    schedule = await service.schedule()
    if day.weekday() not in schedule.days or not timedelta(0) <= display_day(now, schedule) - day <= timedelta(days=ADVICE_REDO_DAYS):
        await callback.answer("Bu kun uchun qayta tahlil qilib bo'lmaydi — /tavsiya", show_alert=True)
        return
    if service.advisor.busy:
        await callback.answer("⏳ Tahlil allaqachon ketmoqda — kuting", show_alert=True)
        return
    await callback.answer("Tahlil boshlandi…")
    chat_id = callback.message.chat.id if callback.message is not None else callback.from_user.id
    await _advice_generate(bot, chat_id, service, day)


def build_router() -> Router:
    router = Router(name="reports")
    router.my_chat_member.register(on_my_chat_member)
    router.message.register(on_group_message, F.chat.type.in_(GROUP_TYPES))
    router.edited_message.register(on_group_edit, F.chat.type.in_(GROUP_TYPES))
    router.message_reaction.register(on_reaction)
    private = F.chat.type == ChatType.PRIVATE
    router.message.register(cmd_report, Command("hisobot", "report"), private)
    router.message.register(cmd_groups, Command("guruhlar", "groups"), private)
    router.message.register(cmd_task, Command("vazifa", "task"), private, F.forward_origin.is_(None))
    router.message.register(cmd_tasks, Command("vazifalar", "tasks"), private)
    router.message.register(cmd_journal, Command("kundalik", "journal"), private)
    router.message.register(cmd_excel, Command("excel", "export"), private)
    router.message.register(cmd_staff, Command("xodimlar", "staff"), private, F.forward_origin.is_(None))
    router.message.register(cmd_voucher, Command("vaucher", "voucher", "vaucherlar"), private)
    router.message.register(cmd_advice, Command("tavsiya", "advice", "maslahat"), private)
    router.message.register(on_voucher_export, private, is_voucher_export)
    router.message.register(on_contact, private, is_owner_contact)
    router.message.register(on_contact_rename, private, is_contact_rename)
    router.message.register(on_staff_list, private, is_staff_list)
    router.message.register(on_staff_forward, private, is_staff_forward)
    router.message.register(on_private_task, private, is_owner_task)
    router.callback_query.register(cb_task_answer, F.data.startswith("tq:"))
    router.callback_query.register(cb_task_owner, F.data.startswith("tk:"))
    router.callback_query.register(cb_group_role, F.data.startswith("rg:"))
    router.callback_query.register(cb_group_open, F.data.startswith("rgo:"))
    router.callback_query.register(cb_report, F.data.startswith("rp:"))
    router.callback_query.register(cb_excel, F.data.startswith("xl:"))
    router.callback_query.register(cb_staff_contact, F.data.startswith("sc:"))
    router.callback_query.register(cb_advice, F.data.startswith("adv:"))
    return router

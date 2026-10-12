"""Bot ko'rinishi: /start menyusi tugmalari, profil (tavsif, rasm) va bannerlar."""
import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update

import fakes
from bot import branding
from bot.db import Database
from test_flow import OWNER, Env, run


async def press_on_photo(env: Env, data: str, user_id: int = OWNER) -> None:
    env.update_id += 1
    update = Update.model_validate({"update_id": env.update_id, "callback_query": {
        "id": f"cb{env.update_id}", "from": {"id": user_id, "is_bot": False, "first_name": "Shohrux"},
        "chat_instance": "ci", "data": data,
        "message": {"message_id": 7, "date": 1760240000, "chat": {"id": user_id, "type": "private"},
                    "photo": [{"file_id": "p", "file_unique_id": "pu", "width": 1280, "height": 720}],
                    "caption": "menyu"},
    }}, context={"bot": env.bot})
    await env.dp.feed_update(env.bot, update)


def test_menu_buttons(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        try:
            await press_on_photo(env, "menu:search")
            edit = env.session.of("EditMessageCaption")[-1]
            assert "qidiruv: o'chiq" in edit.caption
            assert edit.reply_markup.inline_keyboard[1][0].text == "🔎 Qidiruv ⬜"
            assert env.session.of("AnswerCallbackQuery")[-1].text == "🔎 Qidiruv o'chirildi"
            assert (await env.ctx.user_settings(OWNER)).web_search is False

            await press_on_photo(env, "menu:model")
            assert "Modelni tanlang" in env.session.last_out()
            await press_on_photo(env, "menu:effort")
            assert "Fikrlash darajasi" in env.session.last_out()
            await press_on_photo(env, "menu:cost")
            assert "Xarajat hisoboti" in env.session.last_out()
            await press_on_photo(env, "menu:help")
            assert "Qanday foydalanish" in env.session.last_out()
            await press_on_photo(env, "menu:admin")
            assert "Admin panel" in env.session.last_out() and "Versiya: <b>v1.1.0</b>" in env.session.last_out()

            before = (await env.ctx.user_settings(OWNER)).conv_id
            await env.db.add_turn(OWNER, before, [{"type": "text", "text": "x"}], "y")  # bo'sh suhbatdan /new raqamni oshirmaydi
            await press_on_photo(env, "menu:new")
            assert (await env.ctx.user_settings(OWNER)).conv_id == before + 1
            assert "Yangi suhbat boshlandi" in env.session.last_out()
        finally:
            await env.close()
    run(go())


def test_apply_profile_only_when_changed(tmp_path, monkeypatch):
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        session = fakes.FakeSession()
        bot = Bot("1:T", session=session)
        try:
            assert await branding.apply_profile(bot, db) == ["description", "short_description", "photo"]
            assert session.of("SetMyDescription")[0].description == branding.DESCRIPTION
            assert session.of("SetMyShortDescription")[0].short_description == branding.SHORT_DESCRIPTION
            photo = session.of("SetMyProfilePhoto")[0].photo
            assert photo.type == "static"
            assert await branding.apply_profile(bot, db) == []  # qayta ishga tushganda hech narsa yuborilmaydi

            monkeypatch.setattr(branding, "DESCRIPTION", "Yangi tavsif")
            assert await branding.apply_profile(bot, db) == ["description"]
            assert len(session.of("SetMyProfilePhoto")) == 1  # rasm qayta yuklanmadi

            # rasm o'rnatilmasa — keyingi safar faqat rasm qayta urinadi
            await db.delete_config("profile:photo")

            class Failing(fakes.FakeSession):
                async def make_request(self, bot, method, timeout=None):
                    if type(method).__name__ == "SetMyProfilePhoto":
                        raise TelegramBadRequest(method=method, message="Bad Request: photo is too small")
                    return await super().make_request(bot, method, timeout)

            failing = Failing()
            assert await branding.apply_profile(Bot("1:T", session=failing), db) == []
            assert await db.get_config("profile:photo") is None
            assert await branding.apply_profile(bot, db) == ["photo"]
        finally:
            await db.close()
    run(go())


def test_send_banner_cache_and_fallbacks(tmp_path, monkeypatch):
    async def go():
        db = await Database.open(tmp_path / "bot.db")

        class StaleCache(fakes.FakeSession):
            async def make_request(self, bot, method, timeout=None):
                if type(method).__name__ == "SendPhoto" and isinstance(method.photo, str):
                    raise TelegramBadRequest(method=method, message="Bad Request: wrong file identifier")
                return await super().make_request(bot, method, timeout)

        session = StaleCache()
        bot = Bot("1:T", session=session)
        try:
            key = f"file_id:welcome:{branding._digest(branding.ASSETS / 'welcome.jpg')}"
            await db.set_config(key, "eskirgan-id")
            message = await branding.send_banner(bot, db, 5, "welcome", "<b>Salom</b>")
            assert message.photo and session.photo_uploads == 1
            assert await db.get_config(key) == message.photo[-1].file_id  # yangi file_id saqlandi

            monkeypatch.setattr(branding, "ASSETS", tmp_path / "yoq")
            await branding.send_banner(bot, db, 5, "welcome", "<b>Salom</b>")
            assert session.of("SendMessage")[-1].text == "<b>Salom</b>"  # rasm bo'lmasa — matn
        finally:
            await db.close()
    run(go())


def test_texts_fit_telegram_limits():
    from bot.handlers import LOCKED_TEXT, WELCOME_TEXT

    def units(text: str) -> int:  # Telegram UTF-16 birliklarida sanaydi
        return len(text.encode("utf-16-le")) // 2

    assert units(branding.DESCRIPTION) <= 512 and units(branding.SHORT_DESCRIPTION) <= 120
    assert units(WELCOME_TEXT.format(name="X" * 64, settings="Y" * 120)) <= 1024
    assert units(LOCKED_TEXT) <= 1024
    for name in ("avatar", "welcome", "locked"):
        path = branding.ASSETS / f"{name}.jpg"
        assert path.read_bytes()[:3] == b"\xff\xd8\xff" and path.stat().st_size < 1_000_000


@pytest.mark.parametrize("name", ["welcome", "locked"])
def test_banner_assets_exist(name):
    assert (branding.ASSETS / f"{name}.jpg").exists()

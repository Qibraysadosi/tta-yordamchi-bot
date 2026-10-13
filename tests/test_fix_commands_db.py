"""commands-db tuzatishlari: Database.open, qidiruv, /api/ask, apostroflar, /shablon ro'yxati, /tarix sanasi."""
import asyncio
import html
import json
import logging
import re
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
from aiogram.exceptions import TelegramBadRequest

import fakes
from bot import webapp
from bot.db import Database
from bot.handlers import PASSWORD_KEY, _when
from test_flow import OWNER, Env, run
from test_webapp import App


def tg_len(text: str, parse_mode: str | None) -> int:
    """Telegram hisobi: HTML teglarsiz, UTF-16 birlikda."""
    if parse_mode == "HTML":
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return len(text.encode("utf-16-le")) // 2


class LimitedSession(fakes.FakeSession):
    """Haqiqiy Telegram kabi 4096 belgidan uzun xabarni rad etadi."""

    async def make_request(self, bot, method, timeout=None):
        if type(method).__name__ in ("SendMessage", "EditMessageText") and tg_len(method.text, method.parse_mode) > 4096:
            raise TelegramBadRequest(method=method, message="Bad Request: message is too long")
        return await super().make_request(bot, method, timeout)


# ----------------------------------------------------------------------------- Database.open


def test_open_closes_connection_when_setup_fails(tmp_path, monkeypatch):
    opened = []
    real_connect = aiosqlite.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    async def broken_migrate(self):
        raise RuntimeError("buzilgan migratsiya")

    monkeypatch.setattr(aiosqlite, "connect", connect)
    monkeypatch.setattr(Database, "_migrate", broken_migrate)

    async def go():
        try:
            with pytest.raises(RuntimeError, match="buzilgan migratsiya"):
                await Database.open(tmp_path / "bot.db")
            assert len(opened) == 1
            # ulanish yopilgan, ishchi oqim tugagan — jarayon chiqib keta oladi
            assert opened[0]._connection is None
            opened[0]._thread.join(timeout=5)
            assert not opened[0]._thread.is_alive()
        finally:
            for conn in opened:
                if conn._connection is not None:
                    await conn.close()
    run(go())


# ----------------------------------------------------------------------------- qidiruv


def test_search_only_text_unicode_case_and_wildcards(tmp_path):
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJDkodishuypulXYZ" * 50}}
    text_doc = {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "Ijara narxi 100_000 so'm"},
                "title": "shartnoma.txt"}

    async def go():
        db = await Database.open(tmp_path / "bot.db")
        try:
            await db.add_turn(1, 1, [{"type": "text", "text": "Salom, qalaysan?"}], "Yaxshi, rahmat")
            await db.add_turn(1, 2, [image, {"type": "text", "text": "Bu rasmda nima bor?"}], "Qizil fon")
            await db.add_turn(1, 3, [{"type": "text", "text": "Привет, Тошкент"}], "Хорошо")
            await db.add_turn(1, 4, [text_doc, {"type": "text", "text": "Tahlil qil"}], "Tayyor")
            await db.add_turn(1, 5, [{"type": "text", "text": "50% chegirma haqida"}], "Mana")
            await db.add_turn(2, 1, [{"type": "text", "text": "Привет begona"}], "Boshqa odam")

            async def ids(query):
                return sorted(c.conv_id for c in await db.search_conversations(1, query))

            # JSON kalitlari va base64 ichida qidirilmaydi
            for query in ("text", "type", "source", "kod", "ish", "uy", "pul", "jpeg", "base64"):
                assert await ids(query) == [], query
            # kirill: katta-kichik harf farqsiz (sarlavha ham, javob ham)
            assert await ids("Привет") == [3]
            assert await ids("привет") == [3]
            assert await ids("ТОШКЕНТ") == [3]
            assert await ids("хорошо") == [3]
            assert await ids("SALOM") == [1]
            # % va _ — oddiy belgi
            assert await ids("_") == [4]
            assert await ids("%") == [5]
            assert await ids("100_000") == [4]
            # hujjat nomi va matnli hujjat ichi topiladi
            assert await ids("shartnoma") == [4]
            assert await ids("ijara") == [4]
            assert await ids("rasmda") == [2]
        finally:
            await db.close()
    run(go())


def test_history_search_command_cyrillic(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Мана, тайёр")])
        try:
            await env.send_text("Шартнома тузиб бер")
            await env.send_text("/tarix Шартнома")
            listing = env.session.of("SendMessage")[-1]
            assert "Hech narsa topilmadi" not in listing.text
            assert [b.callback_data for row in listing.reply_markup.inline_keyboard for b in row] == ["h:o:1"]
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- /api/ask


def test_ask_long_question_is_echoed_short_and_sent_in_full(tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "FakeSession", LimitedSession)

    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Uzun matn tahlili."), fakes.simple_answer("Emoji javob.")])
        env = app.env
        try:
            question = "Bu maqolani qisqartir: " + "a" * 5000
            res = await app.post("/api/ask", {"text": question})
            assert res["ok"] is True
            for _ in range(100):
                if "Uzun matn tahlili" in env.session.last_out() and not env.ctx.lock_for(OWNER).locked():
                    break
                await asyncio.sleep(0.02)
            echo = env.session.of("SendMessage")[0]
            assert echo.text.startswith("📨 <b>Ilovadan savol:</b>\nBu maqolani") and echo.text.endswith("…")
            assert tg_len(echo.text, echo.parse_mode) <= 4096
            # Claude'ga to'liq matn boradi
            assert env.api.requests[0]["messages"][-1]["content"][-1]["text"] == question

            # emoji (UTF-16 da 2 birlik) ham sig'adi
            env.session.clear()
            await app.post("/api/ask", {"text": "😀" * 3000})
            for _ in range(100):
                if "Emoji javob" in env.session.last_out() and not env.ctx.lock_for(OWNER).locked():
                    break
                await asyncio.sleep(0.02)
            echo = env.session.of("SendMessage")[0]
            assert tg_len(echo.text, echo.parse_mode) <= 4096
            assert env.api.requests[1]["messages"][-1]["content"][-1]["text"] == "😀" * 3000

            err = await app.post("/api/ask", {"text": "b" * 8001}, status=400)
            assert "juda uzun" in err["message"] and "8000" in err["message"]
        finally:
            await app.close()
    run(go())


def test_ask_background_error_is_logged(tmp_path, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("fon xatosi")

    monkeypatch.setattr(webapp, "run_chat_for", boom)
    caplog.set_level(logging.ERROR, logger="bot.webapp")

    async def go():
        app = await App().start(tmp_path)
        try:
            res = await app.post("/api/ask", {"text": "Salom"})
            assert res["ok"] is True
            for _ in range(100):
                if any(r.exc_info for r in caplog.records):
                    break
                await asyncio.sleep(0.02)
            records = [r for r in caplog.records if r.name == "bot.webapp" and r.exc_info]
            assert records and isinstance(records[0].exc_info[1], RuntimeError)
            assert "fon xatosi" in caplog.text
            assert not app.web._tasks  # tugagan vazifa ro'yxatdan chiqarilgan
        finally:
            await app.close()
    run(go())


# ----------------------------------------------------------------------------- apostroflar


def test_typographic_apostrophes_in_commands(tmp_path):
    async def go():
        env = await Env().start(tmp_path)

        def sent():
            return env.session.of("SendMessage")[-1].text

        try:
            await env.send_text("/shablon qo’sh Post\nShu mavzuda post yoz")  # iOS: ’
            assert "Shablon saqlandi" in sent()
            await env.send_text("/shablon qoʻsh O'qish reja\nKitob o'qish rejasini tuz")  # o'zbek: ʻ
            assert "Shablon saqlandi" in sent()
            await env.send_text("/shablon qo‘sh Xat\nRasmiy xat yoz")  # ‘
            assert "Shablon saqlandi" in sent()
            assert sorted(t.name for t in await env.db.templates(OWNER)) == ["O'qish reja", "Post", "Xat"]

            await env.send_text("/shablon o’chir post")
            assert "O'chirildi" in sent()
            await env.send_text("/shablon oʻchir O’qish reja")  # nomdagi apostrof ham farqsiz
            assert "O'chirildi" in sent()
            assert [t.name for t in await env.db.templates(OWNER)] == ["Xat"]

            # /parol o’chir paroli "o’chir" bo'lib qolmasin
            await env.send_text("/parol TTA@2026")
            assert await env.db.get_config(PASSWORD_KEY)
            await env.send_text("/parol o’chir")
            assert "Parol o'chirildi" in sent()
            assert await env.db.get_config(PASSWORD_KEY) is None
            await env.send_text("/parol TTA@2026")
            await env.send_text("/parol yoʻq")
            assert "Parol o'chirildi" in sent()
            assert await env.db.get_config(PASSWORD_KEY) is None
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- /shablon ro'yxati


def test_template_listing_fits_telegram_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(fakes, "FakeSession", LimitedSession)

    async def go():
        env = await Env().start(tmp_path)
        try:
            for i in range(40):
                await env.db.add_template(OWNER, f"Shablon {i:02d} ".ljust(40, "x"), "Uzun shablon matni " * 15)
            await env.send_text("/shablon")
            listing = env.session.of("SendMessage")[-1]
            assert tg_len(listing.text, listing.parse_mode) <= 4096
            buttons = [b.callback_data for row in listing.reply_markup.inline_keyboard for b in row]
            assert len(buttons) == 50 and "… va yana 15 ta" in listing.text

            await env.press(buttons[1])  # 🗑 — ro'yxat yangilanadi
            edit = env.last_edit()
            assert tg_len(edit.text, edit.parse_mode) <= 4096 and "… va yana 14 ta" in edit.text
            assert len(await env.db.templates(OWNER)) == 39
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- /tarix sanasi


def test_history_dates_distinguish_june_and_july():
    tz = ZoneInfo("Asia/Tashkent")
    assert _when("2026-06-05T09:00:00", tz) == "5-iyn 14:00"
    assert _when("2026-07-05T09:00:00", tz) == "5-iyl 14:00"
    labels = {_when(f"2026-{m:02d}-10T09:00:00", tz).split()[0].split("-")[1] for m in range(1, 13)}
    assert len(labels) == 12


def test_search_text_ignores_broken_content(tmp_path):
    """Buzilgan yoki g'ayrioddiy JSON qidiruvni yiqitmaydi."""
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        try:
            await db.add_turn(1, 1, [{"type": "text", "text": "Oddiy savol"}], "Javob")
            for content in ("not json", json.dumps("matn"), json.dumps([1, "x", None]), json.dumps({"type": "text"})):
                await db._conn.execute(
                    "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (1, 1, 'user', ?, '2026-10-01T10:00:00')",
                    (content,),
                )
            await db._conn.commit()
            assert [c.conv_id for c in await db.search_conversations(1, "oddiy")] == [1]
            assert await db.search_conversations(1, "json") == []
        finally:
            await db.close()
    run(go())

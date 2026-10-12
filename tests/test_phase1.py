"""1-bosqich: javob tugmalari, rejimlar, avto-model, byudjet, /tarix, shablonlar, havola, lotin-kirill, zaxira."""
import asyncio
import io
import json
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
from aiogram.types import Update
from docx import Document

import fakes
from bot import links, scheduler
from bot.db import Database, conversation_title
from bot.translit import to_cyrillic, to_latin
from test_flow import OWNER, Env, run

GUEST = 111


async def press_answer(env: Env, data: str, message_id: int = 5001, user_id: int = OWNER) -> None:
    env.update_id += 1
    update = Update.model_validate({"update_id": env.update_id, "callback_query": {
        "id": f"cb{env.update_id}", "from": {"id": user_id, "is_bot": False, "first_name": "Shohrux"},
        "chat_instance": "ci", "data": data,
        "message": {"message_id": message_id, "date": 1760240000, "chat": {"id": user_id, "type": "private"},
                    "from": {"id": 999, "is_bot": True, "first_name": "Bot"}, "text": "javob"},
    }}, context={"bot": env.bot})
    await env.dp.feed_update(env.bot, update)


# ----------------------------------------------------------------------------- javob tugmalari


def test_answer_buttons(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [
            fakes.simple_answer("Uzun javob matni **qalin** so'z bilan"),
            fakes.simple_answer("Qisqa variant", model="claude-haiku-5-5"),
            fakes.simple_answer("Короткий вариант", model="claude-haiku-5-5"),
            fakes.simple_answer("Yangi javob"),
        ])
        try:
            await env.send_text("Savol")
            final = env.last_edit()
            answer_id = int(final.reply_markup.inline_keyboard[0][0].callback_data.rsplit(":", 1)[1])
            assert (await env.db.answer(answer_id))[2] == "Uzun javob matni **qalin** so'z bilan"

            # ✂️ Qisqa — arzon model bilan, tarixga yozilmaydi, natija tugmalari «Qayta»siz
            await press_answer(env, f"ans:short:{answer_id}")
            req = env.api.requests[1]
            assert req["model"] == "claude-haiku-5-5" and "qisqartir" in req["messages"][0]["content"][0]["text"]
            assert "Uzun javob matni" in req["messages"][0]["content"][0]["text"] and len(req["messages"]) == 1
            short = env.last_edit()
            assert "Qisqa variant" in short.text and "Haiku 5.5" in short.text
            assert [b.text for b in short.reply_markup.inline_keyboard[0]] == ["✂️ Qisqa", "🌐 Tarjima"]
            assert len(await env.db.history(OWNER, 1, 0)) == 2

            # 🌐 Tarjima — avval til tanlanadi
            await press_answer(env, f"ans:tr:{answer_id}")
            markup = env.session.of("EditMessageReplyMarkup")[-1].reply_markup
            assert [b.callback_data for b in markup.inline_keyboard[0]] == [
                f"ans:tr:{answer_id}:uz", f"ans:tr:{answer_id}:ru", f"ans:tr:{answer_id}:en"]
            await press_answer(env, f"ans:tr:{answer_id}:ru")
            assert "rus tiliga" in env.api.requests[2]["messages"][0]["content"][0]["text"]
            assert "Короткий вариант" in env.last_edit().text

            # 📄 Word
            await press_answer(env, f"ans:word:{answer_id}")
            doc = env.session.of("SendDocument")[-1]
            assert doc.document.filename == f"javob-{answer_id}.docx"
            paragraphs = [p.text for p in Document(io.BytesIO(doc.document.data)).paragraphs if p.text]
            assert paragraphs == ["Uzun javob matni qalin so'z bilan"]

            # 🔡 Кирилл
            await press_answer(env, f"ans:cyr:{answer_id}")
            assert env.session.of("SendMessage")[-1].text == "Узун жавоб матни **қалин** сўз билан"

            # 🔄 Qayta — oxirgi savol qayta yuboriladi, eski javob tarixdan o'chadi
            await press_answer(env, f"ans:re:{answer_id}")
            assert env.api.requests[3]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Savol"}]}]
            history = await env.db.history(OWNER, 1, 0)
            assert [m.role for m in history] == ["user", "assistant"]
            assert history[1].content[0]["text"] == "Yangi javob"
            await press_answer(env, f"ans:re:{answer_id}")  # o'chirilgan javob — eskirgan tugma
            assert "topilmadi" in env.session.of("AnswerCallbackQuery")[-1].text

            # begona odam boshqaning javobiga tugma bosa olmaydi
            new_id = int(env.last_edit().reply_markup.inline_keyboard[0][0].callback_data.rsplit(":", 1)[1])
            await env.db.add_member(GUEST, "Ali", None)
            await press_answer(env, f"ans:word:{new_id}", user_id=GUEST)
            assert "topilmadi" in env.session.of("AnswerCallbackQuery")[-1].text
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- rejimlar


def test_modes(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Xat tayyor"), fakes.simple_answer("Oddiy")])
        try:
            await env.send_text("/rejim")
            menu = env.session.of("SendMessage")[-1]
            keys = [row[0].callback_data for row in menu.reply_markup.inline_keyboard]
            assert keys == ["mode:oddiy", "mode:rasmiy_xat", "mode:instagram", "mode:meta_reklama", "mode:tarjimon", "mode:dasturchi"]
            await env.press("mode:rasmiy_xat")
            assert (await env.ctx.user_settings(OWNER)).mode == "rasmiy_xat"
            await env.send_text("Bandlik markaziga xat")
            system = env.api.requests[0]["system"]
            assert "Rejim: Rasmiy xat" in system and "Sh. K. Yerjanov" in system and "Shohrux" in system
            assert "rejim: 📝 Rasmiy xat" in (await env.ctx.user_settings(OWNER)).mode or True
            await env.send_text("/start")
            assert "rejim: 📝 Rasmiy xat" in env.session.of("SendPhoto")[-1].caption
            await env.send_text("/new")  # rejim saqlanadi
            assert (await env.ctx.user_settings(OWNER)).mode == "rasmiy_xat"
            await env.press("mode:oddiy")
            assert (await env.ctx.user_settings(OWNER)).mode is None
            await env.send_text("Salom")
            assert "Rejim:" not in env.api.requests[1]["system"]
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- avto-model va byudjet


def test_auto_model_and_economy(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [
            fakes.json_answer("simple"), fakes.simple_answer("Salom!", model="claude-haiku-5-5"),
            fakes.json_answer("complex"), fakes.simple_answer("Chuqur tahlil", model="claude-opus-5-5"),
            (500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}),
            fakes.simple_answer("O'rta", model="claude-sonnet-5-5"),
            fakes.simple_answer("Tejamkor", model="claude-haiku-5-5"),
        ])
        try:
            await env.press("m:auto")
            assert (await env.ctx.user_settings(OWNER)).model == "auto"
            await env.send_text("Salom")
            classify, answer = env.api.requests[0], env.api.requests[1]
            assert classify["model"] == "claude-haiku-5-5" and classify["max_tokens"] == 5 and "Salom" in classify["messages"][0]["content"]
            assert answer["model"] == "claude-haiku-5-5"
            assert "✨ Avto → Haiku 5.5" in env.last_edit().text
            _, n = await env.db.spend(OWNER)
            assert n == 2  # tasniflash + javob

            await env.send_text("Shartnoma loyihasini tahlil qil")
            assert env.api.requests[3]["model"] == "claude-opus-5-5" and "✨ Avto → Opus 5.5" in env.last_edit().text

            await env.send_text("Tasniflash xato bersa")  # tasniflash xato → o'rta model
            assert env.api.requests[5]["model"] == "claude-sonnet-5-5"

            # kredit 90% dan oshdi → tejamkor model, egasiga 50/80% ogohlantirishlari
            await env.db.log_usage(OWNER, "claude-opus-5-5", {"input": 1}, 91.0)
            env.session.clear()
            await env.send_text("Yana savol")
            assert env.api.requests[-1]["model"] == "claude-haiku-5-5"
            assert len(env.api.requests) == 7  # tejamkor rejimda tasniflash ham chaqirilmaydi
            final = env.last_edit()
            assert "💡 Tejamkor → Haiku 5.5" in final.text and "Kredit 91% sarflangani" in final.text
            alerts = [m.text for m in env.session.of("SendMessage") if "Kredit sarfi" in m.text]
            assert len(alerts) == 2 and "50%" in alerts[0] and "80%" in alerts[1] and "$91." in alerts[1]
            assert await env.db.alert_sent(env.ctx.now().replace(day=1).strftime("%Y-%m-%d"), 80)
        finally:
            await env.close()
    run(go())


def test_budget_alert_once_per_cycle(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("a"), fakes.simple_answer("b")])
        try:
            await env.db.log_usage(OWNER, "claude-opus-5-5", {"input": 1}, 101.0)
            await env.send_text("1")
            await env.send_text("2")
            alerts = [m.text for m in env.session.of("SendMessage") if "Kredit sarfi" in m.text]
            assert [a.split("%")[0][-3:] for a in alerts] == [" 50", " 80", "100"]
            assert "tugagan bo'lishi mumkin" in alerts[-1]
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- /tarix


def test_history_list_search_and_resume(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Toshkent haqida"), fakes.simple_answer("Kod tayyor"),
                                           fakes.simple_answer("Davomi")])
        try:
            await env.send_text("/tarix")
            assert "Hali suhbatlar yo'q" in env.session.of("SendMessage")[-1].text
            await env.send_text("Toshkent shahri haqida gapirib ber, iltimos batafsil va uzun qilib")
            await env.send_text("/new")
            await env.send_text("Python'da kod yoz")
            await env.send_text("/tarix")
            listing = env.session.of("SendMessage")[-1]
            assert "✅ <b>Python'da kod yoz</b>" in listing.text and "<b>Toshkent shahri haqida gapirib ber, iltimos bat…</b>" in listing.text
            buttons = [b.callback_data for row in listing.reply_markup.inline_keyboard for b in row]
            assert buttons == ["h:o:2", "h:o:1"]

            await env.send_text("/tarix kod")
            assert "«kod» bo'yicha" in env.session.of("SendMessage")[-1].text
            assert [b.callback_data for row in env.session.of("SendMessage")[-1].reply_markup.inline_keyboard for b in row] == ["h:o:2"]

            await env.press("h:o:1")
            assert (await env.ctx.user_settings(OWNER)).conv_id == 1
            assert "Davom etmoqda: <b>Toshkent shahri" in env.last_edit().text
            await env.send_text("Yana davom et")
            assert [m["role"] for m in env.api.requests[2]["messages"]] == ["user", "assistant", "user"]
            assert "Toshkent haqida" in env.api.requests[2]["messages"][1]["content"][0]["text"]
            await env.send_text("/new")
            assert (await env.ctx.user_settings(OWNER)).conv_id == 3
            await env.press("h:o:99")
            assert "topilmadi" in env.session.of("AnswerCallbackQuery")[-1].text
        finally:
            await env.close()
    run(go())


def test_conversation_titles_and_migration(tmp_path):
    assert conversation_title([{"type": "text", "text": "  Salom   dunyo  "}]) == "Salom dunyo"
    assert conversation_title([{"type": "text", "text": "[Iqtibos]\nxxx\n[/Iqtibos]\n\nTarjima qil"}]) == "Tarjima qil"
    assert conversation_title([{"type": "document", "title": "hisobot.pdf"}]) == "📎 hisobot.pdf"
    assert conversation_title([{"type": "image", "source": {}}]) == "🖼 Rasm bilan suhbat"
    assert len(conversation_title([{"type": "text", "text": "a" * 100}])) == 48

    async def go():
        db = await Database.open(tmp_path / "old.db")
        await db._conn.execute("DROP TABLE conversations")
        await db._conn.execute("DELETE FROM sqlite_sequence")
        await db._conn.commit()
        # eski versiyadagi kabi: suhbatlar jadvali yo'q, xabarlar bor
        await db._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (1, 1, 'user', ?, '2026-10-01T10:00:00')",
            (json.dumps([{"type": "text", "text": "Eski savol"}]),))
        await db._conn.execute(
            "INSERT INTO messages (user_id, conv_id, role, content, created_at) VALUES (1, 1, 'assistant', ?, '2026-10-01T10:00:05')",
            (json.dumps([{"type": "text", "text": "Eski javob"}]),))
        await db._conn.execute("ALTER TABLE settings DROP COLUMN mode")
        await db._conn.commit()
        await db.close()

        db = await Database.open(tmp_path / "old.db")  # qayta ochilganda migratsiya
        items, total = await db.conversations(1)
        assert total == 1 and items[0].title == "Eski savol" and items[0].turns == 1
        assert (await db.get_settings(1, "claude-opus-5-5", True)).mode is None
        await db.close()
    run(go())


# ----------------------------------------------------------------------------- shablonlar


def test_templates(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Post tayyor")])
        try:
            await env.send_text("/shablon")
            assert "Qo'shish:" in env.session.of("SendMessage")[-1].text
            await env.send_text("/shablon qo'sh Post\nShu mavzuda Instagram post yoz: AI kurslari")
            assert "Shablon saqlandi" in env.session.of("SendMessage")[-1].text
            await env.send_text("/shablon qo'sh Nomsiz")
            assert "nomi va matni kerak" in env.session.of("SendMessage")[-1].text
            await env.send_text("/shablon")
            listing = env.session.of("SendMessage")[-1]
            buttons = [b.callback_data for row in listing.reply_markup.inline_keyboard for b in row]
            assert len(buttons) == 2 and buttons[0].startswith("tpl:u:") and buttons[1].startswith("tpl:d:")
            await env.press(buttons[0])
            assert env.api.requests[0]["messages"][-1]["content"][0]["text"] == "Shu mavzuda Instagram post yoz: AI kurslari"
            await env.db.add_member(GUEST, "Ali", None)
            await env.press(buttons[0], user_id=GUEST)  # boshqaning shabloni
            assert "topilmadi" in env.session.of("AnswerCallbackQuery")[-1].text
            await env.send_text("/shablon o'chir post")
            assert "O'chirildi" in env.session.of("SendMessage")[-1].text and await env.db.templates(OWNER) == []
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- havola o'qish


def test_link_reading(tmp_path, monkeypatch):
    html = b"<html><head><title>Maqola</title></head><body><article><h1>Sarlavha</h1><p>" + b"Matn. " * 60 + b"</p></article></body></html>"

    async def handler(request):
        if request.url.host == "kun.uz":
            return httpx2.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html)
        return httpx2.Response(404)

    original = links.fetch

    async def fetch(url, max_bytes, client=None):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as c:
            return await original(url, max_bytes, c)

    monkeypatch.setattr(links, "fetch", fetch)

    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Xulosa"), fakes.simple_answer("Javob")])
        try:
            await env.send_text("Shu maqolani qisqacha ayt: https://kun.uz/news/1")
            content = env.api.requests[0]["messages"][0]["content"]
            assert content[0]["type"] == "document" and content[0]["title"] == "Maqola"
            assert "Manba: https://kun.uz/news/1" in content[0]["source"]["data"] and "# Sarlavha" in content[0]["source"]["data"]
            assert content[1] == {"type": "text", "text": "Shu maqolani qisqacha ayt: https://kun.uz/news/1"}
            assert env.session.of("SendChatAction")

            await env.send_text("Bu nima: https://yoq.uz/x")
            warning = [m.text for m in env.session.of("SendMessage") if "⚠️" in m.text][-1]
            assert "404" in warning and "havolasiz javob" in warning
            assert env.api.requests[1]["messages"][-1]["content"][0]["type"] == "text"
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- lotin ↔ kirill


def test_translit_commands_and_rules(tmp_path):
    assert to_cyrillic("O'zbekiston Respublikasi, g'oya va ma'no") == "Ўзбекистон Республикаси, ғоя ва маъно"
    assert to_cyrillic("yo'l, yomon, elektr, aytsa, Konstitutsiya") == "йўл, ёмон, электр, айтса, Конституция"
    assert to_cyrillic("SHOX va `kod` https://kun.uz @bot") == "ШОХ ва `kod` https://kun.uz @bot"
    assert to_latin("Ўзбекистон, йўл, ёмон, электр, сентябрь") == "O'zbekiston, yo'l, yomon, elektr, sentyabr"

    async def go():
        env = await Env().start(tmp_path)
        try:
            await env.send_text("/kirill Salom dunyo")
            assert env.session.of("SendMessage")[-1].text == "Салом дунё"
            await env.send_text("/lotin Салом дунё")
            assert env.session.of("SendMessage")[-1].text == "Salom dunyo"
            await env.send_text("/kirill")
            assert "reply qiling" in env.session.of("SendMessage")[-1].text
            await env.send_text("/kirill", reply_to_message={
                "message_id": 1, "date": 1760240000, "chat": {"id": OWNER, "type": "private"},
                "from": {"id": 999, "is_bot": True, "first_name": "Bot"}, "text": "Javob matni"})
            assert env.session.of("SendMessage")[-1].text == "Жавоб матни"
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- zaxira


def test_backup_command_and_schedule(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        try:
            await env.db.add_turn(OWNER, 1, [{"type": "text", "text": "x"}], "y")
            await env.send_text("/zaxira")
            doc = env.session.of("SendDocument")[-1]
            assert doc.document.filename.startswith("backup-") and doc.document.filename.endswith(".db")
            assert doc.document.data[:16] == b"SQLite format 3\x00" and "Zaxira nusxa (qo'lda)" in doc.caption
            assert not list((tmp_path).glob("backup-*.db"))  # vaqtinchalik fayl o'chirildi
            assert await env.db.get_config("backup:last") == env.ctx.now().strftime("%Y-%m-%d")
            await env.db.add_member(GUEST, "Ali", None)
            await env.send_text("/zaxira", user_id=GUEST)
            assert "faqat bot egasi" in env.session.of("SendMessage")[-1].text

            # o'tkazib yuborilgan kunlik zaxira
            await env.db.set_config("backup:last", "2000-01-01")
            assert await scheduler.missed_backup(env.db, "Asia/Tashkent", "00:00")
            assert not await scheduler.missed_backup(env.db, "Asia/Tashkent", "23:59")
            job = scheduler.DailyJob("t", "03:30", "Asia/Tashkent", lambda: asyncio.sleep(0))
            now = datetime(2026, 10, 12, 4, 0, tzinfo=timezone(timedelta(hours=5)))
            assert job.next_run(now) == datetime(2026, 10, 13, 3, 30, tzinfo=timezone(timedelta(hours=5)))
            assert job.next_run(now.replace(hour=2)) == datetime(2026, 10, 12, 3, 30, tzinfo=timezone(timedelta(hours=5)))
        finally:
            await env.close()
    run(go())


@pytest.mark.parametrize("command,expected", [("/rejim", "Ish rejimi"), ("/tarix", "Suhbatlar tarixi"), ("/shablon", "Shablonlar")])
def test_member_can_use_phase1_commands(tmp_path, command, expected):
    async def go():
        env = await Env().start(tmp_path)
        try:
            await env.db.add_member(GUEST, "Ali", None)
            await env.send_text(command, user_id=GUEST)
            assert expected in env.session.of("SendMessage")[-1].text
        finally:
            await env.close()
    run(go())

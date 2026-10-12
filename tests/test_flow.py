"""To'liq oqim sinovlari: haqiqiy aiogram Dispatcher + soxta Telegram + soxta Anthropic."""
import asyncio
import io
import json
import shutil
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.types import Update
from PIL import Image

import fakes
from bot.app import build_dispatcher
from bot.claude import ClaudeService
from bot.config import Settings
from bot.db import Database
from bot.handlers import AppContext, Debouncer
from bot.live import LiveMessage
from bot.prompt import PromptLoader
from bot.updater import UpdateManager

OWNER = 5591487950
PROJECT = Path(__file__).resolve().parents[1]
PROMPT = PROJECT / "prompts/system.md"


def make_app_dir(target: Path) -> Path:
    """Loyihaning release qismlari nusxasi — yangilanish sinovlari haqiqiy kodga tegmasin."""
    target.mkdir(parents=True, exist_ok=True)
    for item in ("bot", "prompts"):
        shutil.copytree(PROJECT / item, target / item, ignore=shutil.ignore_patterns("__pycache__"))
    for item in ("requirements.txt", "launcher.py", "Dockerfile", "README.md"):
        shutil.copy2(PROJECT / item, target / item)
    (target / "VERSION").write_text("1.1.0\n")
    return target


def make_settings(tmp_path, **over) -> Settings:
    base = dict(
        telegram_token="123456:TESTTOKEN", anthropic_api_key="sk-test", owner_ids=frozenset({OWNER}),
        default_model="claude-opus-5-5", fallback_model="claude-opus-4-8", max_tokens=32000,
        web_search_default=True, web_search_tool="web_search_20250305", web_search_max_uses=5,
        search_city="Tashkent", search_country="UZ", cache_ttl="5m", history_max_messages=40,
        long_context_tokens=80000, image_max_edge=1568, monthly_budget=100.0, budget_reset_day=1,
        timezone="Asia/Tashkent", db_path=tmp_path / "bot.db", system_prompt_path=PROMPT,
        budget_economy_percent=90, economy_model="claude-haiku-5-5",
        auto_models=("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"), quick_model="claude-haiku-5-5",
        link_reading=True, link_max_bytes=8 * 1024 * 1024, backup_time="03:30", modes_dir=PROJECT / "prompts/modes",
        webapp_url=None, webapp_port=8091, files_max_bytes=300 * 1024 * 1024,
    )
    base.update(over)
    return Settings(**base)


def png(size=(1280, 853)):
    buf = io.BytesIO()
    Image.new("RGB", size, (220, 20, 20)).save(buf, "PNG")
    return buf.getvalue()


class Env:
    async def start(self, tmp_path, responses=None, delay=0.0, **over):
        self.settings = make_settings(tmp_path, **over)
        self.db = await Database.open(self.settings.db_path)
        self.api = fakes.FakeAnthropic(responses, delay)
        self.claude = ClaudeService(
            api_key="x", max_tokens=self.settings.max_tokens, web_search_tool=self.settings.web_search_tool,
            web_search_max_uses=5, cache_ttl="5m", client=self.api.client(),
        )
        self.app_dir = make_app_dir(tmp_path / "app")
        self.ctx = AppContext(
            settings=self.settings, db=self.db, claude=self.claude,
            prompt=PromptLoader(self.settings.system_prompt_path, "Asia/Tashkent"),
            updater=UpdateManager(app_dir=self.app_dir, data_dir=tmp_path / "data"),
        )
        self.ctx.restart_delay = 0
        self.ctx.texts = Debouncer(0.01)
        self.ctx.albums = Debouncer(0.05)
        self.session = fakes.FakeSession()
        self.bot = Bot(self.settings.telegram_token, session=self.session)
        self.dp = build_dispatcher(self.ctx)
        self.update_id = 0
        self.message_id = 100
        return self

    async def close(self):
        await self.db.close()

    def _msg(self, user_id, **fields):
        self.message_id += 1
        return {
            "message_id": self.message_id, "date": 1760240000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Shohrux"},
            **fields,
        }

    async def _feed(self, payload):
        self.update_id += 1
        update = Update.model_validate({"update_id": self.update_id, **payload}, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    async def send_text(self, text, user_id=OWNER, **extra):
        await self._feed({"message": self._msg(user_id, text=text, **extra)})

    async def send_photo(self, file_id, data, caption=None, media_group_id=None):
        self.session.files[file_id] = data
        fields = {"photo": [
            {"file_id": file_id + "_s", "file_unique_id": file_id + "s", "width": 90, "height": 60},
            {"file_id": file_id, "file_unique_id": file_id + "b", "width": 1280, "height": 853},
        ]}
        if caption:
            fields["caption"] = caption
        if media_group_id:
            fields["media_group_id"] = media_group_id
        await self._feed({"message": self._msg(OWNER, **fields)})

    async def send_document(self, file_id, data, file_name, mime, caption=None, user_id=OWNER):
        self.session.files[file_id] = data
        fields = {"document": {"file_id": file_id, "file_unique_id": "d" + file_id, "file_name": file_name,
                               "mime_type": mime, "file_size": len(data)}}
        if caption:
            fields["caption"] = caption
        await self._feed({"message": self._msg(user_id, **fields)})

    async def press(self, data, user_id=OWNER):
        await self._feed({"callback_query": {
            "id": str(self.update_id + 1), "from": {"id": user_id, "is_bot": False, "first_name": "Shohrux"},
            "chat_instance": "ci", "data": data,
            "message": {"message_id": 1, "date": 1760240000, "chat": {"id": user_id, "type": "private"}, "text": "menyu"},
        }})

    def last_edit(self):
        return self.session.of("EditMessageText")[-1]


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- testlar


def test_non_owner_rejected(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        await env.send_text("salom", user_id=111)
        sends = env.session.of("SendMessage")
        assert len(sends) == 1 and "Bu shaxsiy bot" in sends[0].text and "111" in sends[0].text
        assert env.api.requests == []
        await env.close()
    run(go())


def test_start_help(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        await env.send_text("/start")
        photo = env.session.of("SendPhoto")[-1]
        assert photo.parse_mode == "HTML" and "Salom, Shohrux!" in photo.caption and "Opus 5.5" in photo.caption
        data = [b.callback_data for row in photo.reply_markup.inline_keyboard for b in row]
        assert data == ["menu:model", "menu:effort", "menu:search", "menu:cost", "menu:mode", "menu:history",
                        "menu:new", "menu:help", "menu:admin"]
        assert env.session.photo_uploads == 1
        await env.send_text("/start")  # ikkinchi marta rasm qayta yuklanmaydi — file_id keshdan
        assert env.session.photo_uploads == 1 and env.session.of("SendPhoto")[-1].photo.startswith("photo-")
        await env.send_text("/yordam")
        msg = env.session.of("SendMessage")[-1]
        assert msg.parse_mode == "HTML" and "/model" in msg.text and "Admin panel" in msg.text
        await env.send_text("/nimadir")
        assert "Bunday buyruq yo'q" in env.session.of("SendMessage")[-1].text
        await env.close()
    run(go())


def test_basic_chat(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("**Salom!** Qanday yordam bera olaman?")])
        await env.send_text("Salom")
        req = env.api.requests[0]
        assert req["model"] == "claude-opus-5-5" and req["stream"] is True and req["max_tokens"] == 32000
        assert req["cache_control"] == {"type": "ephemeral"}
        assert "output_config" not in req and "thinking" not in req and "temperature" not in req
        tool = req["tools"][0]
        assert tool["type"] == "web_search_20250305" and tool["name"] == "web_search" and tool["max_uses"] == 5
        assert tool["user_location"] == {"type": "approximate", "city": "Tashkent", "country": "UZ", "timezone": "Asia/Tashkent"}
        assert "Shohrux" in req["system"] and "{sana}" not in req["system"] and "-yil" in req["system"]
        assert req["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Salom"}]}]

        placeholder = env.session.of("SendMessage")[0]
        assert placeholder.text.startswith("🧠 Opus 5.5") and placeholder.reply_markup is not None
        final = env.last_edit()
        assert final.parse_mode == "HTML"
        buttons = [b.callback_data for row in final.reply_markup.inline_keyboard for b in row]
        assert buttons == ["ans:re:2", "ans:short:2", "ans:tr:2", "ans:word:2", "ans:cyr:2"]
        assert "<b>Salom!</b> Qanday yordam bera olaman?" in final.text
        assert "<i>Opus 5.5 · 💰 $0.001 · bugun $0.00</i>" in final.text
        hist = await env.db.history(OWNER, 1, 0)
        assert [m.role for m in hist] == ["user", "assistant"]
        assert hist[1].content == [{"type": "text", "text": "**Salom!** Qanday yordam bera olaman?"}]
        spent, n = await env.db.spend(OWNER)
        assert n == 1 and abs(spent - 0.0014) < 1e-9
        await env.close()
    run(go())


def test_history_model_effort_search_toggle(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Birinchi javob"),
                                           fakes.simple_answer("Ikkinchi javob", model="claude-fable-5-1")])
        await env.send_text("Birinchi savol")
        await env.press("m:claude-fable-5-1")
        await env.press("e:high")
        await env.send_text("/qidiruv")
        assert "o'chirildi" in env.session.of("SendMessage")[-1].text
        await env.send_text("Ikkinchi savol")
        req = env.api.requests[1]
        assert req["model"] == "claude-fable-5-1" and req["output_config"] == {"effort": "high"}
        assert "web_search" not in [t.get("name") for t in req["tools"]]  # qidiruv o'chiq, fayl vositalari qoladi
        assert [m["role"] for m in req["messages"]] == ["user", "assistant", "user"]
        assert req["messages"][1]["content"] == [{"type": "text", "text": "Birinchi javob"}]
        assert "Fable 5.1 · effort high" in env.last_edit().text
        await env.press("e:default")
        s = await env.ctx.user_settings(OWNER)
        assert s.effort is None and s.model == "claude-fable-5-1" and s.web_search is False
        await env.close()
    run(go())


def test_web_search_pause_turn_and_citations(tmp_path):
    async def go():
        first = (200, fakes.sse([
            fakes.msg_start(), *fakes.text(0, ["Qidirib ko'raman."]), *fakes.search(1, "dollar kursi bugun"),
            fakes.delta("pause_turn", 20, searches=1), fakes.STOP]))
        cite = {"type": "web_search_result_location", "url": "https://kun.uz/x", "title": "Kun.uz maqola",
                "encrypted_index": "ei", "cited_text": "Dollar 12 800 so'm"}
        second = (200, fakes.sse([
            fakes.msg_start(input_tokens=300), *fakes.text(0, ["Bugun dollar ", "12 800 so'm."], citations=[cite]),
            fakes.delta("end_turn", 30), fakes.STOP]))
        env = await Env().start(tmp_path, [first, second])
        await env.send_text("Dollar kursi qancha?")
        r2 = env.api.requests[1]
        last = r2["messages"][-1]
        print("\nDAVOM SO'ROVIDAGI ASSISTANT BLOKLARI:", json.dumps(last["content"], ensure_ascii=False)[:600])
        assert last["role"] == "assistant"
        assert [b["type"] for b in last["content"]] == ["text", "server_tool_use", "web_search_tool_result"]
        final = env.last_edit().text
        assert "Qidirib ko'raman.\n\nBugun dollar 12 800 so'm." in final
        assert "Manbalar" in final and 'href="https://kun.uz/x"' in final and "🔎 1" in final
        hist = await env.db.history(OWNER, 1, 0)
        assert hist[1].content[0]["text"] == "Qidirib ko'raman.\n\nBugun dollar 12 800 so'm."
        spent, n = await env.db.spend(OWNER)
        # 1-qadam: 100*4 + 20*20 = 800 → 0.0008 + 0.01 qidiruv; 2-qadam: 300*4 + 30*20 = 1800 → 0.0018
        assert n == 1 and abs(spent - 0.0126) < 1e-9
        await env.close()
    run(go())


def test_refusal_fallback_sticks_until_new(tmp_path):
    async def go():
        refusal = (200, fakes.sse([
            fakes.msg_start("claude-fable-5-1"), *fakes.text(0, ["Bu haqida"]),
            fakes.delta("refusal", 5, stop_details={"type": "refusal", "category": "cyber", "explanation": None}),
            fakes.STOP]))
        env = await Env().start(tmp_path, [refusal, fakes.simple_answer("Zaxira model javobi", model="claude-opus-4-8"),
                                           fakes.simple_answer("Keyingi javob", model="claude-opus-4-8")])
        await env.ctx.user_settings(OWNER)
        await env.db.update_settings(OWNER, model="claude-fable-5-1", effort="max")
        await env.send_text("Savol")
        r1, r2 = env.api.requests[0], env.api.requests[1]
        assert r1["model"] == "claude-fable-5-1" and r1["output_config"] == {"effort": "max"}
        assert r2["model"] == "claude-opus-4-8" and "output_config" not in r2
        assert r2["messages"] == r1["messages"]
        final = env.last_edit().text
        assert "Zaxira model javobi" in final and "↪️ Fable 5.1 rad etgani uchun javobni Opus 4.8 berdi" in final
        assert "Bu haqida" not in final
        s = await env.ctx.user_settings(OWNER)
        assert s.model_override == "claude-opus-4-8"
        hist = await env.db.history(OWNER, 1, 0)
        assert len(hist) == 2 and hist[1].content[0]["text"] == "Zaxira model javobi"
        assert (await env.db.spend(OWNER))[1] == 2
        await env.send_text("Yana savol")
        r3 = env.api.requests[2]
        assert r3["model"] == "claude-opus-4-8" and "output_config" not in r3
        await env.send_text("/new")
        s = await env.ctx.user_settings(OWNER)
        assert s.model_override is None and s.conv_id == 2 and s.model == "claude-fable-5-1"
        await env.close()
    run(go())


def test_refusal_without_fallback(tmp_path):
    async def go():
        refusal = (200, fakes.sse([
            fakes.msg_start(), fakes.delta("refusal", 0, stop_details={"type": "refusal", "category": "bio",
                                                                       "explanation": "Biologik xavf"}), fakes.STOP]))
        env = await Env().start(tmp_path, [refusal], fallback_model=None)
        await env.send_text("Savol")
        final = env.last_edit().text
        assert final.startswith("🚫 Opus 5.5 bu so'rovga javob bermadi (biologiya mavzusi).") and "Biologik xavf" in final
        assert await env.db.history(OWNER, 1, 0) == []
        await env.close()
    run(go())


def test_photo_without_caption_waits_for_question(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Qizil rang.")])
        await env.send_photo("ph1", png())
        assert "Qabul qilindi (1 ta fayl)" in env.session.of("SendMessage")[-1].text
        assert env.api.requests == []
        await env.send_text("Bu rasmda nima bor?")
        content = env.api.requests[0]["messages"][0]["content"]
        assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
        assert content[1] == {"type": "text", "text": "Bu rasmda nima bor?"}
        hist = await env.db.history(OWNER, 1, 0)
        assert hist[0].content[0]["type"] == "image"
        await env.close()
    run(go())


def test_album_pdf_docx_and_unsupported(tmp_path):
    async def go():
        import docx

        d = docx.Document()
        d.add_paragraph("Shartnoma №5")
        buf = io.BytesIO()
        d.save(buf)
        env = await Env().start(tmp_path, [fakes.simple_answer("Ikki rasm."), fakes.simple_answer("PDF xulosa."),
                                           fakes.simple_answer("Word xulosa.")])
        await asyncio.gather(
            env.send_photo("a1", png(), caption="Solishtir", media_group_id="g1"),
            env.send_photo("a2", png((800, 600)), media_group_id="g1"),
        )
        c0 = env.api.requests[0]["messages"][0]["content"]
        assert [b["type"] for b in c0] == ["image", "image", "text"] and c0[2]["text"] == "Solishtir"

        await env.send_document("pdf1", b"%PDF-1.7\n1 0 obj\n", "hisobot.pdf", "application/pdf", caption="Xulosa")
        c1 = env.api.requests[1]["messages"][-1]["content"]
        assert c1[0]["type"] == "document" and c1[0]["source"]["media_type"] == "application/pdf"
        assert c1[0]["title"] == "hisobot.pdf"

        await env.send_document("w1", buf.getvalue(), "shartnoma.docx",
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                caption="Nima haqida?")
        c2 = env.api.requests[2]["messages"][-1]["content"]
        assert c2[0]["source"] == {"type": "text", "media_type": "text/plain", "data": "Shartnoma №5"}

        await env.send_document("z1", b"PK..", "arxiv.zip", "application/zip", caption="och")
        assert "arxiv.zip" in env.session.of("SendMessage")[-1].text and len(env.api.requests) == 3
        await env.close()
    run(go())


def test_stop_button_cancels(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("kech qolgan javob")], delay=5.0)
        task = asyncio.create_task(env.send_text("Uzoq savol"))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if env.ctx.active.get(OWNER) is not None:
                break
        assert env.ctx.active.get(OWNER) is not None
        await env.press("stop")
        await asyncio.wait_for(task, 3)
        assert "⏹ Javob to'xtatildi." in env.last_edit().text
        assert env.ctx.active == {}
        spent, n = await env.db.spend(OWNER)
        assert n == 1
        assert await env.db.history(OWNER, 1, 0) == []
        await env.press("stop")
        await env.close()
    run(go())


def test_credit_and_search_errors(tmp_path):
    async def go():
        credit = (400, {"type": "error", "error": {"type": "invalid_request_error", "message":
                  "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."}})
        tool_err = (400, {"type": "error", "error": {"type": "invalid_request_error",
                    "message": "tools.0: web_search_20250305 is not supported for this model"}})
        env = await Env().start(tmp_path, [credit, tool_err, fakes.simple_answer("Qidiruvsiz javob")])
        await env.send_text("Salom")
        assert env.last_edit().text.startswith("💳 API kredit tugadi")
        assert await env.db.history(OWNER, 1, 0) == []
        await env.send_text("Yangilik?")
        names = [[t.get("name") for t in r.get("tools", [])] for r in env.api.requests[1:3]]
        assert "web_search" in names[0] and "web_search" not in names[1] and "create_document" in names[1]
        final = env.last_edit().text
        assert "Qidiruvsiz javob" in final and "Internet qidiruvi bu safar ishlamadi" in final
        await env.close()
    run(go())


def test_html_rejected_falls_back_to_plain(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("**Qalin** matn")])
        env.session.fail_html_marker = "<b>"
        await env.send_text("Salom")
        final = env.last_edit()
        assert final.parse_mode is None and final.text.startswith("**Qalin** matn")
        await env.close()
    run(go())


def test_long_answer_is_split(tmp_path):
    async def go():
        long_text = "\n\n".join(f"{i}-band. " + "Uzun javob matni. " * 30 for i in range(20))
        env = await Env().start(tmp_path, [fakes.simple_answer(long_text, output_tokens=3000)])
        await env.send_text("Batafsil yoz")
        edits = [e for e in env.session.of("EditMessageText") if e.parse_mode == "HTML"]
        sends = [s for s in env.session.of("SendMessage") if s.parse_mode == "HTML"]
        assert len(edits) == 1 and len(sends) >= 2
        everything = edits[0].text + "".join(s.text for s in sends)
        assert "0-band." in edits[0].text and "19-band." in everything
        assert "<i>Opus 5.5 · 💰" in sends[-1].text
        assert all(len(m.text) <= 4096 for m in [*edits, *sends])
        await env.close()
    run(go())


def test_cost_report_and_toggle(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("A"), fakes.simple_answer("B")])
        await env.send_text("1")
        await env.send_text("/xarajat")
        report = env.session.of("SendMessage")[-1]
        assert "Xarajat hisoboti" in report.text and "(1 ta so'rov)" in report.text and "Opus 5.5 — $0.00 (1 ta)" in report.text
        assert "/ $100 (0%)" in report.text
        await env.press("c:toggle")
        assert (await env.ctx.user_settings(OWNER)).show_cost is False
        await env.send_text("2")
        assert env.last_edit().text.endswith("<i>Opus 5.5</i>")
        await env.close()
    run(go())


def test_history_trim_and_debounce_and_queue(tmp_path):
    async def go():
        answers = [fakes.simple_answer(f"J{i}") for i in range(4)]
        env = await Env().start(tmp_path, answers, history_max_messages=4)
        await env.send_text("S0")
        await env.send_text("S1")
        await env.send_text("S2")  # tarixda 4 ta xabar → eng eskilari chiqariladi
        roles_texts = [(m["role"], m["content"][0]["text"]) for m in env.api.requests[2]["messages"]]
        assert roles_texts == [("user", "S1"), ("assistant", "J1"), ("user", "S2")]
        assert "eng eski xabarlar kontekstdan chiqarildi" in env.last_edit().text
        s = await env.ctx.user_settings(OWNER)
        assert s.history_start_id > 0
        await env.close()

        # Tez ketma-ket kelgan matnlar bitta so'rovga birlashadi
        env = await Env().start(tmp_path / "b", [fakes.simple_answer("ok")])
        env.ctx.texts = Debouncer(0.2)
        await asyncio.gather(env.send_text("Birinchi qism"), env.send_text("Ikkinchi qism"))
        assert len(env.api.requests) == 1
        assert env.api.requests[0]["messages"][0]["content"][0]["text"] == "Birinchi qism\nIkkinchi qism"
        await env.close()

        # Javob yozilayotganda kelgan xabar navbatga turadi
        env = await Env().start(tmp_path / "c", [fakes.simple_answer("bir"), fakes.simple_answer("ikki")], delay=0.3)
        t1 = asyncio.create_task(env.send_text("birinchi"))
        await asyncio.sleep(0.1)
        await env.send_text("ikkinchi")
        await t1
        assert any("navbatga qo'yildi" in m.text for m in env.session.of("SendMessage"))
        assert len(env.api.requests) == 2
        assert [m["content"][0]["text"] for m in env.api.requests[1]["messages"]] == ["birinchi", "bir", "ikkinchi"]
        await env.close()
    run(go())


def test_live_preview_ticker(tmp_path):
    async def go():
        LiveMessage.INTERVAL = 0.05
        env = await Env().start(tmp_path, [fakes.simple_answer("Tayyor")], delay=0.4)
        try:
            await env.send_text("Salom")
            edits = env.session.of("EditMessageText")
            interim = [e for e in edits if e.parse_mode is None and e.reply_markup is not None]
            assert interim and interim[0].text.startswith("🧠 Opus 5.5 o'ylayapti…")
            # oraliq matn (agar ulgurib chiqsa) kursor bilan ko'rsatiladi — vaqtga bog'liq, shuning uchun majburiy emas
            assert all(e.text.endswith(" ▌") or e.text.startswith("🧠") for e in interim)
            assert edits[-1].parse_mode == "HTML" and "Tayyor" in edits[-1].text
            assert edits[-1].reply_markup.inline_keyboard[0][0].text == "🔄 Qayta"
            assert env.session.of("SendChatAction")
            assert env.session.of("SendMessage")[0].parse_mode is None
        finally:
            LiveMessage.INTERVAL = 1.6
            await env.close()
    run(go())


def test_connect_retries_and_bad_token(monkeypatch):
    from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
    from bot import app
    from bot.config import ConfigError

    async def no_sleep(_):
        return None

    monkeypatch.setattr(app.asyncio, "sleep", no_sleep)

    class Flaky(fakes.FakeSession):
        def __init__(self, failures, error):
            super().__init__()
            self.failures, self.error = failures, error

        async def make_request(self, bot, method, timeout=None):
            if self.failures > 0:
                self.failures -= 1
                raise self.error(method=method, message="boom")
            return await super().make_request(bot, method, timeout)

    async def go():
        me = await app.connect(Bot("1:T", session=Flaky(2, TelegramNetworkError)))
        assert me.username == "claude_test_bot"
        with pytest.raises(ConfigError):
            await app.connect(Bot("1:T", session=Flaky(1, TelegramUnauthorizedError)))
        with pytest.raises(TelegramNetworkError):
            await app.connect(Bot("1:T", session=Flaky(99, TelegramNetworkError)), attempts=3)
    run(go())

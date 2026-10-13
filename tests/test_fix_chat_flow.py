"""Suhbat oqimi tuzatishlari: xarajat yozuvi, oqim xatolari, tasniflash, 🔄, navbat tartibi, kutayotgan fayllar."""
import asyncio
import json
import logging

import httpx2

import fakes
from bot import handlers, links
from bot.claude import ClaudeService
from test_flow import OWNER, Env, png, run
from test_phase1 import press_answer
from test_tools import fakes_docx_mime, make_docx

OVERLOADED = (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})


class StreamFake(fakes.FakeAnthropic):
    """Navbatda funksiya bo'lsa — o'zi javob yasaydi (oqim o'rtasida aloqa uzilishi uchun)."""

    async def handler(self, request):
        if self.responses and callable(self.responses[0]):
            self.requests.append(json.loads(request.content))
            return self.responses.pop(0)()
        return await super().handler(request)


def use_stream_fake(env: Env, responses: list) -> None:
    env.api = StreamFake(responses)
    env.claude.client = env.api.client()


def dropped_stream():
    async def body():
        yield fakes.sse([fakes.msg_start(), *fakes.text(0, ["Salom, "])])
        raise httpx2.RemoteProtocolError("peer closed connection without sending complete message body")
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body())


def pause_with_search():
    """Birinchi qadam: qidiruv + pause_turn (5000 kirish, 200 chiqish, 1 qidiruv ≈ $0.034)."""
    return (200, fakes.sse([
        fakes.msg_start(input_tokens=5000), *fakes.search(0, "dollar kursi"),
        fakes.delta("pause_turn", 200, searches=1), fakes.STOP,
    ]))


def answer_id_of(env: Env) -> int:
    return int(env.last_edit().reply_markup.inline_keyboard[0][0].callback_data.rsplit(":", 1)[1])


def content_types(request: dict) -> list[str]:
    return [b["type"] for b in request["messages"][-1]["content"]]


# ----------------------------------------------------------------------------- xarajat yozuvi


def test_completed_steps_logged_when_later_step_fails(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [pause_with_search(), OVERLOADED])
        try:
            await env.send_text("Dollar kursi qancha?")
            assert "serverlari hozir band" in env.last_edit().text
            cost, rows = await env.db.spend(OWNER)
            assert rows == 1 and abs(cost - 0.034) < 1e-6
        finally:
            await env.close()
    run(go())


def test_search_retry_keeps_first_attempt_cost(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [
            pause_with_search(),
            (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "web_search tool is not supported"}}),
            fakes.simple_answer("Qidiruvsiz javob"),
        ])
        try:
            await env.send_text("Dollar kursi qancha?")
            assert "Qidiruvsiz javob" in env.last_edit().text
            cost, rows = await env.db.spend(OWNER)
            assert rows == 2 and cost > 0.034  # birinchi urinish ham, qayta urinish ham
        finally:
            await env.close()
    run(go())


def test_unexpected_stream_error_logs_completed_cost(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [pause_with_search(), (200, b"event: message_start\ndata: {not json\n\n")])
        try:
            await env.send_text("Dollar kursi qancha?")
            assert "Kutilmagan xato" in env.last_edit().text
            cost, rows = await env.db.spend(OWNER)
            assert rows == 1 and abs(cost - 0.034) < 1e-6
        finally:
            await env.close()
    run(go())


def test_unparsable_translation_is_still_logged(tmp_path):
    async def go():
        source = make_docx(["Shartnoma", "Tomonlar kelishib oldilar."])
        env = await Env().start(tmp_path, [
            fakes.tool_answer("translate_document", {"target_language": "rus"}),
            fakes.json_answer("Kechirasiz, bu hujjatni tarjima qila olmayman.", model="claude-opus-5-5", output_tokens=15000),
            fakes.simple_answer("Tarjima chiqmadi."),
        ])
        try:
            await env.send_document("fd", source, "shartnoma.docx", fakes_docx_mime(), caption="rus tiliga tarjima qil")
            tool_result = env.api.requests[2]["messages"][-1]["content"][0]
            assert tool_result.get("is_error") is True
            cost, rows = await env.db.spend(OWNER)
            assert rows == 2 and cost > 0.3  # suhbat + to'langan (lekin yaroqsiz) tarjima so'rovi
        finally:
            await env.close()
    run(go())


def test_translate_items_returns_cost_on_bad_reply():
    async def go():
        api = fakes.FakeAnthropic([fakes.json_answer("Bu JSON emas", model="claude-opus-5-5", output_tokens=1000)])
        svc = ClaudeService(api_key="x", max_tokens=1000, web_search_tool="web_search_20250305",
                            web_search_max_uses=5, cache_ttl="5m", client=api.client())
        items, numbers, cost = await svc.translate_items("claude-opus-5-5", "rus", None, ["Salom"])
        assert items == [] and numbers["output"] == 1000 and cost > 0
    run(go())


# ----------------------------------------------------------------------------- oqim xatolari


def test_mid_stream_error_event_is_translated(tmp_path):
    async def go():
        def broken(etype, message):
            return (200, fakes.sse([fakes.msg_start(), *fakes.text(0, ["Salom, "]),
                                    {"type": "error", "error": {"type": etype, "message": message}}]))
        env = await Env().start(tmp_path, [broken("overloaded_error", "Overloaded"), broken("rate_limit_error", "Rate limited")])
        try:
            await env.send_text("Salom")
            final = env.last_edit().text
            assert "serverlari hozir band" in final and "(200)" not in final
            await env.send_text("Yana")
            assert "limiti oshdi" in env.last_edit().text
        finally:
            await env.close()
    run(go())


def test_mid_stream_disconnect_is_claude_error(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        use_stream_fake(env, [dropped_stream])
        try:
            await env.send_text("Salom")
            assert "aloqa uzilib qoldi" in env.last_edit().text
        finally:
            await env.close()
    run(go())


def test_quick_task_unexpected_error_is_reported(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Uzun javob"), (200, b"event: message_start\ndata: {not json\n\n")])
        try:
            await env.send_text("Savol")
            await press_answer(env, f"ans:short:{answer_id_of(env)}")
            assert "Kutilmagan xato" in env.last_edit().text
            assert not env.ctx.lock_for(OWNER).locked() and OWNER not in env.ctx.active
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- avto-model tasniflash


def classify_reply(content, stop_reason):
    return (200, {
        "id": "msg_c", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


def test_classifier_leaves_room_and_disables_thinking_on_haiku(caplog):
    async def go():
        api = fakes.FakeAnthropic([
            classify_reply([{"type": "text", "text": "simple"}], "end_turn"),
            classify_reply([{"type": "thinking", "thinking": "", "signature": "x"}], "max_tokens"),
            classify_reply([{"type": "text", "text": "complex"}], "end_turn"),
        ])
        svc = ClaudeService(api_key="x", max_tokens=1000, web_search_tool="web_search_20250305",
                            web_search_max_uses=5, cache_ttl="5m", client=api.client())
        level, _, cost = await svc.classify("claude-haiku-5-5", "Salom", False)
        req = api.requests[0]
        assert level == "simple" and cost > 0
        assert req["max_tokens"] >= 256 and req["output_config"] == {"effort": "low"}
        assert req["thinking"] == {"type": "disabled"}
        with caplog.at_level(logging.WARNING, logger="bot.claude"):
            level, _, _ = await svc.classify("claude-haiku-5-5", "Salom", False)
        assert level == "medium" and "Tasniflash javobsiz" in caplog.text
        # boshqa modellarda `disabled` 400 qaytaradi — yuborilmaydi
        level, _, _ = await svc.classify("claude-sonnet-5-5", "Shartnomani tahlil qil", True)
        assert level == "complex" and "thinking" not in api.requests[2] and api.requests[2]["max_tokens"] >= 256
    run(go())


# ----------------------------------------------------------------------------- 🔄 qayta yozish


def test_failed_regenerate_keeps_turn(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [
            fakes.simple_answer("Birinchi javob"), OVERLOADED, fakes.simple_answer("Ikkinchi javob"),
        ])
        try:
            await env.send_text("Savol")
            answer_id = answer_id_of(env)
            before = [(m.id, m.role) for m in await env.db.history(OWNER, 1, 0)]
            await press_answer(env, f"ans:re:{answer_id}")
            assert "serverlari hozir band" in env.last_edit().text
            assert env.api.requests[1]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Savol"}]}]
            assert [(m.id, m.role) for m in await env.db.history(OWNER, 1, 0)] == before
            assert (await env.db.conversation(OWNER, 1)).turns == 1
            markup = env.session.of("EditMessageReplyMarkup")[-1].reply_markup
            assert markup.inline_keyboard[0][0].callback_data == f"ans:re:{answer_id}"

            await press_answer(env, f"ans:re:{answer_id}")  # endi muvaffaqiyatli
            assert env.api.requests[2]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Savol"}]}]
            history = await env.db.history(OWNER, 1, 0)
            assert [m.role for m in history] == ["user", "assistant"] and history[1].content[0]["text"] == "Ikkinchi javob"
            assert history[1].id != answer_id and (await env.db.conversation(OWNER, 1)).turns == 1
        finally:
            await env.close()
    run(go())


def test_regenerate_of_stale_answer_does_nothing(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("J1"), fakes.simple_answer("J2")])
        try:
            await env.send_text("S1")
            first = answer_id_of(env)
            await env.send_text("S2")
            new_id = await handlers.run_chat_for(
                OWNER, OWNER, 1, env.bot, env.ctx, [{"type": "text", "text": "S1"}], replace=first
            )
            assert new_id is None and len(env.api.requests) == 2
            assert "oxirgisi emas" in env.session.of("SendMessage")[-1].text
            assert len(await env.db.history(OWNER, 1, 0)) == 4
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- navbat tartibi


def test_slow_link_reading_keeps_message_order(tmp_path, monkeypatch):
    async def slow_links(text, max_bytes):
        await asyncio.sleep(0.3)
        return [{"type": "text", "text": "<maqola>kun.uz</maqola>"}], []
    monkeypatch.setattr(links, "read_links", slow_links)

    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("J1"), fakes.simple_answer("J2")])
        try:
            first = asyncio.create_task(env.send_text("Birinchi https://kun.uz/a"))
            await asyncio.sleep(0.1)
            await env.send_text("Ikkinchi")
            await first
            assert "Birinchi" in env.api.requests[0]["messages"][-1]["content"][-1]["text"]
            texts = [m["content"][-1]["text"] for m in env.api.requests[1]["messages"]]
            assert texts[1:] == ["J1", "Ikkinchi"] and "Birinchi" in texts[0]
            assert any("navbatga qo'yildi" in m.text for m in env.session.of("SendMessage"))
        finally:
            await env.close()
    run(go())


def test_slow_photo_download_reaches_next_question(tmp_path, monkeypatch):
    original = handlers._download

    async def slow_download(bot, file_id):
        await asyncio.sleep(0.3)
        return await original(bot, file_id)
    monkeypatch.setattr(handlers, "_download", slow_download)

    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Qizil rasm")])
        try:
            photo = asyncio.create_task(env.send_photo("p1", png()))
            await asyncio.sleep(0.1)
            await env.send_text("Rasmda nima bor?")
            await photo
            assert content_types(env.api.requests[0]) == ["image", "text"]
            assert env.ctx.take_pending(OWNER) == []
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- kutayotgan fayllar


def test_pending_files_restored_after_transient_error(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [OVERLOADED, fakes.simple_answer("Qizil rasm")])
        try:
            await env.send_photo("p1", png())
            await env.send_text("Rasmda nima bor?")
            assert "saqlab qolindi" in env.last_edit().text
            await env.send_text("Rasmda nima bor?")
            assert content_types(env.api.requests[1]) == ["image", "text"]
            assert "Qizil rasm" in env.last_edit().text
        finally:
            await env.close()
        # doimiy xato (400) — fayl navbatda qolib ketmaydi
        env = await Env().start(tmp_path / "b", [
            (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "Could not process image"}}),
        ])
        try:
            await env.send_photo("p1", png())
            await env.send_text("Rasmda nima bor?")
            assert "saqlab qolindi" not in env.last_edit().text and env.ctx.take_pending(OWNER) == []
        finally:
            await env.close()
    run(go())


def test_template_restores_pending_files(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [OVERLOADED])
        try:
            template = await env.db.add_template(OWNER, "Tahlil", "Rasmni tahlil qil")
            await env.send_photo("p1", png())
            await env.press(f"tpl:u:{template.id}")
            assert content_types(env.api.requests[0]) == ["image", "text"]
            assert "saqlab qolindi" in env.last_edit().text
            assert [b["type"] for b in env.ctx.take_pending(OWNER)] == ["image"]
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- yakuniy review tuzatishlari


def test_miniapp_ask_restores_pending_files(tmp_path):
    """Ilovadan savol ham kutayotgan fayllarni qulf ichida oladi va vaqtinchalik xatoda qaytaradi."""
    from test_webapp import App

    async def go():
        app = await App().start(tmp_path, [OVERLOADED])
        env = app.env
        try:
            await env.send_photo("p1", png())
            await app.post("/api/ask", {"text": "Rasmda nima bor?"})
            await asyncio.wait_for(asyncio.gather(*app.web._tasks), timeout=10)
            assert content_types(env.api.requests[0]) == ["image", "text"]
            assert "saqlab qolindi" in env.last_edit().text
            assert [b["type"] for b in env.ctx.take_pending(OWNER)] == ["image"]
        finally:
            await app.close()
    run(go())


def test_queue_notice_names_the_real_reason(tmp_path, monkeypatch):
    """Fayl yuklanayotganda «javob yozilmoqda» deyilmaydi; izohsiz keyingi fayl jim kutadi."""
    original = handlers._download

    async def slow_download(bot, file_id):
        await asyncio.sleep(0.2)
        return await original(bot, file_id)
    monkeypatch.setattr(handlers, "_download", slow_download)

    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Ikkala rasm qizil")])
        try:
            first = asyncio.create_task(env.send_photo("p1", png()))
            await asyncio.sleep(0.05)
            await env.send_photo("p2", png())  # izohsiz — xabarsiz kutadi
            await first
            texts = [m.text for m in env.session.of("SendMessage")]
            assert not any(t.startswith("⏳") for t in texts)
            assert [t for t in texts if t.startswith("📎 Qabul qilindi")] == [
                "📎 Qabul qilindi (1 ta fayl). Endi savolingizni yozing — masalan: «qisqacha mazmunini ayt».",
                "📎 Qabul qilindi (2 ta fayl). Endi savolingizni yozing — masalan: «qisqacha mazmunini ayt».",
            ]
            # fayl yuklanayotganda savol yozilsa — fayl haqida aytiladi, «javob yozilmoqda» emas
            third = asyncio.create_task(env.send_photo("p3", png()))
            await asyncio.sleep(0.05)
            await env.send_text("Rasmlarda nima bor?")
            await third
            notices = [m.text for m in env.session.of("SendMessage") if m.text.startswith("⏳")]
            assert notices == ["⏳ Oldingi fayl hali yuklanmoqda — bu xabar undan keyin ko'riladi."]
            assert content_types(env.api.requests[0]) == ["image", "image", "image", "text"]
            assert env.ctx.busy == {}
        finally:
            await env.close()
    run(go())


def test_restored_files_stay_in_one_batch_with_later_files(tmp_path):
    """Vaqtinchalik xatodan keyin qaytgan rasm va keyin yuborilgan rasm bitta to'plam: PDF ikkalasidan."""
    from bot import tools

    async def go():
        env = await Env().start(tmp_path, [OVERLOADED, fakes.simple_answer("Tayyor")])
        try:
            await env.send_photo("p1", png())
            await env.send_text("PDF qil")
            assert "saqlab qolindi" in env.last_edit().text
            await env.send_photo("p2", png(), caption="Endi ikkalasini PDF qil")
            files = env.ctx.raw.recent(OWNER)
            assert len(files) == 2
            assert len(tools.pick(files, None, ("image",))) == 2
            # muvaffaqiyatli navbatdan keyin yangi rasm — yangi to'plam
            await env.send_photo("p3", png())
            assert [f.batch for f in env.ctx.raw.recent(OWNER)] == [0, 0, 1]
        finally:
            await env.close()
    run(go())

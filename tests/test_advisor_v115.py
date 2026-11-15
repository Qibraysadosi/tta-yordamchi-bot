"""v1.15: kunlik tavsiya — kuchli model (Fable 5.2) kun natijalari, lidlar, yozishmalar, vaucher va vazifalarni ko'rib,
egasiga nimani yaxshilashni yozadi: jadval (20:30, ish kunlari), /tavsiya, «🔄 Qayta tahlil», zaxira model, Mini App
(Arxiv → Tavsiya, Sozlamalar) va AI suhbati (javob — iqtibos, bot_status)."""
from datetime import date

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import Message

import fakes
from bot.config import ConfigError, load_settings
from bot.handlers import _quoted
from bot.models import MODELS, SELECTABLE_MODELS, cost_usd, label
from bot.reports.advisor import ADVICE_NAME, ADVICE_UID, MAX_CHARS, AdvisorService, plain_text, split_staff
from bot.reports.stats import Schedule
from test_ai_tools_v113 import ask, owner_tools, say
from test_archive import MON, NEXT_MON, SAT, THU, queue_analysis, seed_week
from test_assistant import LEADER
from test_fix_miniapp import chromium, go_hash, settled
from test_flow import OWNER, run
from test_reports import BOT_ID, LEADERS, MALIKA, WED, AdminSession, local, person, start, ts
from test_reports_api import Api, init_data

FRI = date(2026, 10, 23)
SUN = date(2026, 10, 18)
MEMBER = 777
ANSWER = """**🎯 Asosiy xulosa**
Bugun 2 ta lidga qo'ng'iroq qilindi, asosiy natija yo'q.

**📞 Call center**
• Malika — lidni 15 daqiqada oldi.

=== OPERATORLARGA ===
Assalomu alaykum, hamkasblar! Ertaga «ko'tarmadi» bo'lganlarga qayta qo'ng'iroq qilaylik."""


def advice_answer(text=ANSWER, model="claude-fable-5-2"):
    return fakes.simple_answer(text, model=model, input_tokens=20000, output_tokens=3000)  # $0.16 + $0.12


def api_error(status, kind, message):
    return (status, {"type": "error", "error": {"type": kind, "message": message}})


async def seeded(tmp_path, **over):
    """19–24 oktabr haftasi (lidlar, izoh, vazifalar; izohlar tahlil qilingan) + rahbarlar guruhida telefonli xabar."""
    env = await start(tmp_path, **over)
    await seed_week(env)
    await queue_analysis(env)
    await env.ctx.reports.analyze_pending()
    await say(env, LEADERS, person(MALIKA, "Malika", "malika_op"), "Bugun 3 kishi keldi, +998 90 111 22 33 ertaga keladi",
              ts(WED, "16:00"))
    return env


def buttons(message) -> list[str]:
    return [b.callback_data for row in message.reply_markup.inline_keyboard for b in row]


class FlakySession(AdminSession):
    """down=True — Telegram'ga xabar ketmaydi (tarmoq xatosi)."""

    down = False

    async def make_request(self, bot, method, timeout=None):
        if self.down and type(method).__name__ == "SendMessage":
            self.calls.append(method)
            raise TelegramNetworkError(method=method, message="Bad Gateway")
        return await super().make_request(bot, method, timeout)


# --------------------------------------------------------------------------- model va sozlamalar
def test_fable_52_model_and_settings(monkeypatch):
    assert SELECTABLE_MODELS[0] == "claude-fable-5-2" and label("claude-fable-5-2") == "Fable 5.2"
    r = MODELS["claude-fable-5-2"].rates
    assert (r.input, r.output, r.cache_write_5m, r.cache_write_1h, r.cache_read) == (8, 40, 10, 16, 0.20)
    assert cost_usd("claude-fable-5-2", {"input": 20000, "output": 3000}) == pytest.approx(0.28)
    for name in ("ADVICE_MODEL", "ADVICE_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("OWNER_IDS", "5591487950")
    s = load_settings()
    assert (s.advice_model, s.advice_effort) == ("claude-fable-5-2", "high")
    monkeypatch.setenv("ADVICE_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("ADVICE_EFFORT", "Medium")
    assert (load_settings().advice_model, load_settings().advice_effort) == ("claude-opus-5-5", "medium")
    monkeypatch.setenv("ADVICE_EFFORT", "max")  # bitta tahlil uchun ortiqcha qimmat
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("ADVICE_EFFORT", "high")
    monkeypatch.setenv("ADVICE_MODEL", "gpt-5")
    with pytest.raises(ConfigError):
        load_settings()


def test_split_staff_and_plain_text():
    body, staff = split_staff(ANSWER)
    assert body.endswith("15 daqiqada oldi.") and staff.startswith("Assalomu alaykum") and "===" not in body + staff
    assert split_staff("Faqat tavsiya") == ("Faqat tavsiya", "")
    assert split_staff("A\n== operatorlarga ==\nB") == ("A", "B")
    assert plain_text("**🎯 Xulosa**\n• `kod` va __so'z__") == "🎯 Xulosa\n• kod va so'z"


# --------------------------------------------------------------------------- ma'lumot
def test_digest_sections_without_phones(tmp_path):
    async def go():
        env = await seeded(tmp_path)
        try:
            advisor = env.ctx.reports.advisor
            data = await advisor.digest(WED, local(WED, "20:30"))
            assert {"meta", "day", "day_response", "leads", "two_weeks", "month", "chats", "tasks", "staff", "groups",
                    "schedule"} <= set(data)
            assert "vouchers" not in data and "previous_advice" not in data  # vaucher guruhi va oldingi tavsiya yo'q
            assert data["meta"]["report_day"] == "2026-10-21" and data["meta"]["interim"] is False
            assert data["day"]["totals"]["calls"] == 2 and data["leads"]["total"] == 2
            assert data["day_response"]["totals"]["first_call_min_all"] == 15
            assert len(data["two_weeks"]["days"]) == 12  # 14 kun, 2 ta yakshanbasiz
            assert data["month"]["totals"]["new_leads"] == 3
            assert {"by_course", "by_district", "by_age", "by_hour", "by_weekday", "by_operator"} <= set(data["month"])
            assert "Bugun 3 kishi keldi, [raqam] ertaga keladi" in [m["text"] for m in data["chats"]["messages"]]
            assert data["staff"]["operators"] == ["Malika", "Sardor"] and data["tasks"]["counts"]["open"] == 2
            packed = advisor.pack(data)
            for digits in ("998", "111 22 33", "888 77 66", "555 66 77", "123-45-67"):
                assert digits not in packed  # telefon raqamlari modelga ketmaydi

            # oldingi tavsiyalar — keyingi kunga (takrorlamasin), oraliqlari emas
            await advisor.store.save("2026-10-20", text="Kechagi tavsiya", staff_text="", model="claude-fable-5-2",
                                     cost=0.3, interim=False, note="")
            await advisor.store.save("2026-10-19", text="Oraliq", staff_text="", model="claude-fable-5-2", cost=0.3,
                                     interim=True, note="")
            data = await advisor.digest(WED, local(WED, "20:30"))
            assert data["previous_advice"] == [{"report_day": "2026-10-20", "advice": "Kechagi tavsiya"}]
        finally:
            await env.close()
    run(go())


def test_pack_trims_long_chats():
    data = {"meta": {}, "chats": {"messages": [{"text": "x" * 300}] * 800, "shown": 800},
            "leads": {"items": [{"name": "y" * 50}] * 5, "shown": 5}}
    text = AdvisorService.pack(data)
    assert len(text) <= MAX_CHARS and data["chats"]["shown"] < 800 and data["leads"]["shown"] == 5


# --------------------------------------------------------------------------- jadval
def test_due_day_rules(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            advisor = env.ctx.reports.advisor
            s = Schedule()  # hisobot 19:00, tavsiya 20:30, dushanba–shanba
            assert (s.advice_enabled, s.advice_time) == (True, "20:30")
            assert await advisor.due_day(local(WED, "20:29"), s) is None
            assert await advisor.due_day(local(WED, "20:30"), s) == WED
            assert await advisor.due_day(local(THU, "00:29"), s) == WED  # bot o'chiq edi — 4 soatgacha kechikib
            assert await advisor.due_day(local(THU, "00:31"), s) is None
            assert await advisor.due_day(local(date(2026, 10, 25), "20:30"), s) is None  # yakshanba
            assert await advisor.due_day(local(NEXT_MON, "20:30"), s) == NEXT_MON  # shanba 19:00 → dushanba 19:00
            morning = s.updated({"advice_time": "9:00"})
            assert morning.advice_time == "09:00"
            assert await advisor.due_day(local(THU, "09:00"), morning) == WED  # ertalab — oldingi hisobot kuni
            assert await advisor.due_day(local(NEXT_MON, "09:00"), morning) == SAT
            assert "oldingi hisobot kuni" in advisor.describe(morning) and "o'sha kunning" in advisor.describe(s)
            assert s.updated({"advice_time": "25:00"}).advice_time == "20:30"
            assert s.updated({"advice_enabled": False}).as_dict()["advice_enabled"] is False
            # yuborilgani — boshqa urinilmaydi; oraliq (kun tugamasdan) — yakuniysiga xalaqit bermaydi
            await advisor.store.save("2026-10-21", text="Oraliq", staff_text="", model="claude-fable-5-2", cost=0.3,
                                     interim=True, note="")
            await advisor.store.mark_sent("2026-10-21")
            assert await advisor.due_day(local(WED, "20:30"), s) == WED
            await advisor.store.save("2026-10-21", text="Yakuniy", staff_text="", model="claude-fable-5-2", cost=0.3,
                                     interim=False, note="")
            assert await advisor.due_day(local(WED, "20:30"), s) == WED  # saqlangan, lekin yetkazilmagan
            await advisor.store.mark_sent("2026-10-21")
            assert await advisor.due_day(local(WED, "20:30"), s) is None
            # lidlar guruhi yo'q — tavsiya ham yo'q
            assert not await advisor.check(env.bot, local(THU, "20:30"))
        finally:
            await env.close()
    run(go())


def test_scheduled_advice_once_per_working_day(tmp_path):
    async def go():
        env = await seeded(tmp_path)
        try:
            reports = env.ctx.reports
            advisor = reports.advisor
            assert not await advisor.check(env.bot, local(WED, "20:29"))
            env.session.clear()
            requests = len(env.api.requests)
            env.api.responses.append(advice_answer())
            assert await advisor.check(env.bot, local(WED, "20:30"))
            assert not await advisor.check(env.bot, local(WED, "20:30"))  # tahlil ketyapti — ikkinchisi boshlanmaydi
            await advisor._task
            assert len(env.api.requests) == requests + 1
            req = env.api.requests[-1]
            assert req["model"] == "claude-fable-5-2" and req["output_config"] == {"effort": "high"}
            assert "cache_control" not in req and "thinking" not in req and "tools" not in req  # bir martalik so'rov
            assert "OPERATORLARGA" in req["system"] and "Reklama xarajati" in req["system"]
            content = req["messages"][0]["content"]
            assert "Hisobot kuni: 21.10.2026 (chorshanba)" in content and '"report_day":"2026-10-21"' in content
            assert "998" not in content

            sends = env.session.of("SendMessage")
            assert [m.chat_id for m in sends] == [OWNER, OWNER]  # faqat egasiga: tavsiya + operatorlarga xabar
            main, staff = sends
            assert main.text.startswith("<b>💡 Kunlik tavsiya · 21.10.2026 (chorshanba)</b>")
            assert "<b>🎯 Asosiy xulosa</b>" in main.text and "operatorlar uchun" in main.text
            assert "Fable 5.2 · 💰 $0.28" in main.text and "OPERATORLARGA" not in main.text
            assert main.reply_markup is None  # jadvaldagisida tugma yo'q (/tavsiya — «Qayta tahlil» bilan)
            assert staff.text.startswith("Assalomu alaykum, hamkasblar!")
            saved = await advisor.store.get("2026-10-21")
            assert saved.sent_at and not saved.interim and saved.model == "claude-fable-5-2"
            assert saved.cost == pytest.approx(0.28) and saved.staff_text.startswith("Assalomu")
            by_user = {uid: cost for uid, cost, _ in await env.db.spend_by_user(local(MON, "00:00"))}
            assert by_user[ADVICE_UID] == pytest.approx(0.28)
            assert (await reports.usage_names())[ADVICE_UID] == ADVICE_NAME

            # bir kunga — bitta; yakshanba yo'q; o'chirilsa — yo'q
            assert not await advisor.check(env.bot, local(WED, "20:35"))
            assert not await advisor.check(env.bot, local(THU, "00:20"))
            await reports.save_schedule({"advice_enabled": False})
            assert not await advisor.check(env.bot, local(THU, "20:30"))
            # vaqt o'zgardi: ertalab 09:00 — oldingi hisobot kuni bo'yicha
            await reports.save_schedule({"advice_enabled": True, "advice_time": "09:00"})
            assert not await advisor.check(env.bot, local(THU, "09:00"))  # chorshanba — allaqachon yuborilgan
            env.api.responses.append(advice_answer("**🎯 Asosiy xulosa**\nJuma bo'yicha."))
            assert await advisor.check(env.bot, local(SAT, "12:50"))
            await advisor._task
            text = env.session.of("SendMessage")[-1].text
            assert "23.10.2026 (juma)" in text and "Juma bo'yicha." in text
            assert "Hisobot kuni: 23.10.2026" in env.api.requests[-1]["messages"][0]["content"]
        finally:
            await env.close()
    run(go())


def test_retry_fallback_and_warning(tmp_path):
    async def go():
        env = await seeded(tmp_path)
        try:
            advisor = env.ctx.reports.advisor
            env.session.clear()
            # 1) server band — jim, 10 daqiqadan keyin qayta
            env.api.responses.append(api_error(529, "overloaded_error", "Overloaded"))
            assert await advisor.check(env.bot, local(WED, "20:30"))
            await advisor._task
            assert env.session.of("SendMessage") == [] and await advisor.store.get("2026-10-21") is None
            assert not await advisor.check(env.bot, local(WED, "20:35"))
            # 2) Fable 5.2 ga ruxsat yo'q — zaxira (botning asosiy modeli Opus 5.5), odatiy effort bilan
            env.api.responses += [api_error(403, "permission_error", "no access"), advice_answer(model="claude-opus-5-5")]
            assert await advisor.check(env.bot, local(WED, "20:40"))
            await advisor._task
            first, second = env.api.requests[-2:]
            assert first["model"] == "claude-fable-5-2" and second["model"] == "claude-opus-5-5"
            assert "output_config" not in second
            text = env.session.of("SendMessage")[-2].text
            assert "⚠️ Fable 5.2 ishlamadi (🚫 Bu API kalit bilan ushbu modelga ruxsat yo'q) — tavsiyani Opus 5.5 tayyorladi" in text
            assert "/model" not in text and "Opus 5.5 · 💰 $0.14" in text
            assert (await advisor.store.get("2026-10-21")).model == "claude-opus-5-5"

            # 3) payshanba: model rad etdi, zaxira ham ishlamadi — egasiga bir marta xabar, shu kun boshqa urinilmaydi
            refusal = (200, fakes.sse([
                fakes.msg_start("claude-fable-5-2"), *fakes.text(0, ["Kechirasiz"]),
                fakes.delta("refusal", 5, stop_details={"type": "refusal", "category": "cyber", "explanation": None}),
                fakes.STOP]))
            env.api.responses += [refusal, api_error(404, "not_found_error", "model: claude-opus-5-5")]
            assert await advisor.check(env.bot, local(THU, "20:30"))
            await advisor._task
            warning = env.session.of("SendMessage")[-1]
            assert warning.chat_id == OWNER and warning.text == (
                "⚠️ Kunlik tavsiya (22.10.2026) tayyorlanmadi: ❓ Model topilmadi\nQayta urinish: /tavsiya")
            assert not await advisor.check(env.bot, local(THU, "20:45"))
            assert await advisor.store.get("2026-10-22") is None
        finally:
            await env.close()
    run(go())


def test_economy_mode_uses_economy_model(tmp_path):
    async def go():
        env = await seeded(tmp_path)
        try:
            await env.db.log_usage(OWNER, "claude-opus-5-5", {"input": 10}, 95.0)  # kredit 95% — tejamkor rejim
            env.api.responses.append(advice_answer(model="claude-haiku-5-5"))
            advisor = env.ctx.reports.advisor
            assert await advisor.check(env.bot, local(WED, "20:30"))
            await advisor._task
            req = env.api.requests[-1]
            assert req["model"] == "claude-haiku-5-5" and "output_config" not in req
            advice = next(m.text for m in env.session.of("SendMessage") if "Kunlik tavsiya" in m.text)
            assert "tejamkor model" in advice and "Haiku 5.5 · 💰" in advice
        finally:
            await env.close()
    run(go())


def test_undelivered_advice_is_resent_without_new_analysis(tmp_path):
    async def go():
        env = await seeded(tmp_path)
        try:
            env.session = FlakySession()
            env.bot.session = env.session
            env.session.down = True
            advisor = env.ctx.reports.advisor
            env.api.responses.append(advice_answer())
            assert await advisor.check(env.bot, local(WED, "20:30"))
            await advisor._task
            assert (await advisor.store.get("2026-10-21")).sent_at is None
            requests = len(env.api.requests)
            env.session.down = False
            assert not await advisor.check(env.bot, local(WED, "20:35"))
            assert await advisor.check(env.bot, local(WED, "20:40"))
            await advisor._task
            assert len(env.api.requests) == requests  # qayta tahlil qilinmadi — saqlangani yuborildi
            assert (await advisor.store.get("2026-10-21")).sent_at
            assert "Kunlik tavsiya · 21.10.2026" in env.session.of("SendMessage")[-2].text
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- /tavsiya va tugma
def test_advice_command_and_regenerate(tmp_path):
    async def go():
        env = await seeded(tmp_path)
        try:
            reports = env.ctx.reports
            advisor = reports.advisor
            await env.db.add_member(MEMBER, "Ali", "ali")
            env.session.clear()
            # kunduzi (15:00): so'nggi tugagan hisobot kuni — seshanba
            env.api.responses.append(advice_answer())
            await env.send_text("/tavsiya")
            status, main, staff = env.session.of("SendMessage")
            assert status.text.startswith("🧠 Fable 5.2 kun ma'lumotlarini tahlil qilmoqda")
            assert env.session.of("DeleteMessage")[-1].message_id == env.session.next_id - 2  # holat xabari o'chdi
            assert "20.10.2026 (seshanba)" in main.text and buttons(main) == ["adv:re:2026-10-20"]
            assert staff.text.startswith("Assalomu alaykum")
            requests = len(env.api.requests)
            await env.send_text("/advice")  # saqlangani — bepul
            assert len(env.api.requests) == requests and "20.10.2026" in env.session.of("SendMessage")[-2].text

            # «bugun» — kun tugamagan: oraliq; ketma-ket bosilsa qayta tahlil qilinmaydi
            env.api.responses.append(advice_answer("**🎯 Asosiy xulosa**\nHozircha 2 ta qo'ng'iroq."))
            await env.send_text("/tavsiya bugun")
            text = env.session.of("SendMessage")[-1].text
            assert "21.10.2026 (chorshanba)" in text and "hozirgi holat — kun hali tugamagan" in text
            assert "kun hali tugamagan" in env.api.requests[-1]["messages"][0]["content"]
            assert (await advisor.store.get("2026-10-21")).interim
            await env.send_text("/tavsiya hozir")
            assert len(env.api.requests) == requests + 1

            # tavsiyaga javob (reply) — AI suhbatiga iqtibos bo'lib boradi
            env.api.responses.append(fakes.simple_answer("Reklamani Chilonzorga qarating."))
            await env.send_text("Reklamani qanday kuchaytiraman?", reply_to_message={
                "message_id": 77, "date": 1760240000, "chat": {"id": OWNER, "type": "private"},
                "from": {"id": BOT_ID, "is_bot": True, "first_name": "TTA Yordamchi"},
                "text": "💡 Kunlik tavsiya · 21.10.2026 (chorshanba)\n\n🎯 Asosiy xulosa\nHozircha 2 ta qo'ng'iroq."})
            asked = env.api.requests[-1]["messages"][-1]["content"][0]["text"]
            assert asked.startswith("[Iqtibos]\n💡 Kunlik tavsiya · 21.10.2026") and asked.endswith("qanday kuchaytiraman?")

            # «🔄 Qayta tahlil qilish»
            env.api.responses.append(advice_answer("**🎯 Asosiy xulosa**\nYangi tahlil."))
            await env.press("adv:re:2026-10-20")
            assert "Tahlil boshlandi" in env.session.of("AnswerCallbackQuery")[-1].text
            assert "Yangi tahlil." in env.session.of("SendMessage")[-1].text
            saved = await advisor.store.get("2026-10-20")
            assert saved.text.endswith("Yangi tahlil.") and saved.sent_at
            requests = len(env.api.requests)
            for data in ("adv:re:2026-10-18", "adv:re:2026-09-01", "adv:re:2026-10-23", "adv:re:x"):
                await env.press(data)  # yakshanba, juda eski, kelajak, buzuq
            assert len(env.api.requests) == requests
            assert sum("qayta tahlil qilib bo'lmaydi" in (a.text or "") for a in env.session.of("AnswerCallbackQuery")) == 3
            await env.press("adv:re:2026-10-20", user_id=MEMBER)
            assert "Faqat bot egasi" in env.session.of("AnswerCallbackQuery")[-1].text

            # jadval: vaqt, o'chirish, yoqish, yordam; boshqa odam
            await env.send_text("/tavsiya 21:15")
            assert "21:15" in env.session.of("SendMessage")[-1].text
            s = await reports.schedule()
            assert (s.advice_time, s.advice_enabled) == ("21:15", True)
            await env.send_text("/tavsiya o'chir")
            assert (await reports.schedule()).advice_enabled is False
            await env.send_text("/maslahat yoq")
            assert (await reports.schedule()).advice_enabled is True
            await env.send_text("/tavsiya qalaysan")
            help_text = env.session.of("SendMessage")[-1].text
            assert "/tavsiya bugun" in help_text and "21:15" in help_text
            await env.send_text("/tavsiya", user_id=MEMBER)
            assert env.session.of("SendMessage")[-1].text == "Bu buyruq faqat bot egasi uchun."
        finally:
            await env.close()
    run(go())


def test_advice_command_needs_leads_group(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await env.send_text("/tavsiya")
            assert "guruh" in env.session.of("SendMessage")[-1].text.lower() and env.api.requests == []
        finally:
            await env.close()
    run(go())


def test_reply_quotes_reports_but_not_chat_answers():
    def reply_to(sender, text):
        return Message.model_validate({
            "message_id": 2, "date": 1760240000, "chat": {"id": OWNER, "type": "private"},
            "from": {"id": OWNER, "is_bot": False, "first_name": "Shohrux"}, "text": "Nega?",
            "reply_to_message": {"message_id": 1, "date": 1760240000, "chat": {"id": OWNER, "type": "private"},
                                 "from": {"id": sender, "is_bot": sender == BOT_ID, "first_name": "X"}, "text": text}})

    for text in ("💡 Kunlik tavsiya · 21.10.2026", "📊 KUNLIK HISOBOT · 21.10.2026", "📊 HISOBOT (oraliq) · 21.10",
                 "📓 KUNDALIK · 21.10.2026"):
        assert _quoted(reply_to(BOT_ID, text), BOT_ID) == text  # suhbat tarixida yo'q — iqtibos qilinadi
    assert _quoted(reply_to(BOT_ID, "Oddiy javob"), BOT_ID) is None  # bot javobi tarixda bor
    assert _quoted(reply_to(42, "Do'stimning xabari"), BOT_ID) == "Do'stimning xabari"


def test_bot_status_knows_advice(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            advisor = env.ctx.reports.advisor
            await advisor.store.save("2026-10-20", text="**🎯** Kechagi", staff_text="", model="claude-fable-5-2",
                                     cost=0.41, interim=False, note="")
            await advisor.store.mark_sent("2026-10-20")
            await advisor.store.save("2026-10-21", text="Bugungi", staff_text="Salom, hamkasblar", model="claude-opus-5-5",
                                     cost=0.2, interim=True, note="")
            tools = owner_tools(env)
            data = await ask(tools, "bot_status", section="advice")
            assert set(data) == {"now", "advice"}
            advice = data["advice"]
            assert (advice["enabled"], advice["time"]) == (True, "20:30")
            first, second = advice["latest"]
            assert first == {"report_day": "2026-10-21", "sent": None, "model": "Opus 5.5", "advice": "Bugungi",
                             "interim": True, "message_for_operators": "Salom, hamkasblar"}
            assert second["report_day"] == "2026-10-20" and second["sent"] and second["advice"] == "**🎯** Kechagi"
            everything = await ask(tools, "bot_status")
            assert "advice" not in everything  # matnlar — faqat alohida so'ralganda
            assert everything["settings"]["daily_advice"] == {"enabled": True, "time": "20:30", "model": "Fable 5.2",
                                                              "to": "egasiga lichkada, ish kunlari"}
            assert [a["report_day"] for a in everything["deliveries"]["daily_advice"]] == ["2026-10-21", "2026-10-20"]
            assert "section=advice" in tools.guide()
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- Mini App
async def saved_advice(env):
    advisor = env.ctx.reports.advisor
    await advisor.store.save("2026-10-21", text="**🎯 Asosiy xulosa**\nReklamani kuchaytiring.",
                             staff_text="Assalomu alaykum, hamkasblar!", model="claude-fable-5-2", cost=0.31,
                             interim=False, note="")
    await advisor.store.mark_sent("2026-10-21")


def test_advice_in_mini_app_api(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            env = api.env
            await seed_week(env)
            await saved_advice(env)
            data = await api.call("GET", "/api/report/archive?kind=advice")
            assert [i["key"] for i in data["items"]] == ["advice:2026-10-21"]
            item = data["items"][0]
            assert (item["model"], item["interim"], bool(item["sent_label"])) == ("Fable 5.2", False, True)
            assert data["advice_model"] == "Fable 5.2" and data["schedule"]["advice_time"] == "20:30"
            detail = await api.call("GET", "/api/report/archive/advice:2026-10-21")
            assert detail["text"] == "🎯 Asosiy xulosa\nReklamani kuchaytiring." and detail["kind"] == "advice"
            assert (detail["staff_text"], detail["model"], detail["cost"]) == ("Assalomu alaykum, hamkasblar!", "Fable 5.2", 0.31)
            await api.call("GET", "/api/report/archive/advice:2026-10-22", status=404)
            assert (await api.call("GET", "/api/report/settings"))["advice_model"] == "Fable 5.2"

            # rahbar — tavsiyalarni ko'rmaydi (Excel arxivini ko'radi)
            await env.ctx.reports.store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            leader = (LEADER, "rahbar_1")
            await api.call("GET", "/api/report/archive?kind=weekly", user=leader)
            await api.call("GET", "/api/report/archive?kind=advice", user=leader, status=403)
            await api.call("GET", "/api/report/archive/advice:2026-10-21", user=leader, status=403)

            res = await api.call("PATCH", "/api/report/schedule", {"advice_time": "21:15", "advice_enabled": False})
            assert (res["schedule"]["advice_time"], res["schedule"]["advice_enabled"]) == ("21:15", False)
            schedule = await env.ctx.reports.schedule()
            assert (schedule.advice_time, schedule.advice_enabled) == ("21:15", False)
        finally:
            await api.close()
    run(go())


def test_advice_in_mini_app_screens(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            env = api.env
            await seed_week(env)
            await saved_advice(env)
            async with chromium() as open_page:
                page, base = await open_page(api, width=360)
                await page.goto(base + "#r-arch")
                await settled(page)
                await page.click("[data-action=r-akind][data-k=advice]")
                await page.wait_for_function("document.querySelector('[data-action=r-advice]')"
                                             " && document.querySelector('.seg .on').dataset.k === 'advice'")
                text = await page.evaluate("document.querySelector('#screen').textContent")
                assert "Kunlik tavsiyalar" in text and "21-oktabr" in text and "Fable 5.2" in text
                # 5 ta bo'lim tor ekranga (360 px) sig'adi — nomlar qirqilmaydi
                assert await page.evaluate("Array.from(document.querySelectorAll('.seg button'))"
                                           ".every((b) => b.scrollWidth <= b.clientWidth)")
                await page.click("[data-action=r-advice]")
                await page.wait_for_selector(".sheet .pre-text")
                sheet = await page.evaluate("document.querySelector('.sheet').textContent")
                assert "Reklamani kuchaytiring." in sheet and "**" not in sheet
                assert "Operatorlar uchun xabar" in sheet and "Assalomu alaykum, hamkasblar!" in sheet and "$0.31" in sheet
                await page.click(".sheet", position={"x": 20, "y": 20})

                # sozlamalar: tumbler va vaqt
                await go_hash(page, "#r-set")
                assert "Kunlik tavsiya (AI)" in await page.evaluate("document.querySelector('#screen').textContent")
                async with page.expect_response(lambda r: r.url.endswith("/api/report/schedule")):
                    await page.evaluate("document.querySelector('[data-rtoggle=r-advice]').click()")
                await page.wait_for_function("document.querySelector('#toast').textContent === 'Kunlik tavsiya o\\'chirildi'")
                assert (await env.ctx.reports.schedule()).advice_enabled is False
                await page.click("[data-action=r-atime]")
                await page.fill(".sheet input[name=advice_time]", "21:40")
                async with page.expect_response(lambda r: r.url.endswith("/api/report/schedule")):
                    await page.click(".sheet button[type=submit]")
                await page.wait_for_function("!document.querySelector('.sheet') && !document.querySelector('.skeleton')"
                                             " && document.querySelector('#screen').textContent.includes('21:40')")
                assert (await env.ctx.reports.schedule()).advice_time == "21:40"
                await page.screenshot(path=str(tmp_path / "settings.png"), full_page=True)

                # rahbarga — «Tavsiya» bo'limi yo'q
                await env.ctx.reports.store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
                page, base = await open_page(api, init=init_data(LEADER, "rahbar_1"))
                await page.goto(base + "#r-arch")
                await settled(page)
                assert await page.locator(".seg button").all_inner_texts() == ["Kunlik", "Haftalik", "Oylik"]
        finally:
            await api.close()
    run(go())

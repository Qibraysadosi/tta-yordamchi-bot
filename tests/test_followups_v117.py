"""v1.17: qayta qo'ng'iroq navbati (izohdagi vaqt va status bo'yicha, operatorga eslatma, hisobot va kundalikda
«🔁 9/12», /qayta, Mini App) va lid → vaucher bog'lash (F.I.Sh. bo'yicha: /vaucher, AI, Excel «Vaucher» ustuni)."""
from datetime import date, datetime, timedelta, timezone

from aiogram.exceptions import TelegramForbiddenError

from bot.reports.archive import WEEK
from bot.reports.followups import CURSOR_KEY, into_work, parse_callback, retry_label
from bot.reports.funnel import match, people_of
from bot.reports.stats import Schedule
from bot.reports.store import Lead, iso
from test_ai_tools_v113 import ask, owner_tools
from test_archive import FRI, MON, SAT, THU, column, queue_analysis, seed_week, workbook
from test_fix_miniapp import chromium, settled
from test_flow import run
from test_reports import (BOSS, CARD_1, CARD_2, MALIKA, SARDOR, TZ, WED, AdminSession, classify, local, person,
                          setup_groups, start, ts)
from test_reports_api import Api
from test_vouchers_v114 import VOUCHERS, Vouchers

TUE = date(2026, 10, 20)


class BlockedSession(AdminSession):
    """Ba'zi odamlarga xabar ketmaydi (botni bloklagan yoki /start bosmagan)."""

    blocked: set = set()

    async def make_request(self, bot, method, timeout=None):
        if type(method).__name__ == "SendMessage" and method.chat_id in self.blocked:
            self.calls.append(method)
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        return await super().make_request(bot, method, timeout)


def dms(env, user_id):
    return [m.text for m in env.session.of("SendMessage") if m.chat_id == user_id]


# --------------------------------------------------------------------------- vaqtni tushunish
def test_parse_callback_phrases():
    at = local(WED, "11:30")
    cases = {
        "ertaga": local(THU, "10:00"), "ertaga 15:00": local(THU, "15:00"), "Ertaga ertalab": local(THU, "10:00"),
        "indinga": local(FRI, "10:00"), "dushanba": local(date(2026, 10, 26), "10:00"), "juma kuni": local(FRI, "10:00"),
        "chorshanba": local(date(2026, 10, 28), "10:00"), "kechqurun": local(WED, "17:00"),
        "tushdan keyin": local(WED, "14:00"), "2 soatdan keyin": local(WED, "13:30"),
        "yarim soatdan keyin": local(WED, "12:00"), "soat 3 da": local(WED, "15:00"), "15:30": local(WED, "15:30"),
        "keyingi hafta": local(date(2026, 10, 26), "10:00"), "завтра": local(THU, "10:00"),
        "через 2 часа": local(WED, "13:30"), "22.10.2026 18.00": local(THU, "18:00"), "23-oktabr": local(FRI, "10:00"),
        "bugun": local(WED, "12:30"),
    }
    for text, expected in cases.items():
        assert parse_callback(text, at) == expected, text
    for text in ("keyinroq", "keyin", "1 oydan keyin", "5-yanvar", "31.02.2026", "", None):
        assert parse_callback(text, at) is None, text
    assert parse_callback("bugun kechqurun", local(WED, "18:30")) == local(WED, "19:30")  # aytilgan payt — 1 soatdan keyin

    s = Schedule()  # dushanba–shanba, 09:00–19:00
    assert into_work(local(WED, "21:30"), s) == local(THU, "09:00")
    assert into_work(local(SAT, "20:00"), s) == local(date(2026, 10, 26), "09:00")  # yakshanba dam
    assert into_work(local(WED, "07:00"), s) == local(WED, "09:00") and into_work(local(WED, "12:00"), s) == local(WED, "12:00")
    assert [retry_label(m) for m in (0, 60, 180, 1440, 2880, 90)] == [
        "yo'q", "1 soatdan keyin", "3 soatdan keyin", "ertasi kuni", "2 kundan keyin", "90 daqiqadan keyin"]


# --------------------------------------------------------------------------- navbat
async def prepare(env):
    g = await setup_groups(env)
    store = env.ctx.reports.store
    await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
    await store.add_person("operator", username="sardor", user_id=None, name="Sardor")
    await store.add_person("excluded", username=None, user_id=BOSS, name="Boss")
    await env.ctx.reports.followups.sync()  # birinchi ishga tushish — eski belgilar navbatga tushmaydi
    return g


def test_followup_queue_reminders_and_reports(tmp_path):
    async def go():
        env = await start(tmp_path)
        env.session = BlockedSession()
        env.bot.session = env.session
        try:
            g = await prepare(env)
            reports = env.ctx.reports
            followups = reports.followups
            assert await env.db.get_config(CURSOR_KEY) is not None
            malika, sardor = person(MALIKA, "Malika", "malika_op"), person(SARDOR, "Sardor", "sardor")
            a = await g.post(CARD_1, ts(WED, "09:00"))
            b = await g.post(CARD_2, ts(WED, "09:10"))
            c = await g.post("Ism: Dilshod\nTelefon: 99 888 77 66\nKurs: Kompyuter", ts(WED, "09:20"))
            d = await g.post("Ism: Nodira\nTelefon: 95 111 22 33\nKurs: Mobilografiya", ts(WED, "09:30"))
            await g.react(a["message_id"], malika, "🆒", ts(WED, "10:00"))  # 3 soatdan keyin
            await g.comment(b, sardor, "ertaga soat 3 da qayta qilaman", ts(WED, "10:30"))
            await g.react(c["message_id"], malika, "👍", ts(WED, "10:35"))  # asosiy natija — navbat yo'q
            await g.react(d["message_id"], malika, "🆒", ts(WED, "10:40"))
            await g.comment(d, malika, "ko'tarmadi, kechqurun qilaman", ts(WED, "10:45"))  # reaksiyani almashtiradi
            await g.react(d["message_id"], person(BOSS, "Boss"), "🆒", ts(WED, "11:00"))  # istisno — sanalmaydi
            assert await followups.sync() == 1 and await followups.sync() == 0  # keyingi izoh tahlilini kutadi
            pending = await reports.store.pending_comments(10)
            env.api.responses.append(classify(
                {"id": pending[0].id, "call": True, "status": "", "reason": "", "callback": "ertaga 15:00"},
                {"id": pending[1].id, "call": True, "status": "🆒", "reason": "", "callback": "kechqurun"}))
            await reports.analyze_pending()
            await followups.sync()
            waiting = await followups.store.waiting()
            assert [(f.note, f.kind) for f in waiting] == [("🆒 ko'tarmadi", "retry"), ("kechqurun", "callback"),
                                                           ("ertaga 15:00", "callback")]
            assert [f.due_at for f in waiting] == [iso(local(WED, "13:00")), iso(local(WED, "17:00")),
                                                   iso(local(THU, "15:00"))]
            assert [f.user_id for f in waiting] == [MALIKA, MALIKA, SARDOR]

            # eslatma: vaqti kelganda — o'sha operatorga lichkada
            env.session.clear()
            assert await followups.check(env.bot, local(WED, "12:59")) == 0
            followups._next = None
            assert await followups.check(env.bot, local(WED, "13:05")) == 1
            text = dms(env, MALIKA)[-1]
            assert "Qayta qo'ng'iroq vaqti keldi</b> — 1 ta lid" in text
            assert "Aziza Karimova — Kompyuter savodxonligi · 🆒 ko'tarmadi (bugun 10:00)" in text and "ochish" in text
            await g.react(a["message_id"], malika, "👎", ts(WED, "13:20"))  # qo'ng'iroq qildi — bajarildi
            followups._next = None
            await followups.check(env.bot, local(WED, "17:05"))
            assert "Nodira — Mobilografiya · «kechqurun» deb yozgansiz (bugun 10:45)" in dms(env, MALIKA)[-1]
            followups._next = None
            assert await followups.check(env.bot, local(WED, "19:30")) == 0  # ish vaqtidan tashqari

            # hisobot (rahbarlar guruhi), kundalik va /qayta
            _, text, _ = await reports.compose(WED, local(WED, "19:05"), interim=False)
            assert "🔁 Qayta qo'ng'iroq: <b>1</b>/2 bajarildi · qolgan: Malika 1" in text
            journal = await reports.archive.journal_text(local(WED, "20:00"))
            assert "🔁 <b>Qayta qo'ng'iroq</b>: bugun 2 ta vaqti keldi — 1 tasi qilindi, 1 tasi qoldi (Malika 1)" in journal
            assert "📅 Ertaga rejada: 1 ta" in journal
            reports.now = lambda: local(WED, "20:00")
            await env.send_text("/qayta")
            text = env.session.of("SendMessage")[-1].text
            assert "<b>⏰ Vaqti o'tgan: 1</b>" in text and "Malika — 1 ta" in text
            assert "«kechqurun» deb yozgan (bugun 10:45)" in text and "Keyingi kunlarga: 1 ta" in text
            assert "So'nggi 7 kun: 2 tadan 1 tasi bajarildi · bajarilmagan: Malika 1" in text

            # AI: report_stats → callbacks
            reports.now = lambda: local(WED, "18:00")
            stats = await ask(owner_tools(env), "report_stats", period="today")
            assert {k: stats["callbacks"][k] for k in ("due", "done", "missed", "waiting")} == {
                "due": 2, "done": 1, "missed": 1, "waiting": 0}
            assert stats["callbacks"]["by_person"][0] == {"name": "Malika", "due": 2, "done": 1, "missed": 1, "waiting": 0}

            # o'chirilsa — eslatma yo'q; yoqilsa — keladi; operator botni ochmagan — kundalikda ogohlantirish
            await reports.save_schedule({"followups": False})
            followups._next = None
            assert await followups.check(env.bot, local(THU, "15:05")) == 0
            await reports.save_schedule({"followups": True})
            env.session.blocked = {SARDOR}
            followups._next = None
            assert await followups.check(env.bot, local(THU, "15:10")) == 0  # yetmadi
            journal = await reports.archive.journal_text(local(THU, "20:00"))
            assert "1 tasi qoldi (Sardor 1)" in journal and "⚠️ 1 ta eslatma yetmadi" in journal
        finally:
            env.session.blocked = set()
            await env.close()
    run(go())


def test_status_retry_settings(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            data = await api.call("GET", "/api/report/settings")
            statuses = {s["emoji"]: s for s in data["statuses"]}
            assert (statuses["🆒"]["retry_minutes"], statuses["🆒"]["retry_label"]) == (180, "3 soatdan keyin")
            assert (statuses["🤔"]["retry_minutes"], statuses["👍"]["retry_minutes"]) == (1440, 0)
            assert [c["minutes"] for c in data["retry_choices"]] == [0, 60, 120, 180, 1440, 2880]
            await api.call("PATCH", f"/api/report/statuses/{statuses['🆒']['id']}", {"retry_minutes": 60})
            await api.call("PATCH", f"/api/report/statuses/{statuses['🆒']['id']}", {"retry_minutes": -5}, status=400)
            await api.call("POST", "/api/report/statuses", {"emoji": "📵", "label": "o'chiq", "retry_minutes": 120})
            store = api.env.ctx.reports.store
            now = {s.emoji: s.retry_minutes for s in await store.statuses()}
            assert (now["🆒"], now["📵"], now["🤔"]) == (60, 120, 1440)
            await store.update_status(statuses["🤔"]["id"], retry_minutes=0)
            await store.seed_statuses()  # qayta ishga tushish — egasining tanlovi saqlanadi
            assert {s.emoji: s.retry_minutes for s in await store.statuses()}["🤔"] == 0

            async with chromium() as open_page:
                page, base = await open_page(api, width=360)
                await page.goto(base + "#r-set")
                await settled(page)
                text = await page.evaluate("document.querySelector('#screen').textContent")
                assert "Qayta qo'ng'iroq eslatmasi" in text and "🔁 qayta qo'ng'iroq: 1 soatdan keyin" in text
                await page.click(f"[data-action=r-status][data-id='{statuses['🆒']['id']}']")
                await page.wait_for_selector(".sheet select[name=retry]")
                assert await page.evaluate("document.querySelector('.sheet select[name=retry]').value") == "60"
                await page.select_option(".sheet select[name=retry]", "1440")
                async with page.expect_response(lambda r: "/api/report/statuses/" in r.url):
                    await page.click(".sheet button[type=submit]")
                await page.wait_for_function("!document.querySelector('.sheet')")
                assert {s.emoji: s.retry_minutes for s in await store.statuses()}["🆒"] == 1440
                await page.wait_for_selector("[data-rtoggle=r-followups]", state="attached")
                async with page.expect_response(lambda r: r.url.endswith("/api/report/schedule")):
                    await page.evaluate("document.querySelector('[data-rtoggle=r-followups]').click()")
                await page.wait_for_function(
                    "document.querySelector('#toast').textContent === 'Qayta qo\\'ng\\'iroq eslatmasi o\\'chirildi'")
                assert (await api.env.ctx.reports.schedule()).followups is False
        finally:
            await api.close()
    run(go())


# --------------------------------------------------------------------------- lid → vaucher
def lead(lead_id, name, when, phones=""):
    return Lead(id=lead_id, chat_id=1, message_id=lead_id, source="post", created_at=iso(when), name=name,
                phones=phones, age="", district="", course="", reminded=False)


def voucher(voucher_id, name, when, phone=""):
    return {"id": voucher_id, "name": name, "name_key": "", "phone": phone, "created_at": iso(when)}


def test_lead_voucher_matching_rules():
    leads = [lead(1, "Aziza Karimova", local(MON, "10:00"), "+998901234567"),
             lead(2, "Bobur", local(TUE, "11:00"), "+998937654321"),
             lead(3, "Dilshod", local(WED, "09:30"), "+998998887766"),
             lead(4, "Nodira Aliyeva", local(THU, "10:00")),
             lead(5, "Jasur", local(THU, "11:00")),
             lead(6, "Kamola Sobirova", local(SAT, "10:00"))]
    people = people_of([
        voucher(10, "Каримова Азиза Рустам қизи", local(THU, "12:00")),  # kirill, tartib va «qizi» farqsiz
        voucher(11, "Boburov Bobur", local(FRI, "10:00"), "998937654321"),  # faqat ism — telefon mos
        voucher(12, "Ergashev Dilshod", local(FRI, "11:00"), "998900000000"),  # faqat ism — shu ismli bitta odam
        voucher(13, "Nodira Karimova", local(FRI, "12:00")),  # familiyasi boshqa — yo'q
        voucher(14, "Sardor Aliyev", local(FRI, "13:00"), "998901234567"),  # telefon mos, ism boshqa — yo'q
        voucher(15, "Jasur Toshmatov", local(FRI, "14:00")), voucher(16, "Jasur Qodirov", local(FRI, "15:00")),
        voucher(17, "Sobirova Kamola", local(WED, "10:00")),  # vaucher liddan oldin — yo'q
    ])
    assert match(leads, people) == {10: 1, 11: 2, 12: 3}


async def add_vouchers(env):
    """3 ta vaucher (JSHSHIR / vaucher raqami / telefon bilan) va 1 ta faqat ism — u sanalmaydi va lidga bog'lanmaydi
    (lidlarda «Nodira» bor)."""
    store = env.ctx.reports.vouchers.store
    rows = (("Karimova Aziza Rustam qizi", {"pinfl": "12345678901234"}, local(THU, "12:00")),
            ("Toshmatov Sherzod", {"voucher_no": "261-190757"}, local(THU, "13:00")),
            ("Boburov Bobur", {"phone": "998937654321"}, local(FRI, "10:00")),
            ("Saidova Nodira", {}, local(FRI, "11:00")))
    for n, (name, extra, when) in enumerate(rows):
        fields = {"name": name, "phone": "", "course": "Kurs", "district": "", "mahalla": "", "pinfl": "",
                  "passport": "", "note": "", **extra}
        voucher_id = await store.add_voucher(VOUCHERS, 9000 + n, created_at=iso(when), sender_id=606,
                                             sender_name="Dilnoza", fields=fields, status="sent", status_msg=None)
        await store.add_event(voucher_id, "sent", iso(when), VOUCHERS, 9000 + n)


def test_leads_linked_to_vouchers(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            await Vouchers(env).join()
            await add_vouchers(env)
            reports.now = lambda: local(SAT, "21:00")
            tools = owner_tools(env)

            found = await ask(tools, "voucher_list", period="this_week", calendar=True)
            people = {p["name"]: p for p in found["people"]}
            assert people["Karimova Aziza Rustam qizi"]["lead"]["first_called_by"] == "Malika"
            assert people["Karimova Aziza Rustam qizi"]["lead"]["lead_at"] == "19.10.2026 10:00"
            assert people["Boburov Bobur"]["lead"]["first_called_by"] == "Sardor" and "lead" not in people["Toshmatov Sherzod"]
            assert {k: found["from_leads"][k] for k in ("count", "of_sent")} == {"count": 2, "of_sent": 3}
            assert found["from_leads"]["by_first_caller"] == [{"name": "Malika", "vouchers": 1},
                                                             {"name": "Sardor", "vouchers": 1}]

            leads = {item["name"]: item for item in (await ask(tools, "report_leads", period="this_week"))["leads"]}
            assert leads["Aziza Karimova"]["voucher"] == "📤 yuborildi (vaucher guruhida 22.10)"
            assert "voucher" not in leads["Nodira"]

            await env.send_text("/vaucher")
            text = env.session.of("SendMessage")[-1].text
            assert "🔗 Shu oy lidlardan: <b>2</b> ta (Malika 1, Sardor 1) · boshqa manbadan: 1" in text
            assert "Jami ro'yxatda: 3 ta odam · ⚠️ ma'lumot yetarli emas: 1 ta" in text
            assert [p["name"] for p in found["not_counted"]["people"]] == ["Saidova Nodira"]
            lines = await reports.vouchers.report_lines(local(WED, "19:00"), local(THU, "19:00"))
            assert lines[0].startswith("📄 Vaucher: <b>2</b> ta yuborildi") and "· lidlardan 1" in lines[0]

            export = await reports.archive.collect(WEEK, MON, date(2026, 10, 25), local(SAT, "21:00"))
            full, masked = await reports.archive.files(export, False, True)
            for data in (full, masked):
                sheet = workbook(data)["Lidlar"]
                assert column(sheet, "Vaucher")[:2] == ["📤 yuborildi · 22.10.2026", "📤 yuborildi · 23.10.2026"]
                assert column(sheet, "Ism")[:2] == ["Aziza Karimova", "Bobur"]
        finally:
            await env.close()
    run(go())


def test_old_events_are_not_queued(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            a = await g.post(CARD_1, ts(WED, "09:00"))
            await g.react(a["message_id"], person(MALIKA, "Malika", "malika_op"), "🆒", ts(WED, "10:00"))
            followups = env.ctx.reports.followups
            assert await followups.sync() == 0  # birinchi ishga tushish: kursor shu yerdan
            assert await followups.store.waiting() == []
            await g.react(a["message_id"], person(MALIKA, "Malika", "malika_op"), "🤔", ts(WED, "11:00"))
            assert await followups.sync() == 1
            waiting = await followups.store.waiting()
            assert [(f.note, f.due_at) for f in waiting] == [("🤔 o'ylayapti", iso(local(THU, "11:00")))]
            assert datetime.strptime(waiting[0].due_at, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(TZ) \
                == local(WED, "11:00") + timedelta(days=1)
        finally:
            await env.close()
    run(go())

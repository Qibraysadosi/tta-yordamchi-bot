"""v1.7: call center hisobotlari — lid kartochkasi, statuslar (reaksiya + izoh), davr, kunlik hisobot, xodimlar."""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.types import ChatMemberAdministrator, ChatMemberMember, User

import fakes
from bot.reports.leads import extract_phones, mask_phones, message_link, parse_lead
from bot.reports.render import render_report
from bot.reports.stats import Schedule, current_day, in_work_hours, latest_due_day, neighbour_day, period_bounds
from test_flow import OWNER, Env, run

TZ = ZoneInfo("Asia/Tashkent")
LEADS = -1001111111111
LEADERS = -1002222222222
CHANNEL = -1003333333333
BOT_ID = 123456  # make_settings dagi token: 123456:TESTTOKEN
MALIKA, SARDOR, JASUR, BOSS = 501, 502, 503, 504
WED = date(2026, 10, 21)  # chorshanba

CARD_1 = """Ism: Aziza Karimova
Telefon raqami: +998 90 123-45-67
Yoshi: 24
Tumani: Chilonzor
Kurs: Kompyuter savodxonligi"""

CARD_2 = """🆕 YANGI ARIZA
Ismi: Bobur
Yoshi: 31
Raqami: 93.765.43.21
Tuman: Yunusobod
Tanlagan kursi: Massaj
Qo'shimcha aloqa: Onasi | p:+998 97 111 22 33"""


def ts(day: date, hhmm: str) -> int:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return int(datetime(day.year, day.month, day.day, hours, minutes, tzinfo=TZ).timestamp())


def local(day: date, hhmm: str) -> datetime:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hours, minutes, tzinfo=TZ)


def person(uid, name, username=None):
    data = {"id": uid, "is_bot": False, "first_name": name}
    if username:
        data["username"] = username
    return data


# --------------------------------------------------------------------------- lid kartochkasi
def test_parse_lead_formats():
    one = parse_lead(CARD_1)
    assert (one.name, one.age, one.district, one.course) == ("Aziza Karimova", "24", "Chilonzor", "Kompyuter savodxonligi")
    assert one.phones == ["+998901234567"] and one.looks_like_lead
    two = parse_lead(CARD_2)
    assert (two.name, two.age, two.district, two.course) == ("Bobur", "31", "Yunusobod", "Massaj")
    assert two.phones == ["+998937654321", "+998971112233"]  # asosiy va qo'shimcha raqam
    assert not parse_lead("Salom, bugun nechta lid bor?").looks_like_lead
    assert extract_phones("998901234567, 90 123 45 67 va 12345") == ["+998901234567"]


def test_mask_and_link():
    assert mask_phones("+998 90 123-45-67 ga qildim, ko'tarmadi") == "[raqam] ga qildim, ko'tarmadi"
    assert mask_phones("2 marta qildim") == "2 marta qildim"
    assert message_link(-1001234567890, 55) == "https://t.me/c/1234567890/55"
    assert message_link(-12345, 5) is None


# --------------------------------------------------------------------------- davr
def test_schedule_periods():
    s = Schedule()  # 19:00, dushanba–shanba
    assert period_bounds(WED, s, TZ) == (local(date(2026, 10, 20), "19:00"), local(WED, "19:00"))
    monday = date(2026, 10, 26)
    start, end = period_bounds(monday, s, TZ)  # yakshanba dam — shanba 19:00 dan dushanba 19:00 gacha
    assert start == local(date(2026, 10, 24), "19:00") and end == local(monday, "19:00")
    assert current_day(local(WED, "18:59"), s) == WED
    assert current_day(local(WED, "19:00"), s) == date(2026, 10, 22)
    assert current_day(local(date(2026, 10, 24), "20:00"), s) == monday  # shanba kechqurun → dushanba davri
    assert latest_due_day(local(WED, "18:00"), s) == date(2026, 10, 20)
    assert latest_due_day(local(WED, "19:05"), s) == WED
    assert latest_due_day(local(date(2026, 10, 25), "12:00"), s) == date(2026, 10, 24)  # yakshanba
    assert neighbour_day(monday, s, -1) == date(2026, 10, 24) and neighbour_day(date(2026, 10, 24), s, 1) == monday
    assert in_work_hours(local(WED, "09:00"), s) and not in_work_hours(local(WED, "19:00"), s)
    assert not in_work_hours(local(date(2026, 10, 25), "12:00"), s)
    custom = s.updated({"time": "18:30", "days": [0, 2, 4], "remind_minutes": "45", "work_start": "8:00"})
    assert (custom.time, custom.days, custom.remind_minutes, custom.work_start) == ("18:30", (0, 2, 4), 45, "08:00")
    assert custom.days_label() == "Du, Ch, Ju" and s.days_label() == "Du–Sh"
    assert Schedule.from_json(custom.to_json()) == custom and Schedule.from_json("buzuq") == Schedule()
    assert s.updated({"time": "25:00", "days": []}).time == "19:00" and s.updated({"days": []}).days == s.days


# --------------------------------------------------------------------------- oqim
class Group:
    """Env + guruh yangilanishlari (soxta Telegram)."""

    def __init__(self, env: Env):
        self.env = env
        self.mid = 1000

    def chat(self, chat_id, title):
        return {"id": chat_id, "type": "supergroup", "title": title}

    async def join(self, chat_id, title, by=OWNER, status="member"):
        bot_user = {"id": BOT_ID, "is_bot": True, "first_name": "TTA Yordamchi", "username": "claude_test_bot"}
        new = {"status": status, "user": bot_user}
        if status == "administrator":
            new.update(can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_delete_messages=False,
                       can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False,
                       can_change_info=False, can_invite_users=False, can_post_stories=False, can_edit_stories=False,
                       can_delete_stories=False, can_send_welcome_messages=False)
        await self.env._feed({"my_chat_member": {
            "chat": self.chat(chat_id, title), "from": person(by, "Shohrux"), "date": ts(WED, "08:00"),
            "old_chat_member": {"status": "left", "user": bot_user}, "new_chat_member": new,
        }})

    async def post(self, text, when, chat_id=LEADS):
        self.mid += 1
        message = {
            "message_id": self.mid, "date": when, "chat": self.chat(chat_id, "KOMMENTARIYA TA LIDLAR"),
            "from": {"id": 777000, "is_bot": False, "first_name": "Telegram"},
            "sender_chat": {"id": CHANNEL, "type": "channel", "title": "TA LIDLAR KANALI"},
            "is_automatic_forward": True, "text": text,
        }
        await self.env._feed({"message": message})
        return message

    async def comment(self, lead_msg, user, text, when, chat_id=LEADS, voice=False):
        self.mid += 1
        payload = {
            "message_id": self.mid, "date": when, "chat": self.chat(chat_id, "KOMMENTARIYA TA LIDLAR"),
            "from": user, "message_thread_id": lead_msg["message_id"], "reply_to_message": lead_msg,
        }
        if voice:
            payload["voice"] = {"file_id": "v1", "file_unique_id": "uv1", "duration": 3}
        else:
            payload["text"] = text
        await self.env._feed({"message": payload})
        return payload

    async def react(self, message_id, user, emoji, when, chat_id=LEADS):
        await self.env._feed({"message_reaction": {
            "chat": self.chat(chat_id, "KOMMENTARIYA TA LIDLAR"), "message_id": message_id, "date": when,
            "user": user, "old_reaction": [], "new_reaction": [{"type": "emoji", "emoji": emoji}] if emoji else [],
        }})


class AdminSession(fakes.FakeSession):
    """GetChatMember: bot lidlar guruhida admin."""

    admin = True

    async def make_request(self, bot, method, timeout=None):
        if type(method).__name__ == "GetChatMember":
            self.calls.append(method)
            user = User(id=BOT_ID, is_bot=True, first_name="Bot")
            if self.admin:
                return ChatMemberAdministrator(
                    user=user, can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_delete_messages=False,
                    can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False,
                    can_change_info=False, can_invite_users=False, can_post_stories=False, can_edit_stories=False,
                    can_delete_stories=False, can_send_welcome_messages=False,
                )
            return ChatMemberMember(user=user)
        return await super().make_request(bot, method, timeout)


async def start(tmp_path, responses=None, **over):
    env = await Env().start(tmp_path, responses, **over)
    env.session = AdminSession()
    env.bot.session = env.session
    env.ctx.reports.now = lambda: local(WED, "15:00")  # testlar haqiqiy soatga bog'liq bo'lmasin
    await env.ctx.reports.ensure()
    await env.db.set_config("report:since", "2026-10-01T00:00:00")
    return env


async def setup_groups(env):
    g = Group(env)
    await g.join(LEADS, "KOMMENTARIYA TA LIDLAR")
    prompt = env.session.of("SendMessage")[-1]
    assert prompt.chat_id == OWNER and "KOMMENTARIYA TA LIDLAR" in prompt.text and "Lidlar" in prompt.text
    data = [b.callback_data for row in prompt.reply_markup.inline_keyboard for b in row]
    assert data == [f"rg:{LEADS}:leads", f"rg:{LEADS}:leaders", f"rg:{LEADS}:done", f"rg:{LEADS}:off"]
    await env.press(f"rg:{LEADS}:leads")
    await env.press(f"rg:{LEADS}:done")
    summary = env.session.of("EditMessageText")[-1].text
    assert "📥 Lidlar" in summary and "admin emas" not in summary
    await g.join(LEADERS, "Rahbarlar")
    await env.press(f"rg:{LEADERS}:leaders")
    await env.press(f"rg:{LEADERS}:done")
    assert "har kuni 19:00 da" in env.session.of("EditMessageText")[-1].text
    return g


def classify(*items):
    return fakes.json_answer(json.dumps(list(items), ensure_ascii=False), input_tokens=900, output_tokens=120)


def test_group_flow_and_daily_report(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            groups = {gr.chat_id: gr.roles for gr in await store.groups()}
            assert groups == {LEADS: ("leads",), LEADERS: ("leaders",)}

            # operatorlar ro'yxati: Malika va Sardor; Jasur ro'yxatda yo'q, Boss — istisno
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await store.add_person("operator", username="sardor", user_id=None, name="Sardor")
            await store.add_person("excluded", username="boss", user_id=None)
            await env.db.set_config("report:since", "2026-10-21T03:00:00")  # kuzatish chorshanba 08:00 da boshlangan
            env.session.clear()

            a = await g.post(CARD_1, ts(WED, "09:10"))
            b = await g.post(CARD_2, ts(WED, "09:20"))
            c = await g.post("Ism: Dilshod\nTelefon: 99 888 77 66\nKurs: Kompyuter", ts(WED, "11:00"))
            await g.post("Ism: Nodira\nTelefon: 95 111 22 33\nKurs: Mobilografiya", ts(WED, "12:00"))
            malika, sardor = person(MALIKA, "Malika", "Malika_Op"), person(SARDOR, "Sardor", "sardor")
            jasur, boss = person(JASUR, "Jasur", "jasur_admin"), person(BOSS, "Rahbar", "boss")

            await g.react(a["message_id"], malika, "🆒", ts(WED, "10:00"))
            await g.comment(a, malika, "2 marta qildim ko'tarmadi", ts(WED, "10:01"))
            await g.react(b["message_id"], malika, "👍", ts(WED, "10:05"))
            await g.comment(b, malika, "ertaga keladi", ts(WED, "10:06"))
            await g.react(c["message_id"], sardor, "👍", ts(WED, "11:30"))  # reaksiya 👍, izohda — o'ylab ko'radi
            await g.comment(c, sardor, "o'ylab ko'raman dedi, juma kuni qayta qilaman", ts(WED, "11:31"))
            await g.react(a["message_id"], jasur, "👎", ts(WED, "12:30"))
            await g.comment(a, jasur, "uzoqda yashar ekan, kelmaydi", ts(WED, "12:31"))
            await g.comment(b, boss, "Malika, bunga ertaga yana qiling", ts(WED, "13:00"))
            await g.react(c["message_id"], boss, "👍", ts(WED, "13:05"))  # istisno — sanalmaydi
            await g.comment(b, sardor, "", ts(WED, "14:00"), voice=True)  # ovozli izoh: natija — oldingi reaksiya

            # guruhda bot hech narsa yozmaydi, begonaga parol so'ralmaydi
            assert not [m for m in env.session.of("SendMessage") if m.chat_id in (LEADS, LEADERS)]
            assert env.api.requests == []

            leads = {lead.name: lead for lead in await store.leads_between("2026-10-21T00:00:00", "2026-10-22T00:00:00")}
            assert set(leads) == {"Aziza Karimova", "Bobur", "Dilshod", "Nodira"}
            assert leads["Bobur"].phones == "+998937654321,+998971112233" and leads["Bobur"].course == "Massaj"
            assert (await store.people("operator"))[0].user_id == MALIKA  # @username → ID bog'landi (katta-kichik harf)

            pending = await store.pending_comments(50)
            assert [e.text for e in pending] == [
                "2 marta qildim ko'tarmadi", "ertaga keladi", "o'ylab ko'raman dedi, juma kuni qayta qilaman",
                "uzoqda yashar ekan, kelmaydi", "Malika, bunga ertaga yana qiling",
            ]
            ids = [e.id for e in pending]
            env.api.responses += [classify(
                {"id": ids[0], "call": True, "status": "🆒", "reason": "", "callback": ""},
                {"id": ids[1], "call": True, "status": "👍", "reason": "", "callback": "ertaga"},
                {"id": ids[2], "call": True, "status": "🤔", "reason": "", "callback": "juma"},
                {"id": ids[3], "call": True, "status": "👎", "reason": "uzoq", "callback": ""},
                {"id": ids[4], "call": False, "status": "", "reason": "", "callback": ""},
            )]
            now = local(WED, "15:00")
            ov = await env.ctx.reports.overview(WED, now)  # tahlilsiz: izohlar ham qo'ng'iroq sifatida sanaladi
            assert ov.live and ov.pending == 5

            sent = await env.ctx.reports.send_report(env.bot, WED, now, interim=True)
            assert sent == 1
            req = env.api.requests[0]
            assert req["model"] == "claude-haiku-5-5" and req["thinking"] == {"type": "disabled"}
            assert "👍 — keladi" in req["system"] and "99 888" not in req["messages"][0]["content"]
            sent_items = json.loads(req["messages"][0]["content"])
            assert sent_items[0]["reaction"] == "🆒" and sent_items[4]["reaction"] is None

            ov = await env.ctx.reports.overview(WED, now)
            rows = {r.name: r for r in ov.rows}
            assert list(rows) == ["Malika", "Sardor"]
            assert rows["Malika"].total == 2 and dict(rows["Malika"].counts) == {"🆒": 1, "👍": 1}
            # Sardor: c — izoh reaksiyadan keyin (🤔, ziddiyat); b — ovozli izoh, reaksiyasi yo'q → ❔
            assert rows["Sardor"].total == 2 and dict(rows["Sardor"].counts) == {"🤔": 1, "❔": 1}
            assert [(e.name, e.total, dict(e.counts)) for e in ov.extras] == [("Jasur", 1, {"👎": 1})]
            assert ov.conflicts == 1 and ov.callbacks == 2 and ov.reasons == [("uzoq", 1)]
            assert (ov.new_leads, ov.unmarked) == (4, 1)  # Nodira hech kim belgilamagan
            # kurslar: lid bo'yicha so'nggi natija (istisno — rahbarning 👍 i hisobga olinmaydi)
            assert ov.courses == [("massaj", 1, 1), ("kompyuter", 1, 0), ("kompyuter savodxonligi", 1, 0)]
            assert ov.columns() == ["👍", "👎", "🆒", "🤔", "🤝", "💔", "❔"]

            interim = env.session.of("SendMessage")[-1]
            assert interim.chat_id == LEADERS and interim.parse_mode == "HTML" and "oraliq" in interim.text
            usage = await env.db.spend_by_user(datetime(2026, 1, 1, tzinfo=timezone.utc))
            assert usage and usage[0][0] == 0  # tahlil xarajati «Guruh tahlili» (ID 0) qatorida

            # 19:00 — kunlik hisobot bir marta, keyingi tekshiruvda takrorlanmaydi
            env.session.clear()
            await env.ctx.reports.tick(env.bot, local(WED, "18:59"))
            assert not env.session.of("SendMessage")
            await env.ctx.reports.tick(env.bot, local(WED, "19:00"))
            report = env.session.of("SendMessage")[-1]
            assert report.chat_id == LEADERS and "KUNLIK HISOBOT · 21.10.2026 (chorshanba)" in report.text
            assert "20.10 19:00 → 21.10 19:00" in report.text and "kechikib" not in report.text
            assert "<pre>" in report.text and "Malika" in report.text and "JAMI" in report.text
            assert "Ro'yxatda yo'q: Jasur (@jasur_admin) — 1 ta lid (👎 1)" in report.text
            assert "Yangi lidlar: 4 · belgilanmagan: 1" in report.text
            assert "rad sabablari — uzoq (1)" in report.text and "qayta qo'ng'iroq so'raganlar — 2 ta" in report.text
            assert "Ziddiyat: 1 ta lidda" in report.text and "Rahbar" not in report.text
            assert report.reply_markup is None  # BotFather'da Mini App sozlanmagan — «Batafsil» tugmasi yo'q
            env.session.clear()
            await env.ctx.reports.tick(env.bot, local(WED, "19:00") + timedelta(minutes=5))
            assert not env.session.of("SendMessage")
            assert await store.report_sent("2026-10-21 19:00")
        finally:
            await env.close()
    run(go())


def test_details_button_follows_botfather_setting(tmp_path):
    async def go():
        from bot.reports.service import BOT_INFO_TTL

        env = await start(tmp_path, webapp_url="https://bepulkurs.uz/yordamchi/")
        try:
            await setup_groups(env)
            reports = env.ctx.reports
            now = local(WED, "15:00")
            # BotFather'da Main Mini App yoqilmagan — «📊 Batafsil» tugmasi yo'q
            await reports.send_report(env.bot, WED, now, interim=True)
            assert env.session.of("SendMessage")[-1].reply_markup is None and env.session.of("GetMe")
            # yoqildi: getMe natijasi eskirgach (10 daqiqa) botni qayta ishga tushirmasdan ko'rinadi
            env.session.me_fields = {"has_main_web_app": True}
            await reports.send_report(env.bot, WED, now, interim=True)
            assert env.session.of("SendMessage")[-1].reply_markup is None
            reports._bot_info_at -= BOT_INFO_TTL + 1
            await reports.send_report(env.bot, WED, now, interim=True)
            button = env.session.of("SendMessage")[-1].reply_markup.inline_keyboard[0][0]
            assert button.text == "📊 Batafsil" and button.url == "https://t.me/claude_test_bot?startapp=hisobot"
            await reports.send_report(env.bot, date(2026, 10, 20), now)  # yakunlangan kun — sana bilan
            button = env.session.of("SendMessage")[-1].reply_markup.inline_keyboard[0][0]
            assert button.url == "https://t.me/claude_test_bot?startapp=hisobot_20261020"
        finally:
            await env.close()
    run(go())


def test_report_text_table_alignment():
    from collections import Counter

    from bot.reports.stats import Overview, Row
    from bot.reports.store import Status

    statuses = [Status(1, "👍", "keladi", True, 0), Status(2, "🆒", "ko'tarmadi", False, 1)]
    rows = [Row("u:1", "Muhammadali", "m", 1, True, 12, Counter({"👍": 5, "🆒": 7})),
            Row("u:2", "Ali", None, 2, True, 3, Counter({"🆒": 3}))]
    ov = Overview(day=WED, start=local(date(2026, 10, 20), "19:00"), end=local(WED, "19:00"), live=False,
                  statuses=statuses, rows=rows, extras=[], new_leads=5, unmarked=0, courses=[("massaj", 4, 2)],
                  conflicts=0, comments=0, pending=0, reasons=[], callbacks=0, has_operator_list=True)
    text = render_report(ov, late=True)
    table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert table[0] == "Operator  Jami  👍  🆒"
    assert table[1] == "Muhamma…    12   5   7" and table[2] == "Ali          3   0   3"
    assert table[4] == "JAMI        15   5  10"
    assert "kechikib yuborildi" in text and "🎓 Kurslar bo'yicha 👍: massaj 2" in text
    assert "Izohlardan" not in text and "Ziddiyat" not in text


def test_group_messages_stay_silent_and_unknown_group_is_asked(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = Group(env)
            # bot qo'shilgani haqidagi yangilanish kelmagan (v1.7 dan oldin qo'shilgan) — birinchi xabarda so'raladi
            await g.post(CARD_1, ts(WED, "09:00"), chat_id=-1009999)
            asked = [m for m in env.session.of("SendMessage") if m.chat_id == OWNER]
            assert len(asked) == 1 and "guruhiga qo'shildi" in asked[0].text
            await g.post(CARD_2, ts(WED, "09:05"), chat_id=-1009999)
            assert len(env.session.of("SendMessage")) == 1  # qayta so'ralmaydi
            assert await env.ctx.reports.store.leads_between("2026-01-01T00:00:00", "2027-01-01T00:00:00") == []
            # begona guruh a'zosining /start@bot buyrug'i — javobsiz
            await env._feed({"message": {"message_id": 9, "date": ts(WED, "09:10"),
                                         "chat": {"id": -1009999, "type": "supergroup", "title": "X"},
                                         "from": person(777, "Begona"), "text": "/start@claude_test_bot"}})
            assert len(env.session.of("SendMessage")) == 1
            # «Kuzatilmasin» — keyin hech narsa yozilmaydi
            await env.press("rg:-1009999:off")
            assert "kuzatilmaydi" in env.session.of("EditMessageText")[-1].text
            await g.post(CARD_1, ts(WED, "09:20"), chat_id=-1009999)
            assert await env.ctx.reports.store.leads_between("2026-01-01T00:00:00", "2027-01-01T00:00:00") == []
        finally:
            await env.close()
    run(go())


def test_admin_warning_and_role_buttons_owner_only(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            env.session.admin = False
            g = Group(env)
            await g.join(LEADS, "Lidlar")
            await env.press(f"rg:{LEADS}:leads", user_id=777)  # begona tugmani bossa — o'zgarmaydi
            assert (await env.ctx.reports.store.group(LEADS)).roles == ()
            await env.press(f"rg:{LEADS}:leads")
            await env.press(f"rg:{LEADS}:done")
            assert "admin emas" in env.session.of("EditMessageText")[-1].text
            # bot admin qilinsa — egasiga xabar
            env.session.clear()
            await env._feed({"my_chat_member": {
                "chat": g.chat(LEADS, "Lidlar"), "from": person(OWNER, "Shohrux"), "date": ts(WED, "09:00"),
                "old_chat_member": {"status": "member", "user": {"id": BOT_ID, "is_bot": True, "first_name": "B"}},
                "new_chat_member": {"status": "administrator", "user": {"id": BOT_ID, "is_bot": True, "first_name": "B"},
                                    "can_be_edited": False, "is_anonymous": False, "can_manage_chat": True,
                                    "can_delete_messages": False, "can_manage_video_chats": False,
                                    "can_restrict_members": False, "can_promote_members": False, "can_change_info": False,
                                    "can_invite_users": False, "can_post_stories": False, "can_edit_stories": False,
                                    "can_delete_stories": False, "can_send_welcome_messages": False},
            }})
            assert "admin bo'ldi" in env.session.of("SendMessage")[-1].text
            # chiqarib yuborilsa — kuzatish to'xtaydi
            await env._feed({"my_chat_member": {
                "chat": g.chat(LEADS, "Lidlar"), "from": person(OWNER, "Shohrux"), "date": ts(WED, "10:00"),
                "old_chat_member": {"status": "member", "user": {"id": BOT_ID, "is_bot": True, "first_name": "B"}},
                "new_chat_member": {"status": "left", "user": {"id": BOT_ID, "is_bot": True, "first_name": "B"}},
            }})
            assert "chiqarildi" in env.session.of("SendMessage")[-1].text
            assert not (await env.ctx.reports.store.group(LEADS)).active
        finally:
            await env.close()
    run(go())


def test_old_posts_known_through_comments_and_reaction_stubs(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            # bot qo'shilishidan oldingi post: izoh orqali tanib olinadi
            old = {"message_id": 77, "date": ts(date(2026, 10, 19), "10:00"), "chat": g.chat(LEADS, "L"),
                   "from": {"id": 777000, "is_bot": False, "first_name": "Telegram"},
                   "sender_chat": {"id": CHANNEL, "type": "channel", "title": "K"}, "is_automatic_forward": True,
                   "text": CARD_2}
            await g.comment(old, person(MALIKA, "Malika"), "ko'tarmadi", ts(WED, "10:00"))
            lead = await store.lead_by_message(LEADS, 77)
            assert lead.source == "reply" and lead.name == "Bobur" and lead.created_at.startswith("2026-10-19")
            # noma'lum postga reaksiya — «stub» lid; izohga reaksiya — e'tiborsiz
            await g.react(66, person(SARDOR, "Sardor"), "👍", ts(WED, "10:10"))
            stub = await store.lead_by_message(LEADS, 66)
            assert stub.source == "stub" and stub.created_at == ""
            comment_id = g.mid
            await g.react(comment_id, person(SARDOR, "Sardor"), "👍", ts(WED, "10:11"))
            assert await store.lead_by_message(LEADS, comment_id) is None
            # reaksiya olib tashlansa — sanalmaydi
            await g.react(66, person(SARDOR, "Sardor"), None, ts(WED, "10:20"))
            ov = await env.ctx.reports.overview(WED, local(WED, "12:00"))
            assert {r.name: r.total for r in ov.rows} == {"Malika": 1}
            assert ov.new_leads == 0  # eski post va stub yangi lid emas
            # tahrirlangan izoh qayta tahlilga tushadi
            edited = await store.pending_comments(10)
            await store.save_analysis(edited[0].id, is_call=True, status="🆒", reason=None, callback=None)
            await store.commit()
            await env._feed({"edited_message": {"message_id": edited[0].message_id, "date": ts(WED, "10:00"),
                                                "edit_date": ts(WED, "10:30"), "chat": g.chat(LEADS, "L"),
                                                "from": person(MALIKA, "Malika"), "text": "ertaga keladi"}})
            again = await store.pending_comments(10)
            assert [e.text for e in again] == ["ertaga keladi"] and again[0].status is None
        finally:
            await env.close()
    run(go())


def test_reminders_for_unmarked_leads(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await store.add_person("operator", username="sardor", user_id=None, name="Sardor")
            # Malika botga /start bosgan, Sardor — yo'q
            await env.send_text("/start", user_id=MALIKA, **{"from": person(MALIKA, "Malika", "malika_op")})
            hello = env.session.of("SendMessage")[-1]
            assert hello.chat_id == MALIKA and "operatori sifatida" in hello.text
            assert [p.dm_ok for p in await store.people("operator")] == [True, False]
            await env.send_text("salom, savol bor", user_id=MALIKA, **{"from": person(MALIKA, "Malika", "malika_op")})
            assert "operatori" in env.session.of("SendMessage")[-1].text and env.api.requests == []

            first = await g.post(CARD_1, ts(WED, "09:00"))
            await g.post(CARD_2, ts(WED, "09:40"))
            env.session.clear()
            await env.ctx.reports.check_reminders(env.bot, local(WED, "09:35"))
            remind = env.session.of("SendMessage")
            assert len(remind) == 1 and remind[0].chat_id == MALIKA
            assert "1 ta lid 30 daqiqadan beri belgilanmagan" in remind[0].text and "Aziza Karimova" in remind[0].text
            assert "https://t.me/c/1111111111/" in remind[0].text
            env.session.clear()
            await env.ctx.reports.check_reminders(env.bot, local(WED, "09:50"))  # birinchisi qayta eslatilmaydi
            assert not env.session.of("SendMessage")
            await g.react(first["message_id"], person(SARDOR, "Sardor"), "🆒", ts(WED, "10:00"))
            await env.ctx.reports.check_reminders(env.bot, local(WED, "10:15"))
            assert "Bobur" in env.session.of("SendMessage")[-1].text
            env.session.clear()
            await env.ctx.reports.check_reminders(env.bot, local(WED, "20:00"))  # ish vaqtidan keyin — yo'q
            assert not env.session.of("SendMessage")
        finally:
            await env.close()
    run(go())


def test_leader_private_chat_and_strangers(tmp_path):
    async def go():
        env = await start(tmp_path, webapp_url="https://bepulkurs.uz/yordamchi/")
        try:
            store = env.ctx.reports.store
            await store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            await env.send_text("/start", user_id=BOSS, **{"from": person(BOSS, "Rahbar", "Rahbar_1")})
            msg = env.session.of("SendMessage")[-1]
            assert "Hisobotlar" in msg.text and msg.reply_markup.inline_keyboard[0][0].web_app.url.endswith("/yordamchi/")
            assert await env.ctx.reports.is_leader(BOSS, "rahbar_1")
            await env.send_text("salom", user_id=999)  # begona — avvalgidek parol so'raladi
            assert "Bu shaxsiy bot" in env.session.of("SendMessage")[-1].text
        finally:
            await env.close()
    run(go())


def test_report_command_and_groups_list(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await env.send_text("/hisobot")
            assert "Hali guruh yo'q" in env.session.of("SendMessage")[-1].text
            g = await setup_groups(env)
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.react(lead["message_id"], person(MALIKA, "Malika"), "👍", ts(WED, "09:30"))
            env.session.clear()
            await env.send_text("/hisobot")
            out = env.session.of("SendMessage")[-1]
            assert "HISOBOT (oraliq)" in out.text and "Malika" in out.text
            assert [b.callback_data for b in out.reply_markup.inline_keyboard[0]] == ["rp:send", "rp:refresh"]
            await env.press("rp:send")
            assert env.session.of("SendMessage")[-2].chat_id == LEADERS
            assert "rahbarlar guruhiga yuborildi (1 ta chat)" in env.session.of("SendMessage")[-1].text
            await env.send_text("/hisobot", user_id=555)  # a'zo emas — parol so'raladi, hisobot yo'q
            assert "HISOBOT" not in env.session.of("SendMessage")[-1].text
            await env.send_text("/guruhlar")
            listing = env.session.of("SendMessage")[-1]
            assert "KOMMENTARIYA TA LIDLAR" in listing.text and "admin ✅" in listing.text and "Rahbarlar" in listing.text
            await env.press(f"rgo:{LEADS}")
            assert env.session.of("SendMessage")[-1].text.startswith("⚙️")
        finally:
            await env.close()
    run(go())


def test_daily_report_catch_up_and_since(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            service = env.ctx.reports
            env.session.clear()
            # bot 19:00 da o'chiq edi — 21:30 da qayta ishga tushdi: kechikkan hisobot yuboriladi
            assert await service.check_daily(env.bot, local(WED, "21:30"))
            text = env.session.of("SendMessage")[-1].text
            assert "kechikib yuborildi" in text and "qo'ng'iroq qayd etilmadi" in text
            assert not await service.check_daily(env.bot, local(WED, "21:31"))
            # ertasi ertalab: kechagi (payshanba) hisobot yuborilmagan bo'lsa — yuboriladi (13 soat kechikib)
            assert await service.check_daily(env.bot, local(date(2026, 10, 23), "08:00"))
            # 36 soatdan eski davr yuborilmaydi: faqat dushanba kunlari hisobot, bugun chorshanba
            await service.save_schedule({"days": [0]})
            assert not await service.check_daily(env.bot, local(WED, "12:00"))
            await service.save_schedule({"days": [0, 1, 2, 3, 4, 5]})
            # bot shanba 19:30 da o'rnatilgan: shanba 19:00 da tugagan davr uchun hisobot yuborilmaydi
            saturday = date(2026, 10, 24)
            await env.db.set_config("report:since", "2026-10-24T14:30:00")
            assert not await service.check_daily(env.bot, local(saturday, "19:45"))
            # o'chirilgan jadval
            await env.db.set_config("report:since", "2026-10-01T00:00:00")
            await service.save_schedule({"enabled": False})
            assert not await service.check_daily(env.bot, local(saturday, "19:45"))
            await service.save_schedule({"enabled": True})
            assert await service.check_daily(env.bot, local(saturday, "19:45"))
        finally:
            await env.close()
    run(go())


def test_analysis_failures_and_backoff(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.comment(lead, person(MALIKA, "Malika"), "ko'tarmadi", ts(WED, "09:30"))
            service = env.ctx.reports
            # 1) yaroqsiz javob — urinishlar soni oshadi, 3 martadan keyin o'tkazib yuboriladi
            env.api.responses += [fakes.json_answer("tushunmadim")] * 3
            assert await service.analyze_pending(3) == 0
            assert await service.store.pending_comments(10) == []
            # 2) API xatosi — izoh kutib turadi, tahlil 5 daqiqaga to'xtaydi
            await g.comment(lead, person(MALIKA, "Malika"), "yana qildim", ts(WED, "10:00"))
            env.api.responses += [(529, {"type": "error", "error": {"type": "overloaded_error", "message": "x"}})]
            assert await service.analyze_pending(2) == 0
            assert len(await service.store.pending_comments(10)) == 1
            requests = len(env.api.requests)
            assert await service.analyze_pending(2) == 0 and len(env.api.requests) == requests
        finally:
            await env.close()
    run(go())

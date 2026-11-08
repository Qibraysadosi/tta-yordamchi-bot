"""v1.11: administratorlar va rahbarlar hisobotlarda ismi bilan; /xodimlar — bitta xabar bilan ro'yxat."""
import json
from collections import Counter

import fakes
from bot.reports import staff
from bot.reports.excel import OpLine
from bot.reports.render import render_report
from bot.reports.stats import Overview, Row
from bot.reports.store import Status
from test_flow import OWNER, run
from test_reports import CARD_1, CARD_2, WED, local, person, setup_groups, start, ts
from test_reports_api import Api

MALIKA, DILNOZA, AKMAL, JASUR = 601, 602, 603, 604
STAFF_LIST = """@Malika_Op — Malika — operator
2) @dilnoza_admin - Dilnoza - admin
• https://t.me/akmal_boss Akmal aka (rahbar)
+998 90 123 45 67 — Lola"""


def texts(env, chat_id=OWNER):
    return [m.text for m in env.session.of("SendMessage") if m.chat_id == chat_id]


# --------------------------------------------------------------------------- qatorlarni o'qish
def test_parse_staff_lines():
    entries = staff.parse(staff.command_body("/xodimlar@claude_test_bot " + STAFF_LIST + """
Username | Ism | Rol
ID 123456789 — Kamola — Administrator
tg://user?id=55555 — Nodir — rahbar, admin
@test_akk — istisno
@eski_odam — o'chir
@karim_op — Karim — direktor
@aziz_op — Aziz — istisno, operator
998901234567 — Raqam"""))
    got = [(e.username, e.user_id, e.name, sorted(e.roles), e.explicit, bool(e.error)) for e in entries]
    assert got == [
        ("malika_op", None, "Malika", ["operator"], True, False),
        ("dilnoza_admin", None, "Dilnoza", ["admin"], True, False),
        ("akmal_boss", None, "Akmal aka", ["leader"], True, False),
        (None, None, "", [], False, True),  # telefon — Telegram ID emas
        (None, 123456789, "Kamola", ["admin"], True, False),
        (None, 55555, "Nodir", ["admin", "leader"], True, False),
        ("test_akk", None, "", ["excluded"], True, False),
        ("eski_odam", None, "", ["remove"], True, False),
        ("karim_op", None, "Karim direktor", ["operator"], False, True),  # noma'lum rol — xato, taxmin qilinmaydi
        ("aziz_op", None, "Aziz", ["excluded", "operator"], True, True),
        (None, None, "", [], False, True),
    ]
    assert "telefon" in entries[3].error and "direktor" in entries[8].error
    assert [e.line_no for e in entries][4:6] == [6, 7]  # sarlavha qatori («Username | Ism | Rol») o'tkazib yuboriladi
    assert staff.parse("@malika_op Malika")[0].roles == {"operator"} and not staff.parse("@malika_op Malika")[0].explicit
    # /xodimlar'dan keyingi xabar: ro'yxatmi yoki oddiy savolmi
    assert staff.looks_like_list(STAFF_LIST) and staff.looks_like_list("@malika_op — Malika")
    assert staff.looks_like_list("@malika_op Malika admin")
    for chat in ("@malika_op bugun nechta qo'ng'iroq qildi?", "@malika_op Malika", "bugun nechta lid bor",
                 "Salom\n@malika_op bugun qancha qildi", "/hisobot", ""):
        assert not staff.looks_like_list(chat), chat


# --------------------------------------------------------------------------- /xodimlar → hisobot
def test_staff_command_and_names_in_report(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            reports = env.ctx.reports
            store = reports.store
            await store.add_person("excluded", username="akmal_boss", user_id=None)  # avval istisnoga qo'yilgan
            await store.add_person("operator", username="eski_op", user_id=None, name="Eski")
            dilnoza = person(DILNOZA, "Dilya", "dilnoza_admin")
            a = await g.post(CARD_1, ts(WED, "09:10"))
            await g.react(a["message_id"], dilnoza, "🆒", ts(WED, "09:30"))  # bot uni ro'yxatdan oldin ko'rgan
            env.session.clear()

            await env.db.add_member(JASUR, "Jasur", "jasur_x")  # a'zo (AI'dan foydalanadi), lekin egasi emas
            await env.send_text("/xodimlar", user_id=JASUR)
            assert "faqat bot egasi" in texts(env, JASUR)[-1] and not reports.staff_waiting
            await env.send_text("/xodimlar")
            listing = texts(env)[-1]
            assert "Operatorlar va administratorlar</b> (1)" in listing and "• Eski — @eski_op" in listing
            assert "🚫 <b>Istisnolar</b> (1)" in listing and "@dilnoza_admin — Dilnoza — admin" in listing
            assert OWNER in reports.staff_waiting

            env.session.clear()
            await env.send_text(STAFF_LIST + "\n@eski_op — o'chir")  # keyingi xabar — ro'yxat
            reply = texts(env)[-1]
            assert reply.startswith("👥 <b>Xodimlar</b>: 3 ta qo'shildi, 1 ta yangilandi, 1 ta qator o'qilmadi")
            assert "✅ <b>Malika</b> — operator · @malika_op · qo'shildi · guruhda hali ko'rinmagan" in reply
            assert "✅ <b>Dilnoza</b> — administrator · @dilnoza_admin · qo'shildi" in reply
            assert "✅ <b>Akmal aka</b> — rahbar · @akmal_boss · qo'shildi · istisnodan olindi — endi sanaladi" in reply
            assert "⚠️ 4-qator «+998 90 123 45 67 — Lola» — telefon raqami" in reply
            assert "🗑 <b>Eski</b> · @eski_op · ro'yxatlardan olib tashlandi" in reply
            assert not reports.staff_waiting and env.api.requests == []  # AI'ga ketmadi
            # Dilnoza'ni bot oldin ko'rgan — darrov ID bilan bog'landi va salom oldi
            welcome = [m for m in env.session.of("SendMessage") if m.chat_id == DILNOZA]
            assert len(welcome) == 1 and "administratori sifatida" in welcome[0].text
            ops = {p.username: p for p in await store.people("operator")}
            assert set(ops) == {"malika_op", "dilnoza_admin"} and ops["dilnoza_admin"].is_admin
            assert ops["dilnoza_admin"].user_id == DILNOZA and not ops["malika_op"].is_admin
            assert [p.name for p in await store.people("leader")] == ["Akmal aka"]
            assert await store.people("excluded") == []

            # takror yuborish — o'zgarishsiz; lavozim va ism o'zgarsa — yangilandi
            await env.send_text("/xodimlar\n@malika_op — Malika\n@dilnoza_admin — Dilnoza — operator")
            reply = texts(env)[-1]
            assert "▫️ <b>Malika</b> — operator · @malika_op · o'zgarishsiz" in reply
            assert "✏️ <b>Dilnoza</b> — operator · @dilnoza_admin · yangilandi" in reply
            await env.send_text("/staff @dilnoza_admin — Dilnoza Karimova — admin")  # inglizcha taxallus, bitta qator
            assert "✏️ <b>Dilnoza Karimova</b> — administrator" in texts(env)[-1]
            await env.send_text("/xodimlar\n@malika_op — Malika")  # rol yozilmagan — lavozim o'zgarmaydi
            assert "▫️" in texts(env)[-1]

            # guruhda: Malika, administrator Dilnoza, rahbar Akmal va ro'yxatda yo'q Jasur belgilaydi
            malika, akmal = person(MALIKA, "Malika", "malika_op"), person(AKMAL, "Akmal", "Akmal_Boss")
            jasur = person(JASUR, "Jasur", "jasur_x")
            b = await g.post(CARD_2, ts(WED, "10:00"))
            c = await g.post("Ism: Dilshod\nTelefon: 99 888 77 66\nKurs: Kompyuter", ts(WED, "10:05"))
            await g.react(b["message_id"], malika, "👍", ts(WED, "11:00"))
            await g.react(c["message_id"], dilnoza, "👎", ts(WED, "11:10"))
            await g.react(c["message_id"], akmal, "👍", ts(WED, "11:20"))
            await g.react(b["message_id"], jasur, "👎", ts(WED, "11:30"))

            ov = await reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, r.role, r.total) for r in ov.rows] == [("Dilnoza Karimova", "admin", 2), ("Malika", "", 1)]
            assert [(r.name, r.role, r.total) for r in ov.extras] == [("Akmal aka", "leader", 1), ("Jasur", "", 1)]
            assert [r.name for r in ov.leaders] == ["Akmal aka"] and [r.name for r in ov.unlisted] == ["Jasur"]
            text = render_report(ov)
            table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
            assert table[1].startswith("Dilnoz…* ") and table[2].startswith("Malika   ")
            assert "<i>* — administrator</i>" in text
            assert "👔 Rahbar: Akmal aka — 1 ta lid (👍 1)" in text
            assert "➕ Ro'yxatda yo'q: Jasur (@jasur_x) — 1 ta lid (👎 1)" in text
            journal = await reports.archive.journal_text(local(WED, "15:00"), interim=True)
            assert "Qo'ng'iroq: <b>5</b> ta lid" in journal  # rahbar ham sanaladi
        finally:
            await env.close()
    run(go())


def test_waiting_list_does_not_swallow_questions(tmp_path):
    async def go():
        env = await start(tmp_path, responses=[fakes.simple_answer("Malika bugun 0 ta qo'ng'iroq qildi.")])
        try:
            reports = env.ctx.reports
            await reports.store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await env.send_text("/xodimlar")
            assert OWNER in reports.staff_waiting
            await env.send_text("@malika_op bugun nechta qo'ng'iroq qildi?")  # savol — AI suhbatiga ketadi
            assert len(env.api.requests) == 1 and not reports.staff_waiting
            assert [p.name for p in await reports.store.people("operator")] == ["Malika"]
            # kutish tugagan: ro'yxat endi faqat /xodimlar bilan
            reports.staff_waiting[OWNER] = 0.0  # 15 daqiqadan ko'p o'tgan
            env.api.responses.append(fakes.simple_answer("Tushunarli."))
            await env.send_text("@malika_op — Malika Aliyeva — admin")
            assert len(env.api.requests) == 2 and not reports.staff_waiting
            assert not (await reports.store.people("operator"))[0].is_admin
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- kontakt ulashish (username'siz xodim)
ASAL = 605


def contact(user_id=None, first="Asal Ta'lim akademiyasi 🇺🇿", last=None, phone="+998331231238"):
    data = {"phone_number": phone, "first_name": first}
    if last:
        data["last_name"] = last
    if user_id:
        data["user_id"] = user_id
    return {"contact": data}


def test_shared_contact_adds_staff_without_username(tmp_path):
    assert staff.contact_name("Asal Ta'lim akademiyasi 🇺🇿") == "Asal"
    assert staff.contact_name("🇺🇿 «Dilnoza» opa", "Ta'lim") == "Dilnoza" and staff.contact_name("😀", None) == ""

    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            reports = env.ctx.reports
            store = reports.store
            env.session.clear()
            await env._feed({"message": env._msg(OWNER, **contact())})  # Telegram ID'siz kontakt
            assert "Telegram ID yo'q" in texts(env)[-1] and await store.people() == []

            await env._feed({"message": env._msg(OWNER, **contact(ASAL))})
            prompt = env.session.of("SendMessage")[-1]
            assert prompt.chat_id == OWNER and f"ID {ASAL}" in prompt.text and "Hisobotda ismi: <b>Asal</b>" in prompt.text
            data = [b.callback_data for row in prompt.reply_markup.inline_keyboard for b in row]
            assert data == [f"sc:{ASAL}:op", f"sc:{ASAL}:ad", f"sc:{ASAL}:le", f"sc:{ASAL}:ex"]
            await env.db.add_member(MALIKA, "Malika", "malika_op")
            await env.press(f"sc:{ASAL}:op", user_id=MALIKA)  # a'zo, lekin egasi emas
            assert await store.people() == []
            await env.press(f"sc:{ASAL}:op")
            done = env.last_edit()
            assert "✅ <b>Asal</b> — operator · ID 605 · qo'shildi" in done.text and done.reply_markup is None
            [asal] = await store.people("operator")
            assert (asal.user_id, asal.username, asal.name, asal.is_admin) == (ASAL, None, "Asal", False)
            # qayta ulashsa — joriy holati ko'rinadi; administrator qilinadi
            await env._feed({"message": env._msg(OWNER, **contact(ASAL))})
            assert "Hozir: operator «Asal»" in env.session.of("SendMessage")[-1].text
            await env.press(f"sc:{ASAL}:ad")
            assert "✏️ <b>Asal</b> — administrator · ID 605 · yangilandi" in env.last_edit().text
            assert (await store.people("operator"))[0].is_admin
            # username'siz Asal guruhda belgi qo'yadi — ID bo'yicha taniladi, ismi bilan jadvalda
            a = await g.post(CARD_1, ts(WED, "09:10"))
            await g.react(a["message_id"], {"id": ASAL, "is_bot": False, "first_name": "Asal Ta'lim akademiyasi 🇺🇿"},
                          "👍", ts(WED, "09:40"))
            ov = await reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, r.role, r.total) for r in ov.rows] == [("Asal", "admin", 1)] and ov.extras == []
            # boshqa (egasi bo'lmagan) odam ulashgan kontakt e'tiborsiz
            env.session.clear()
            await env._feed({"message": env._msg(MALIKA, **contact(AKMAL, "Akmal"))})
            assert not env.session.of("SendMessage")
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- Mini App va Excel
def test_admin_title_in_api_and_overview(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            res = await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@dilnoza_admin",
                                                                "name": "Dilnoza", "title": "admin"})
            assert res["person"]["label"] == "Dilnoza"
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@x_user", "title": "boss"}, status=400)
            await api.call("POST", "/api/report/people", {"kind": "leader", "who": "@akmal_boss", "name": "Akmal aka",
                                                          "title": "admin"})  # rahbarda lavozim e'tiborsiz
            people = await api.call("GET", "/api/report/people")
            assert [(p["label"], p["title"]) for p in people["operators"]] == [("Dilnoza", "admin")]
            assert [(p["label"], p["title"]) for p in people["leaders"]] == [("Akmal aka", "")]
            dilnoza = people["operators"][0]["id"]
            await api.call("PATCH", f"/api/report/people/{dilnoza}", {"title": "xyz"}, status=400)
            await api.call("PATCH", f"/api/report/people/{dilnoza}", {"title": ""})
            people = await api.call("GET", "/api/report/people")
            assert people["operators"][0]["title"] == ""
            await api.call("PATCH", f"/api/report/people/{dilnoza}", {"name": "Dilnoza K.", "title": "admin"})

            g = await setup_groups(api.env)
            a = await g.post(CARD_1, ts(WED, "09:10"))
            await g.react(a["message_id"], person(DILNOZA, "Dilya", "dilnoza_admin"), "👍", ts(WED, "10:00"))
            await g.react(a["message_id"], person(AKMAL, "Akmal", "akmal_boss"), "👎", ts(WED, "10:05"))
            overview = await api.call("GET", "/api/report/overview")
            assert [(r["name"], r["role"]) for r in overview["rows"]] == [("Dilnoza K.", "admin")]
            assert [(r["name"], r["role"], r["total"]) for r in overview["extras"]] == [("Akmal aka", "leader", 1)]
            assert (await api.web.reports.glance())["calls"] == 2  # rahbar ham sanaladi
        finally:
            await api.close()
    run(go())


def test_excel_labels_and_assistant_roles(tmp_path):
    lines = [OpLine("Dilnoza", "dilnoza_admin", 2, {}, 1, True, "admin"), OpLine("Malika", None, 1, {}, 0),
             OpLine("Akmal aka", "akmal_boss", 1, {}, 1, False, "leader"), OpLine("Jasur", None, 1, {}, 0, False)]
    assert [line.label for line in lines] == ["Dilnoza (admin)", "Malika", "👔 Akmal aka", "➕ Jasur"]

    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            reports = env.ctx.reports
            store = reports.store
            await store.add_person("operator", username="dilnoza_admin", user_id=None, name="Dilnoza", title="admin")
            await store.add_person("leader", username="akmal_boss", user_id=None, name="Akmal aka")
            a = await g.post(CARD_1, ts(WED, "09:10"))
            await g.react(a["message_id"], person(DILNOZA, "Dilya", "dilnoza_admin"), "👍", ts(WED, "10:00"))
            await g.react(a["message_id"], person(AKMAL, "Akmal", "akmal_boss"), "👎", ts(WED, "10:05"))
            reports.now = lambda: local(WED, "15:00")
            stats = json.loads(await reports.ai_tools(masked=False).run("report_stats", {}))
            assert stats["operators"] == [{"name": "Dilnoza", "calls": 1, "by_status": {"👍 keladi": 1}, "main_result": 1,
                                           "username": "@dilnoza_admin", "position": "administrator"}]
            assert [(x["name"], x["position"]) for x in stats["leaders"]] == [("Akmal aka", "rahbar")]
            assert stats["not_in_operator_list"] == [] and stats["totals"]["calls"] == 2
            # Excel: administrator va rahbar belgilari, izoh qatori
            export = await reports.archive.collect("weekly", WED, WED, local(WED, "15:00"))
            assert [(o.label, o.role) for o in export.report.operators] == [("Dilnoza (admin)", "admin"),
                                                                             ("👔 Akmal aka", "leader")]
            assert export.summary["top"][0][0] in ("Dilnoza", "👔 Akmal aka")
        finally:
            await env.close()
    run(go())


def test_admin_mark_fits_name_column():
    statuses = [Status(1, "👍", "keladi", True, 0)]
    rows = [Row("u:1", "Muhammadali", None, 1, True, 3, Counter({"👍": 3}), role="admin"),
            Row("u:2", "Ali", None, 2, True, 1, Counter({"👍": 1}), role="admin"),
            Row("u:3", "Sardor", None, 3, True, 0, Counter())]
    leaders = [Row(f"u:{9 + i}", f"Rahbar {i}", None, 9 + i, False, 1, Counter({"👍": 1}), role="leader") for i in range(7)]
    ov = Overview(day=WED, start=local(WED, "09:00"), end=local(WED, "19:00"), live=False, statuses=statuses,
                  rows=rows, extras=leaders, new_leads=0, unmarked=0, courses=[], conflicts=0, comments=0, pending=0,
                  reasons=[], callbacks=0, has_operator_list=True)
    text = render_report(ov)
    table = text.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert table[1:4] == [f"{'Muhamm…*':<9}{3:>5}{3:>4}", f"{'Ali*':<9}{1:>5}{1:>4}", f"{'Sardor':<9}{0:>5}{0:>4}"]
    assert text.count("👔 Rahbar:") == 5 and "👔 … va yana 2 rahbar" in text and "Ro'yxatda yo'q" not in text
    # faqat rahbarlar belgilagan, operatorlar ro'yxati yo'q — «qayd etilmadi» deyilmaydi
    only = Overview(day=WED, start=local(WED, "09:00"), end=local(WED, "19:00"), live=False, statuses=statuses,
                    rows=[], extras=leaders[:1], new_leads=0, unmarked=0, courses=[], conflicts=0, comments=0,
                    pending=0, reasons=[], callbacks=0, has_operator_list=False)
    text = render_report(only)
    assert "qayd etilmadi" not in text and "👔 Rahbar: Rahbar 0 — 1 ta lid (👍 1)" in text

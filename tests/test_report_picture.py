"""v1.12: kunlik hisobot rasmi (+ qisqa matn), boshqa bo'limlar (Marketing…), bir odamning bir nechta akkaunti,
xodimni uzatilgan xabar / kontakt orqali qo'shish («kimning akkaunti», «boshqa ism»)."""
import json
from collections import Counter
from datetime import date
from io import BytesIO

from aiogram.exceptions import TelegramBadRequest
from PIL import Image

from bot.reports import staff
from bot.reports.excel import OpLine
from bot.reports.picture import render_card
from bot.reports.render import render_caption, render_report
from bot.reports.stats import Overview, Row
from bot.reports.store import DEFAULT_STATUSES, Status, clean_dept
from test_flow import OWNER, run
from test_reports import CARD_1, CARD_2, LEADERS, WED, AdminSession, local, person, setup_groups, start, ts
from test_reports_api import Api
from test_reports_review import failing_env

MARJONA_1, MARJONA_2, BAXTIYOR, ASIYA, MASTURA, STRANGER = 701, 702, 703, 704, 705, 706
STATUSES = [Status(i + 1, e, label, bool(pos), i) for i, (e, label, pos) in enumerate(DEFAULT_STATUSES)]


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return Image.open(BytesIO(data)).size


def overview(rows, extras, **over) -> Overview:
    fields = dict(day=WED, start=local(date(2026, 10, 20), "09:00"), end=local(WED, "19:00"), live=False,
                  statuses=STATUSES, rows=rows, extras=extras, new_leads=12, unmarked=3, courses=[("massaj", 4, 2)],
                  conflicts=1, comments=2, pending=0, reasons=[("uzoq", 1)], callbacks=1, has_operator_list=True)
    fields.update(over)
    return Overview(**fields)


# --------------------------------------------------------------------------- rasm va qisqa matn
def test_card_and_caption():
    rows = [Row("o:n:dilnoza", "Dilnoza", "toshkent_talim_akademiyasi", 1, True, 47,
                Counter({"👍": 19, "👎": 2, "🆒": 15, "🤔": 5, "💔": 3, "❔": 3}), role="admin"),
            Row("o:n:asal", "Asal 🇺🇿", None, 2, True, 22, Counter({"👍": 12, "👎": 1, "🆒": 3, "🤔": 2, "💔": 1, "❔": 3})),
            Row("o:n:azamat", "Azamat", "azamat_supportt", 3, True, 0, Counter())]
    extras = [Row("l:n:mastura", "Mastura", None, 4, False, 3, Counter({"👍": 2, "👎": 1}), role="leader"),
              Row("s:n:baxtiyor:marketing", "Baxtiyor", "mr_bbbbbbbb", 5, False, 1, Counter({"👍": 1}), role="staff",
                  group="Marketing"),
              Row("u:9", "Jasur", "jasur_x", 9, False, 2, Counter({"👎": 1, "🆒": 1}))]
    ov = overview(rows, extras)
    width, height = png_size(render_card(ov))
    assert width == 1080 and 1300 < height < 2600
    # ko'p status — rasm kengayadi; odam juda ko'p — rasm emas (None)
    many = STATUSES + [Status(10 + i, e, "x", False, 10 + i) for i, e in enumerate("🔥🎉🙏👌")]
    assert png_size(render_card(overview(rows, extras, statuses=many)))[0] > 1080
    crowd = [Row(f"u:{i}", f"Odam {i}", None, i, True, 1, Counter({"👍": 1})) for i in range(70)]
    assert render_card(overview(crowd, [])) is None
    interim = render_card(ov, interim_at=local(WED, "16:40"), late=True)
    assert png_size(interim)[0] == 1080

    caption = render_caption(ov, [("uzoq", 1)], interim_at=None)
    assert caption.startswith("📊 <b>KUNLIK HISOBOT · 21.10.2026 (chorshanba)</b>\n<i>20.10 09:00 → 21.10 19:00</i>")
    assert "📞 <b>75</b> ta lidga qo'ng'iroq · 👍 34 (45%)" in caption  # hamma: operatorlar + rahbar + bo'lim + ro'yxatdan tashqari
    assert "📥 Yangi lidlar: 12 · belgilanmagan: 3" in caption and "rad sabablari — uzoq (1)" in caption
    assert "<pre>" not in caption and "Dilnoza" not in caption  # jadval — rasmda
    text = render_report(ov)
    assert "🧑‍💼 Marketing: Baxtiyor — 1 ta lid (👍 1)" in text and "👔 Rahbar: Mastura — 3 ta lid" in text
    assert "➕ Ro'yxatda yo'q: Jasur (@jasur_x)" in text
    assert clean_dept("marketing bo'limi") == "Marketing" and clean_dept(" menejer ") == "Menejer"
    line = OpLine("Baxtiyor", "mr_bbbbbbbb", 1, {}, 1, False, "staff", "Marketing")
    assert line.label == "🧑‍💼 Baxtiyor (Marketing)"


def test_daily_report_goes_as_picture(tmp_path):
    async def go():
        env = await start(tmp_path, picture=True)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_person("operator", username="marjona5651", user_id=None, name="Marjona", title="admin")
            await store.add_person("operator", username="marjona_akademiya", user_id=None, name="Marjona", title="admin")
            await store.add_person("staff", username="mr_bbbbbbbb", user_id=None, name="Baxtiyor", title="Marketing")
            a = await g.post(CARD_1, ts(WED, "09:10"))
            b = await g.post(CARD_2, ts(WED, "09:20"))
            m1, m2 = person(MARJONA_1, "Marjona", "Marjona5651"), person(MARJONA_2, "Marjona TTA", "Marjona_akademiya")
            await g.react(a["message_id"], m1, "🆒", ts(WED, "10:00"))
            await g.react(a["message_id"], m2, "👍", ts(WED, "10:30"))  # o'sha lid ikkinchi akkauntdan — bir marta, so'nggisi
            await g.react(b["message_id"], m2, "👎", ts(WED, "11:00"))
            await g.react(b["message_id"], person(BAXTIYOR, "Baxtiyor TTA", "mr_bbbbbbbb"), "👍", ts(WED, "11:30"))
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, r.role, r.total, dict(r.counts)) for r in ov.rows] == [("Marjona", "admin", 2, {"👍": 1, "👎": 1})]
            assert sorted(ov.rows[0].user_ids) == [MARJONA_1, MARJONA_2]
            assert sorted(ov.rows[0].usernames) == ["marjona5651", "marjona_akademiya"]
            assert [(r.name, r.role, r.group, r.total) for r in ov.extras] == [("Baxtiyor", "staff", "Marketing", 1)]
            assert ov.departments == [("Marketing", ov.extras)] and ov.unlisted == [] and ov.leaders == []

            env.session.clear()
            assert await env.ctx.reports.check_daily(env.bot, local(WED, "19:02"))
            [photo] = env.session.of("SendPhoto")
            assert photo.chat_id == LEADERS and photo.caption.startswith("📊 <b>KUNLIK HISOBOT · 21.10.2026")
            assert "📞 <b>3</b> ta lidga qo'ng'iroq" in photo.caption and photo.parse_mode == "HTML"
            assert png_size(photo.photo.data)[0] == 1080 and not env.session.of("SendMessage")
            saved = await store.report_for_day("2026-10-21")  # arxivda — to'liq matn (jadval bilan)
            assert "<pre>" in saved["text"] and "Marjona*" in saved["text"] and "🧑‍💼 Marketing: Baxtiyor" in saved["text"]
            assert await store.was_seen(LEADERS, env.session.next_id)  # hisobot rasmiga reaksiya — qo'ng'iroq emas
        finally:
            await env.close()
    run(go())


class NoPhotoSession(AdminSession):
    """Rahbarlar guruhida rasm yuborish taqiqlangan."""

    async def make_request(self, bot, method, timeout=None):
        if type(method).__name__ == "SendPhoto" and method.chat_id == LEADERS:
            self.calls.append(method)
            raise TelegramBadRequest(method=method, message="Bad Request: not enough rights to send photos to the chat")
        return await super().make_request(bot, method, timeout)


def test_photo_refused_falls_back_to_text_and_toggle(tmp_path):
    async def go():
        env = await start(tmp_path, picture=True)
        try:
            await setup_groups(env)
            env.session = NoPhotoSession()
            env.bot.session = env.session
            assert await env.ctx.reports.check_daily(env.bot, local(WED, "19:02"))
            sent = [m for m in env.session.of("SendMessage") if m.chat_id == LEADERS]
            assert len(sent) == 1 and "KUNLIK HISOBOT" in sent[0].text and len(env.session.of("SendPhoto")) == 1
            await env.ctx.reports.save_schedule({"picture": False})  # Sozlamalar → «Rasm ko'rinishida» o'chirildi
            env.session.clear()
            await env.ctx.reports.send_report(env.bot, WED, local(WED, "20:00"), interim=True)
            assert not env.session.of("SendPhoto") and env.session.of("SendMessage")
        finally:
            await env.close()
    run(go())


def test_picture_retried_on_network_error_then_owner(tmp_path):
    async def go():
        env = await failing_env(tmp_path, picture=True)
        try:
            await setup_groups(env)
            service = env.ctx.reports
            env.session.fail[LEADERS] = "network"
            assert await service.check_daily(env.bot, local(WED, "19:02"))
            assert not await service.store.report_for_day("2026-10-21")  # vaqtinchalik xato — keyinroq yana
            for minutes in (7, 12, 17, 22, 27):
                await service.check_daily(env.bot, local(WED, "19:00").replace(minute=minutes + 2))
            owner = [m for m in env.session.of("SendPhoto") if m.chat_id == OWNER]
            note = [m for m in env.session.of("SendMessage") if m.chat_id == OWNER]
            assert owner and note and "rahbarlar guruhiga yuborilmadi" in note[-1].text
            assert await service.store.report_for_day("2026-10-21")
        finally:
            await env.close()
    run(go())


def test_report_command_shows_picture_and_refreshes(tmp_path):
    async def go():
        env = await start(tmp_path, picture=True)
        try:
            await setup_groups(env)
            env.session.clear()
            await env.send_text("/hisobot")
            [photo] = env.session.of("SendPhoto")
            assert photo.chat_id == OWNER and "HISOBOT (oraliq)" in photo.caption
            assert [b.callback_data for b in photo.reply_markup.inline_keyboard[0]] == ["rp:send", "rp:refresh"]
            await env._feed({"callback_query": {
                "id": "c1", "from": {"id": OWNER, "is_bot": False, "first_name": "Shohrux"}, "chat_instance": "ci",
                "data": "rp:refresh", "message": {"message_id": 7, "date": 1760240000, "chat": {"id": OWNER, "type": "private"},
                                                  "photo": [{"file_id": "p", "file_unique_id": "u", "width": 1080, "height": 1500}],
                                                  "caption": "eski"}}})
            [media] = env.session.of("EditMessageMedia")
            assert "HISOBOT (oraliq)" in media.media.caption and media.reply_markup is not None
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- /xodimlar: bo'limlar, akkauntlar
def test_staff_list_departments_and_accounts(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            reports = env.ctx.reports
            store = reports.store
            await store.remember_user(OWNER, "yerjanov", "Shohrux")  # egasi guruhda ko'ringan
            await store.add_person("operator", username="mr_bbbbbbbb", user_id=None, name="Baxtiyor")  # xato qo'shilgan
            env.session.clear()
            await env.send_text("/xodimlar\n"
                                "@Marjona5651 @Marjona_akademiya — Marjona — admin\n"
                                "@YERJANOV — Shohrux — direktor\n"
                                "@mr_bbbbbbbb — Baxtiyor — Marketing bo'limi\n"
                                "@YLaziz_1211 — Laziz — menejer")
            reply = [m.text for m in env.session.of("SendMessage") if m.chat_id == OWNER][-1]
            assert "✅ <b>Marjona</b> — administrator · @marjona5651 · qo'shildi" in reply
            assert "✅ <b>Marjona</b> — administrator · @marjona_akademiya · qo'shildi" in reply
            assert "✅ <b>Shohrux</b> — rahbar · @yerjanov · qo'shildi" in reply
            assert "✅ <b>Baxtiyor</b> — Marketing · @mr_bbbbbbbb · qo'shildi · operatorlar jadvalidan olindi" in reply
            assert "✅ <b>Laziz</b> — Menejer · @ylaziz_1211" in reply
            assert not [m for m in env.session.of("SendMessage") if m.chat_id == OWNER and "Salom" in m.text]  # egasiga salom yo'q
            assert [(p.username, p.title) for p in await store.people("operator")] == [
                ("marjona5651", "admin"), ("marjona_akademiya", "admin")]
            assert {(p.name, p.title) for p in await store.people("staff")} == {("Baxtiyor", "Marketing"), ("Laziz", "Menejer")}
            assert OWNER not in await reports.usage_names()  # xarajatda egasi «👔» bo'lib chiqmaydi
            listing = await staff.list_text(store)
            assert "🧑‍💼 <b>Marketing</b> (1)" in listing and "🧑‍💼 <b>Menejer</b> (1)" in listing
        finally:
            await env.close()
    run(go())


def test_forwarded_message_links_second_account(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            reports = env.ctx.reports
            store = reports.store
            await store.add_person("operator", username="tta_manager", user_id=None, name="Asiya", title="admin")
            await store.add_person("leader", username="tta_administrator", user_id=None, name="Mastura")
            await env.send_text("/xodimlar")
            env.session.clear()
            origin = {"type": "user", "date": 1760240000,
                      "sender_user": {"id": ASIYA, "is_bot": False, "first_name": "Asiya", "last_name": "TTA"}}
            await env.send_text("salom", forward_origin=origin)  # Asiyaning shaxsiy akkauntidan xabar
            prompt = env.session.of("SendMessage")[-1]
            assert f"ID {ASIYA}" in prompt.text and env.api.requests == []
            buttons = {b.text: b.callback_data for row in prompt.reply_markup.inline_keyboard for b in row}
            assert "🔗 🎧 Asiya akkaunti" in buttons and "🔗 👔 Mastura akkaunti" in buttons
            await env.press(buttons["🔗 🎧 Asiya akkaunti"])
            assert "✅ <b>Asiya</b> — administrator · ID 704 · qo'shildi" in env.last_edit().text
            assert {(p.username, p.user_id, p.name, p.title) for p in await store.people("operator")} == {
                ("tta_manager", None, "Asiya", "admin"), (None, ASIYA, "Asiya", "admin")}

            # yashirin akkaunt — tushuntirish; kontakt «Арам» → «Aram», «✏️ Boshqa ism» → «Mastura»
            hidden = {"type": "hidden_user", "date": 1760240000, "sender_user_name": "Azamat TTA"}
            await env.send_text("xabar", forward_origin=hidden)
            assert "akkaunti yashiriladi" in env.session.of("SendMessage")[-1].text
            await env._feed({"message": env._msg(OWNER, contact={"phone_number": "+998946047029", "first_name": "Арам",
                                                                 "user_id": MASTURA})})
            assert "Hisobotda ismi: <b>Aram</b>" in env.session.of("SendMessage")[-1].text
            await env.press(f"sc:{MASTURA}:nm")
            assert "ismini yozing" in env.session.of("SendMessage")[-1].text
            await env.send_text("Mastura")
            renamed = env.session.of("SendMessage")[-1]
            assert "Hisobotda ismi: <b>Mastura</b>" in renamed.text and env.api.requests == []
            await env.press(f"sc:{MASTURA}:le")
            assert {(p.username, p.user_id, p.name) for p in await store.people("leader")} == {
                ("tta_administrator", None, "Mastura"), (None, MASTURA, "Mastura")}
            await env.press(f"sc:{STRANGER}:op")  # bot qayta ishga tushgan / eskirgan tugma
            assert len(await store.people("operator")) == 2
        finally:
            await env.close()
    run(go())


def test_api_staff_department(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            await api.call("POST", "/api/report/people", {"kind": "staff", "who": "@mr_bbbbbbbb", "name": "Baxtiyor"}, status=400)
            res = await api.call("POST", "/api/report/people", {"kind": "staff", "who": "@mr_bbbbbbbb", "name": "Baxtiyor",
                                                                "title": "marketing bo'limi"})
            assert res["person"]["label"] == "Baxtiyor"
            people = await api.call("GET", "/api/report/people")
            assert [(p["label"], p["title"]) for p in people["staff"]] == [("Baxtiyor", "Marketing")]
            staff_id = people["staff"][0]["id"]
            await api.call("PATCH", f"/api/report/people/{staff_id}", {"title": ""}, status=400)
            await api.call("PATCH", f"/api/report/people/{staff_id}", {"title": "SMM"})
            # operator qilinsa — bo'limdan olinadi
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@mr_bbbbbbbb", "name": "Baxtiyor"})
            people = await api.call("GET", "/api/report/people")
            assert people["staff"] == [] and [p["label"] for p in people["operators"]] == ["Baxtiyor"]
            settings = await api.call("GET", "/api/report/settings")
            assert settings["schedule"]["picture"] is False  # test muhiti: matn (start(picture=False))
            res = await api.call("PATCH", "/api/report/schedule", {"picture": True})
            assert res["schedule"]["picture"] is True
        finally:
            await api.close()
    run(go())


def test_merged_accounts_in_assistant(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            reports = env.ctx.reports
            store = reports.store
            await store.add_person("operator", username="khalifeess", user_id=None, name="Asiya", title="admin")
            await store.add_person("operator", username="tta_manager", user_id=None, name="Asiya", title="admin")
            await store.add_person("staff", username="joziba_omina", user_id=None, name="Ominaxon", title="Marketing")
            a = await g.post(CARD_1, ts(WED, "09:10"))
            await g.react(a["message_id"], person(ASIYA, "Asiya", "khalifeess"), "👍", ts(WED, "10:00"))
            await g.react(a["message_id"], person(ASIYA + 50, "Asiya TA", "TTA_manager"), "👍", ts(WED, "10:05"))
            await g.react(a["message_id"], person(STRANGER, "Ominaxon", "Joziba_Omina"), "🆒", ts(WED, "10:10"))
            stats = json.loads(await reports.ai_tools(masked=False).run("report_stats", {}))
            [asiya] = stats["operators"]
            assert asiya["calls"] == 1 and asiya["position"] == "administrator"
            assert sorted(asiya["accounts"]) == ["@khalifeess", "@tta_manager"]
            assert [(x["name"], x["position"]) for x in stats["other_departments"]] == [("Ominaxon", "Marketing")]
            assert stats["totals"]["calls"] == 2
        finally:
            await env.close()
    run(go())

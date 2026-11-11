"""v1.13: AI hamma narsani biladi — kalendar kunlar, lidlar kesimlari, hisobot / Excel'ni chatga yuborish, bot holati
va guruh yozishmalari (egasiga)."""
import json
from datetime import date

import pytest

import fakes
from bot.reports.archive import RANGE, Period, period_label
from bot.reports.assistant import ReportToolError, resolve_window
from bot.reports.insights import age_key, district_key
from bot.reports.stats import Schedule
from test_archive import MON, NEXT_MON, SAT, THU, column, queue_analysis, seed_week, workbook
from test_assistant import LEADER, tool_result
from test_flow import OWNER, run
from test_reports import LEADERS, LEADS, MALIKA, SARDOR, WED, Group, local, person, start, ts

TUE = date(2026, 10, 20)
SUN = date(2026, 10, 25)


def owner_tools(env):
    return env.ctx.reports.ai_tools(masked=False).bind(env.bot, OWNER, OWNER)


async def ask(tools, name, **args):
    return json.loads(await tools.run(name, args))


async def say(env, chat_id, user, text, when, **extra):
    """Guruhga oddiy xabar (Group yordamchisidagi kabi soxta yangilanish)."""
    g = Group(env)
    env.mid = getattr(env, "mid", 3000) + 1
    message = {"message_id": env.mid, "date": when, "chat": g.chat(chat_id, "Rahbarlar" if chat_id == LEADERS else
                                                                    "KOMMENTARIYA TA LIDLAR"), "from": user, **extra}
    if text is not None:
        message["text"] = text
    await env._feed({"message": message})
    return message


# --------------------------------------------------------------------------- kalendar kunlar
def test_calendar_windows():
    s = Schedule()  # 19:00, dushanba–shanba
    w = resolve_window({"calendar": True}, local(WED, "20:30"), s)
    assert (w.start, w.end, w.live, w.days, w.first, w.calendar) == (
        local(WED, "00:00"), local(THU, "00:00"), True, [WED], WED, True)
    assert "kalendar" in w.label and w.as_dict(local(WED, "20:30"))["basis"].startswith("kalendar")
    w = resolve_window({"period": "yesterday", "calendar": True}, local(NEXT_MON, "10:00"), s)
    assert w.first == SUN and w.days == [] and w.start == local(SUN, "00:00")  # yakshanba — o'zi (dushanbaga o'tmaydi)
    w = resolve_window({"period": "day", "date": "2026-10-25", "calendar": True}, local(NEXT_MON, "10:00"), s)
    assert (w.start, w.end) == (local(SUN, "00:00"), local(NEXT_MON, "00:00"))
    w = resolve_window({"period": "this_week", "calendar": True}, local(WED, "15:00"), s)
    assert (w.start, w.end, len(w.day_spans(s))) == (local(MON, "00:00"), local(NEXT_MON, "00:00"), 7)
    report = resolve_window({"period": "this_week"}, local(WED, "15:00"), s)
    assert len(report.day_spans(s)) == 6 and report.day_spans(s)[0][1:] == (local(date(2026, 10, 17), "19:00"),
                                                                           local(MON, "19:00"))
    assert (report.first, report.last, report.calendar) == (MON, SUN, False)
    w = resolve_window({"period": "range", "date_from": "2026-10-20", "date_to": "2026-10-21", "calendar": True},
                       local(SAT, "12:00"), s)
    assert (w.start, w.end, w.live) == (local(TUE, "00:00"), local(THU, "00:00"), False)
    for bad in ({"period": "day", "date": "2026-10-30", "calendar": True},
                {"period": "range", "date_from": "2026-11-01", "date_to": "2026-11-05"}):
        with pytest.raises(ReportToolError):
            resolve_window(bad, local(WED, "15:00"), s)


def test_report_stats_calendar_days(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            reports.now = lambda: local(SAT, "21:00")
            tools = owner_tools(env)
            by_report = await ask(tools, "report_stats", period="this_week", by_day=True)
            by_calendar = await ask(tools, "report_stats", period="this_week", by_day=True, calendar=True)
            assert by_report["leads"]["new"] == 4 and by_calendar["leads"]["new"] == 5  # shanba 20:00 dagi lid ham
            assert [d["calls"] for d in by_calendar["days"]] == [1, 1, 2, 1, 0, 0]
            assert [d["new_leads"] for d in by_report["days"]][-1] == 0 and by_calendar["days"][-1]["new_leads"] == 1
            assert by_calendar["period"]["basis"].startswith("kalendar")
            day = await ask(tools, "report_stats", period="day", date="2026-10-21", calendar=True)
            assert day["leads"]["new"] == 1 and day["totals"]["calls"] == 2
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- kesimlar
def test_breakdown_keys():
    assert district_key("Chilonzor tumani") == "Chilonzor" and district_key("Toshkent sh., Yunusobod") == "Toshkent yunusobod"
    assert district_key("") == "ko'rsatilmagan"
    assert age_key("24", 2026) == "18–24" and age_key("31 yosh", 2026) == "25–34" and age_key("2010", 2026) == "17 va kichik"
    assert age_key("", 2026) == "noma'lum" and age_key("150", 2026) == "noma'lum"


def test_report_breakdown(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(SAT, "20:30")
            tools = owner_tools(env)

            days = await ask(tools, "report_breakdown", period="this_week")
            assert days["by"] == "day" and days["totals"]["new_leads"] == 4 and days["totals"]["unmarked"] == 1
            assert [g["group"] for g in days["groups"]] == [
                "2026-10-19 (dushanba)", "2026-10-20 (seshanba)", "2026-10-21 (chorshanba)", "2026-10-22 (payshanba)"]
            assert days["totals"]["first_call_min_work_hours"] == 20  # 30, 20 va 15 daqiqa — mediana
            assert days["groups"][0]["first_call_min"] == 30 and "first_call_min" not in days["groups"][3]

            hours = await ask(tools, "report_breakdown", period="this_week", by="hour")
            assert [(g["group"], g["new_leads"]) for g in hours["groups"]] == [
                ("09:00–10:00", 1), ("10:00–11:00", 2), ("11:00–12:00", 1)]
            courses = await ask(tools, "report_breakdown", period="this_week", by="course")
            assert [g["group"] for g in courses["groups"]] == ["kompyuter", "kompyuter savodxonligi", "massaj", "mobilografiya"]
            status = await ask(tools, "report_breakdown", period="this_week", by="status")
            assert {g["group"]: g["new_leads"] for g in status["groups"]} == {
                "🤔 o'ylayapti": 1, "👎 kelmaydi": 1, "🆒 ko'tarmadi": 1, "belgilanmagan": 1}
            ages = await ask(tools, "report_breakdown", period="this_week", by="age")
            assert [g["group"] for g in ages["groups"]] == ["18–24", "25–34", "noma'lum"]
            places = await ask(tools, "report_breakdown", period="this_week", by="district")
            assert [(g["group"], g["new_leads"]) for g in places["groups"]][0] == ("ko'rsatilmagan", 2)

            who = await ask(tools, "report_breakdown", period="this_week", by="operator")
            assert {g["group"]: g["new_leads"] for g in who["groups"]} == {"Malika": 2, "Sardor": 1, "belgilanmagan": 1}
            assert [(c["name"], c["calls"]) for c in who["calls_in_period"]] == [("Malika", 2), ("Jasur", 1), ("Sardor", 1)]

            only = await ask(tools, "report_breakdown", period="this_week", by="course", course="kompyuter")
            assert only["totals"]["new_leads"] == 2 and only["filter"] == {"course": "kompyuter"}
            calendar = await ask(tools, "report_breakdown", period="this_week", calendar=True)
            assert calendar["totals"]["new_leads"] == 5 and calendar["groups"][-1]["group"] == "2026-10-24 (shanba)"
            assert "by:" in (await ask(tools, "report_breakdown", by="yil"))["error"]

            # rahbarga: ismdagi raqam yashiriladi
            g = Group(env)
            odd = person(611, "Jasur")
            odd["last_name"] = "+998 93 111 22 44"
            lead = await g.post("Ism: Zarina\nTelefon: 97 555 44 33\nKurs: Dizayn", ts(THU, "11:00"))
            await g.react(lead["message_id"], odd, "👍", ts(THU, "11:05"))
            hidden = await reports.ai_tools(masked=True).run("report_breakdown", {"period": "this_week", "by": "operator"})
            assert "111 22 44" not in hidden and "[raqam]" in hidden
            assert "111 22 44" in await tools.run("report_breakdown", {"period": "this_week", "by": "operator"})
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- hisobot va Excel'ni yuborish
def test_range_period_labels():
    day = date(2026, 10, 21)
    assert period_label(RANGE, day, day) == "21 oktabr 2026"
    one = Period(RANGE, day, day, None, None, False)
    assert (one.title, one.filename, one.key) == ("Hisobot", "Hisobot_2026-10-21.xlsx", "range:2026-10-21:2026-10-21")
    many = Period(RANGE, TUE, THU, None, None, True)
    assert many.filename == "Hisobot_2026-10-20_2026-10-22_oraliq.xlsx" and many.label == "20–22 oktabr 2026"


def test_report_send_daily_and_excel(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(WED, "20:00")
            tools = owner_tools(env)
            env.session.clear()

            daily = await ask(tools, "report_send", kind="daily", period="yesterday")
            assert daily["sent"] == "kunlik hisobot (matn)" and daily["report_day"] == "2026-10-20" and not daily["interim"]
            text = env.session.of("SendMessage")[-1]
            assert text.chat_id == OWNER and "KUNLIK HISOBOT · 20.10.2026 (seshanba)" in text.text
            again = await ask(tools, "report_send", kind="daily", period="yesterday")
            assert "hozirgina yuborildi" in again["error"]
            assert "bitta kun" in (await ask(tools, "report_send", kind="daily", period="this_week"))["error"]
            assert "kind" in (await ask(tools, "report_send", kind="pdf"))["error"]

            reports.now = lambda: local(NEXT_MON, "10:00")
            tools = owner_tools(env)
            week = await ask(tools, "report_send", kind="excel", period="last_week")
            assert week["file"] == "Haftalik_hisobot_2026-10-19_2026-10-25.xlsx" and week["phones"] == "to'liq"
            name, data, caption = env.session.documents[-1]
            assert name == week["file"] and "Haftalik hisobot" in caption
            assert column(workbook(data)["Lidlar"], "Telefon")[0] == "+998 90 123-45-67"
            assert await reports.archive.store.export("week:2026-10-19") is not None  # Arxivda «yasalgan»
            span = await ask(tools, "report_send", kind="excel", period="range", date_from="2026-10-20", date_to="2026-10-22")
            assert span["file"] == "Hisobot_2026-10-20_2026-10-22.xlsx" and span["period"] == "20–22 oktabr 2026"
            day = await ask(tools, "report_send", kind="excel", period="day", date="2026-10-21")
            assert day["file"] == "Hisobot_2026-10-21.xlsx"
            assert not await reports.archive.store._all("SELECT key FROM rp_exports WHERE kind = 'range'")
            assert "3 tadan" in (await ask(tools, "report_send", kind="excel", period="this_month"))["error"]  # bir javobda

            tools = owner_tools(env)
            long = await ask(tools, "report_send", kind="excel", period="range", date_from="2026-06-01", date_to="2026-10-20")
            assert "100 kungacha" in long["error"]
            unbound = reports.ai_tools(masked=False)
            assert "yuborib bo'lmaydi" in (await ask(unbound, "report_send", kind="daily"))["error"]

            # rahbarga — telefonlari yashirilgan nusxa
            leader = reports.ai_tools(masked=True).bind(env.bot, LEADER, LEADER)
            masked = await ask(leader, "report_send", kind="excel", period="last_week")
            assert masked["phones"].startswith("yashirilgan")
            name, data, caption = env.session.documents[-1]
            assert column(workbook(data)["Lidlar"], "Telefon")[0] == "+998 90 ***-**-67" and "yashirilgan" in caption
            assert env.session.of("SendDocument")[-1].chat_id == LEADER
        finally:
            await env.close()
    run(go())


def test_report_send_picture(tmp_path):
    async def go():
        env = await start(tmp_path, picture=True)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            reports.now = lambda: local(WED, "15:00")
            env.session.clear()
            daily = await ask(owner_tools(env), "report_send", kind="daily")
            assert daily["sent"] == "kunlik hisobot (rasm)" and daily["interim"] is True
            photo = env.session.of("SendPhoto")[-1]
            assert photo.chat_id == OWNER and "HISOBOT (oraliq)" in photo.caption
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- bot holati
def test_bot_status(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            reports = env.ctx.reports
            await env.db.add_member(802, "Ali", "ali")
            await env.db.log_usage(OWNER, "claude-opus-5-5", {}, 0.5)
            await env.db.log_usage(802, "claude-sonnet-5-5", {}, 0.25)
            await reports.store.add_person("leader", username="mastura", user_id=None, name="Mastura")
            await reports.store.add_person("staff", username="baxtiyor", user_id=None, name="Baxtiyor", title="Marketing")
            tools = owner_tools(env)

            data = await ask(tools, "bot_status")
            assert set(data) == {"now", "activity", "costs", "users", "staff", "groups", "settings", "deliveries"}
            costs = data["costs"]
            assert costs["all_time_usd"] == 0.75 and costs["all_time_requests"] == 2
            assert {u["who"]: u["usd"] for u in costs["cycle_by_user"]} == {"egasi": 0.5, "Ali @ali": 0.25}
            assert [m["model"] for m in costs["cycle_by_model"]][0] == "Opus 5.5" and costs["budget_usd"] > 0
            assert data["users"]["members"][0]["username"] == "@ali"
            staff = data["staff"]
            assert [p["name"] for p in staff["operators"]] == ["Malika", "Sardor"]
            assert staff["leaders"][0] == {"name": "Mastura", "account": "@mastura", "not_seen_yet": True}
            assert staff["departments"][0]["department"] == "Marketing"
            groups = {g["title"]: g for g in data["groups"]["groups"]}
            assert groups["KOMMENTARIYA TA LIDLAR"]["roles"] == ["lidlar"] and groups["Rahbarlar"]["roles"] == ["rahbarlar"]
            settings = data["settings"]
            assert settings["daily_report"]["time"] == "19:00" and "👍 keladi (asosiy natija)" in settings["statuses"]
            assert settings["group_messages_kept_days"] == 120
            assert data["deliveries"]["last_backup"] == "hali yo'q"
            assert data["activity"]["tasks"]["open"] == 2
            only = await ask(tools, "bot_status", section="costs")
            assert set(only) == {"now", "costs"}
            assert "section" in (await ask(tools, "bot_status", section="hamma"))["error"]

            hidden = reports.ai_tools(masked=True)  # rahbarga — yo'q
            assert hidden.names == ("report_stats", "report_leads", "task_status", "report_breakdown", "report_send",
                                    "voucher_list")
            assert [s["name"] for s in hidden.specs()] == list(hidden.names)
            with pytest.raises(ReportToolError):
                await hidden.run("bot_status", {})
            with pytest.raises(ReportToolError):
                await hidden.run("group_messages", {})
            assert "Bot va guruhlar" in tools.guide() and "Bot va guruhlar" not in hidden.guide()
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- guruh yozishmalari
def test_group_chat_is_logged_and_searchable(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g, (a, _, _) = await seed_week(env)
            reports = env.ctx.reports
            store = reports.store
            shohrux, malika = person(OWNER, "Shohrux"), person(MALIKA, "Malika", "malika_op")
            first = await say(env, LEADERS, shohrux, "Bugun majlis soat 15:00 da", ts(WED, "10:00"))
            await say(env, LEADERS, malika, "Xo'p, boraman", ts(WED, "10:05"), reply_to_message=first)
            await say(env, LEADERS, malika, None, ts(WED, "10:06"), caption="reja",
                      photo=[{"file_id": "p1", "file_unique_id": "up1", "width": 90, "height": 90}])
            await say(env, LEADERS, person(SARDOR, "Sardor", "sardor"), None, ts(WED, "11:00"),
                      voice={"file_id": "v1", "file_unique_id": "uv1", "duration": 7})
            await g.comment(a, malika, "Qayta qo'ng'iroq qildim", ts(WED, "12:00"))  # lidlar guruhida izoh ham
            await env._feed({"edited_message": {
                "message_id": first["message_id"], "date": first["date"], "edit_date": ts(WED, "10:10"),
                "chat": first["chat"], "from": shohrux, "text": "Bugun majlis soat 16:00 da"}})
            await say(env, LEADERS, shohrux, "Kecha gaplashdik", ts(TUE, "18:00"))
            assert not await store._all("SELECT 1 FROM rp_messages WHERE text LIKE 'Ism:%'")  # lid postlari yozilmaydi

            reports.now = lambda: local(WED, "20:00")
            tools = owner_tools(env)
            today = await ask(tools, "group_messages")  # lidlar guruhidagi izohlar ham (seed_week'dagi biri bilan)
            assert today["total"] == 6 and today["by_group"] == {"Rahbarlar": 4, "KOMMENTARIYA TA LIDLAR": 2}
            assert today["period"]["basis"].startswith("kalendar") and today["messages"][0]["group"] == "KOMMENTARIYA TA LIDLAR"

            leaders = await ask(tools, "group_messages", group="rahbarlar")
            assert leaders["total"] == 4 and leaders["groups"] == ["Rahbarlar"] and "group" not in leaders["messages"][0]
            texts = [m["text"] for m in leaders["messages"]]
            assert texts == ["Bugun majlis soat 16:00 da", "Xo'p, boraman", "[rasm] reja", "[ovozli xabar, 7 s]"]
            assert leaders["messages"][0]["edited"] is True and leaders["messages"][0]["author"] == "Shohrux"
            assert leaders["messages"][1]["reply_to"] == "Shohrux: Bugun majlis soat 16:00 da"
            assert leaders["messages"][1]["author"] == "Malika (@malika_op)"
            assert (await ask(tools, "group_messages", author="malika"))["total"] == 4
            assert (await ask(tools, "group_messages", group="lid"))["total"] == 2
            assert [m["text"] for m in (await ask(tools, "group_messages", search="MAJLIS"))["messages"]] == [
                "Bugun majlis soat 16:00 da"]
            yesterday = await ask(tools, "group_messages", period="yesterday")
            assert [m["text"] for m in yesterday["messages"]] == ["Kecha gaplashdik"]
            last = await ask(tools, "group_messages", limit=1)
            assert last["shown"] == 1 and last["messages"][0]["text"] == "Qayta qo'ng'iroq qildim" and "note" in last
            assert last["messages"][0]["reply_to"] == "lid: Aziza Karimova · Kompyuter savodxonligi"  # qaysi lidga izoh
            assert "Rahbarlar" in (await ask(tools, "group_messages", group="buxgalteriya"))["error"]

            # bot admin bo'lmagan guruh — ogohlantirish
            await store.upsert_group(LEADERS, "Rahbarlar", is_admin=False)
            reports.bot_info = {"can_read_all": False}
            assert "Rahbarlar" in (await ask(tools, "group_messages"))["warning"]

            # 120 kundan eskilari kuniga bir marta o'chiriladi
            assert await reports.prune_chat(local(date(2027, 2, 20), "10:00")) == 7
            assert await reports.prune_chat(local(date(2027, 2, 20), "11:00")) == 0
        finally:
            await env.close()
    run(go())


def test_unassigned_group_is_not_logged(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            other = -1009999999999
            g = Group(env)
            await g.join(other, "Boshqa guruh")
            await env._feed({"message": {"message_id": 1, "date": ts(WED, "10:00"), "chat": g.chat(other, "Boshqa guruh"),
                                         "from": person(MALIKA, "Malika"), "text": "salom"}})
            assert await env.ctx.reports.store.logged_messages("2000-01-01T00:00:00", "2100-01-01T00:00:00") == []
            assert "ulanmagan" in (await ask(owner_tools(env), "group_messages"))["error"]
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- suhbat orqali
def test_owner_asks_for_excel_in_chat(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(NEXT_MON, "10:00")
            env.api.responses += [
                fakes.tool_answer("report_send", {"kind": "excel", "period": "last_week"}),
                fakes.simple_answer("O'tgan hafta Excel'i yuborildi."),
            ]
            env.session.clear()
            await env.send_text("O'tgan hafta hisobotini Excel qilib tashla")
            result = tool_result(env.api.requests[-1])
            assert result["sent"] == "Excel" and result["file"].startswith("Haftalik_hisobot_2026-10-19")
            assert env.session.of("SendDocument")[-1].chat_id == OWNER
            assert "Excel'i yuborildi" in env.last_edit().text
        finally:
            await env.close()
    run(go())


def test_leader_gets_daily_report_in_chat(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(WED, "20:00")
            await reports.store.add_person("leader", username=None, user_id=LEADER, name="Dilshod aka")
            env.api.responses += [
                fakes.tool_answer("report_send", {"kind": "daily", "period": "today"}),
                fakes.simple_answer("Bugungi hisobot yuborildi."),
            ]
            env.session.clear()
            await env.send_text("Bugungi hisobotni yubor", user_id=LEADER)
            assert tool_result(env.api.requests[-1])["report_day"] == "2026-10-21"
            sent = [m for m in env.session.of("SendMessage") if m.chat_id == LEADER]
            assert "KUNLIK HISOBOT · 21.10.2026" in sent[0].text and sent[-1].text == "Bugungi hisobot yuborildi."
        finally:
            await env.close()
    run(go())


def test_owner_tools_without_leads_group(tmp_path):
    """Lidlar guruhi yo'q, lekin rahbarlar guruhi ulangan — egasiga bot holati va yozishmalar baribir bor."""
    async def go():
        env = await start(tmp_path, [fakes.simple_answer("Salom!")])
        try:
            g = Group(env)
            await g.join(LEADERS, "Rahbarlar")
            await env.press(f"rg:{LEADERS}:leaders")
            await env.press(f"rg:{LEADERS}:done")
            await env.send_text("Salom")
            names = [t.get("name") for t in env.api.requests[-1]["tools"]]
            assert "bot_status" in names and "group_messages" in names
            assert LEADS < 0
        finally:
            await env.close()
    run(go())

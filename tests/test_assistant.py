"""v1.10: AI hisobot savollari — report_stats / report_leads / task_status vositalari, egasi va rahbarlar."""
import dataclasses
import json
from datetime import date

import pytest

import fakes
from bot.reports.assistant import ReportToolError, resolve_window
from bot.reports.stats import Schedule
from test_archive import MON, SAT, THU, queue_analysis, seed_week
from test_flow import run
from test_reports import LEADERS, MALIKA, TZ, WED, local, start

LEADER = 801
MEMBER = 802
SUN = date(2026, 10, 25)


def tool_result(request) -> dict:
    """So'rovdagi oxirgi tool_result (vosita natijasi JSON)."""
    block = request["messages"][-1]["content"][0]
    assert block["type"] == "tool_result", block
    return json.loads(block["content"])


async def tools_for(env, *, masked=False):
    return env.ctx.reports.ai_tools(masked=masked)


# --------------------------------------------------------------------------- davr
def test_resolve_window():
    s = Schedule()  # 19:00, dushanba–shanba
    w = resolve_window({}, local(WED, "15:00"), s)
    assert (w.start, w.end, w.live, w.days) == (local(date(2026, 10, 20), "19:00"), local(WED, "19:00"), True, [WED])
    w = resolve_window({"period": "today"}, local(WED, "20:30"), s)  # 19:00 dan keyin ham — bugungi (yopilgan) davr
    assert (w.end, w.live) == (local(WED, "19:00"), False) and "bugun" in w.label
    w = resolve_window({}, local(SUN, "12:00"), s)  # yakshanba — dushanba davri
    assert w.days == [date(2026, 10, 26)] and w.start == local(SAT, "19:00")
    w = resolve_window({"period": "yesterday"}, local(WED, "10:00"), s)
    assert w.days == [date(2026, 10, 20)]
    w = resolve_window({"period": "yesterday"}, local(date(2026, 10, 26), "10:00"), s)  # dushanba → shanba
    assert w.days == [SAT]
    w = resolve_window({"period": "day", "date": "2026-10-25"}, local(date(2026, 10, 26), "10:00"), s)
    assert w.days == [date(2026, 10, 26)]  # yakshanba — dushanba davriga kiradi
    w = resolve_window({"period": "this_week"}, local(WED, "15:00"), s)
    assert w.days == [MON, date(2026, 10, 20), WED, THU, date(2026, 10, 23), SAT] and w.live
    assert w.start == local(date(2026, 10, 17), "19:00") and w.end == local(SAT, "19:00")
    w = resolve_window({"period": "last_week"}, local(WED, "15:00"), s)
    assert w.days[0] == date(2026, 10, 12) and not w.live
    w = resolve_window({"period": "last_month"}, local(WED, "15:00"), s)
    assert w.days[0] == date(2026, 9, 1) and w.days[-1] == date(2026, 9, 30) and w.label.startswith("o'tgan oy")
    w = resolve_window({"period": "range", "date_from": "2026-10-22", "date_to": "2026-10-20"}, local(SAT, "20:00"), s)
    assert w.days == [date(2026, 10, 20), WED, THU]
    for bad in ({"period": "day", "date": "2026-10-30"}, {"period": "day", "date": "30.10"}, {"period": "yil"},
                {"period": "range", "date_from": "2024-01-01", "date_to": "2026-10-01"}):
        with pytest.raises(ReportToolError):
            resolve_window(bad, local(WED, "15:00"), s)


# --------------------------------------------------------------------------- vositalar
def test_report_tools(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            reports.now = lambda: local(WED, "20:00")
            full = await tools_for(env)

            stats = json.loads(await full.run("report_stats", {}))  # bugun (chorshanba davri, yopilgan)
            assert stats["period"]["start"] == "2026-10-20 19:00" and stats["period"]["live"] is False
            assert stats["totals"]["calls"] == 2 and stats["totals"]["by_status"] == {"👎 kelmaydi": 1, "🆒 ko'tarmadi": 1}
            assert [(o["name"], o["calls"]) for o in stats["operators"]] == [("Malika", 1), ("Sardor", 1)]
            assert stats["leads"] == {"new": 1, "unmarked": 0} and stats["comments_waiting_analysis"] == 0
            one = json.loads(await full.run("report_stats", {"operator": "malika"}))
            assert one["operator"]["name"] == "Malika" and one["operator"]["username"] == "@malika_op" and one["rank"] == 1
            missing = json.loads(await full.run("report_stats", {"operator": "Dilnoza"}))
            assert "topilmadi" in missing["error"] and "Malika" in missing["error"]

            reports.now = lambda: local(SAT, "20:00")
            week = json.loads(await full.run("report_stats", {"period": "this_week", "by_day": True}))
            assert week["totals"]["calls"] == 4 and week["not_in_operator_list"][0]["name"] == "Jasur"
            assert [d["calls"] for d in week["days"]] == [1, 1, 2, 1, 0, 0]

            leads = json.loads(await full.run("report_leads", {"period": "this_week"}))
            assert leads["total"] == 4 and [x["name"] for x in leads["leads"]] == ["Aziza Karimova", "Bobur", "Dilshod", "Nodira"]
            first = leads["leads"][0]
            assert first["phone"] == "+998 90 123-45-67" and first["status"] == "🤔 o'ylayapti"
            assert first["marked_by"] == "Malika, Jasur" and first["link"].startswith("https://t.me/c/")
            assert leads["leads"][2]["last_comment"].startswith("ko'tarmadi, keyin +998 99 888 77 66")
            assert leads["leads"][3]["status"] == "belgilanmagan"
            pick = lambda **kw: full.run("report_leads", {"period": "this_week", **kw})  # noqa: E731
            assert [x["name"] for x in json.loads(await pick(status="belgilanmagan"))["leads"]] == ["Nodira"]
            assert [x["name"] for x in json.loads(await pick(status="kelmaydi"))["leads"]] == ["Bobur"]
            assert [x["name"] for x in json.loads(await pick(operator="sardor"))["leads"]] == ["Bobur"]
            assert [x["name"] for x in json.loads(await pick(search="123 45"))["leads"]] == ["Aziza Karimova"]
            assert [x["name"] for x in json.loads(await pick(course="massaj"))["leads"]] == ["Bobur"]
            assert json.loads(await pick(limit=2))["shown"] == 2
            assert "yo'q" in json.loads(await pick(status="keldi"))["error"]

            tasks = json.loads(await full.run("task_status", {"assignee": "Malika"}))
            assert [t["text"] for t in tasks["tasks"]] == ["Lidlar ro'yxatini tekshirish", "Oylik hisobotni topshirish"]
            done = tasks["tasks"][1]
            assert done["state"] == "✅ bajarildi" and done["closed_by"] == "ijrochi o'zi «Ha» bosdi"
            assert done["history"][0].endswith("qayd etildi")
            overdue = json.loads(await full.run("task_status", {"status": "overdue"}))
            assert [t["who"] for t in overdue["tasks"]] == ["Sardor"] and overdue["counts_all"]["overdue"] == 1
            nobody = json.loads(await full.run("task_status", {"assignee": "Dilnoza"}))
            assert "Malika" in nobody["error"] and "Sardor" in nobody["error"]

            # rahbar uchun: telefonlar va matndagi raqamlar yashirilgan
            hidden = await tools_for(env, masked=True)
            masked = json.loads(await hidden.run("report_leads", {"period": "this_week"}))
            assert masked["leads"][0]["phone"] == "+998 90 ***-**-67" and masked["phones"].startswith("yashirilgan")
            assert "888 77 66" not in json.dumps(masked, ensure_ascii=False)
            task = json.loads(await hidden.run("task_status", {"assignee": "sardor"}))
            assert task["tasks"][0]["text"] == "Shartnomani [raqam] bilan kelishish"
            # raqam bo'laklari bilan qidirib yashirilgan raqamni tiklab bo'lmaydi
            probe = json.loads(await hidden.run("report_leads", {"period": "this_week", "search": "123 45"}))
            assert probe["total"] == 0 and "yopiq" in probe["search_note"]
            by_name = json.loads(await hidden.run("report_leads", {"period": "this_week", "search": "aziza"}))
            assert [x["name"] for x in by_name["leads"]] == ["Aziza Karimova"]
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- egasi suhbatda
def test_owner_chat_uses_report_tools(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            await env.ctx.reports.analyze_pending()
            env.ctx.reports.now = lambda: local(WED, "20:00")
            env.api.responses += [
                fakes.tool_answer("report_stats", {"period": "today"}),
                fakes.simple_answer("Bugun **2 ta** lidga qo'ng'iroq qilindi: Malika 1, Sardor 1."),
            ]
            env.session.clear()
            await env.send_text("Bugun nechta qo'ng'iroq bo'ldi?")
            first, second = env.api.requests[-2:]
            names = [t.get("name") for t in first["tools"]]
            assert names[-3:] == ["report_stats", "report_leads", "task_status"] and "create_document" in names
            assert "Call center va vazifalar" in first["system"]
            assert tool_result(second)["totals"]["calls"] == 2
            final = env.last_edit()
            assert "2 ta" in final.text and "Malika 1" in final.text
        finally:
            await env.close()
    run(go())


def test_member_gets_no_report_tools(tmp_path):
    async def go():
        env = await start(tmp_path, [fakes.simple_answer("Salom!")])
        try:
            await seed_week(env)
            await env.db.add_member(MEMBER, "Ali", "ali")
            env.api.responses.insert(0, fakes.simple_answer("Salom!"))
            await env.send_text("Salom", user_id=MEMBER)
            names = [t.get("name") for t in env.api.requests[-1].get("tools", [])]
            assert "report_stats" not in names and "create_document" in names
            assert "Call center va vazifalar" not in env.api.requests[-1]["system"]
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- rahbar botga yozadi
def test_leader_asks_the_bot(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(SAT, "20:00")
            await reports.store.add_person("leader", username=None, user_id=LEADER, name="Dilshod aka")
            env.api.responses += [
                fakes.tool_answer("report_leads", {"period": "this_week", "status": "belgilanmagan"}),
                fakes.simple_answer("Bu hafta 1 ta lid belgilanmagan: Nodira (+998 95 ***-**-33)."),
            ]
            env.session.clear()
            await env.send_text("Bu hafta qaysi lidlarga hech kim qo'ng'iroq qilmadi?", user_id=LEADER)
            first, second = env.api.requests[-2:]
            assert "Rahbar bilan gaplashyapsan" in first["system"] and "Shohrux" not in first["system"]
            assert [t["name"] for t in first["tools"]] == ["report_stats", "report_leads", "task_status"]  # boshqa vosita yo'q
            result = tool_result(second)
            assert result["leads"][0]["name"] == "Nodira" and result["leads"][0]["phone"] == "+998 95 ***-**-33"
            answer = env.session.of("SendMessage")[-1]
            assert answer.chat_id == LEADER and "Nodira" in answer.text
            assert env.session.of("SendChatAction")  # «yozmoqda…»
            spent, count = await env.db.spend(LEADER)
            assert spent > 0 and count == 1
            names = await reports.usage_names()
            assert names[LEADER] == "👔 Dilshod aka"

            # keyingi savol oldingi savol-javobni ko'radi (qisqa xotira)
            env.api.responses.append(fakes.simple_answer("Uning kursi — Mobilografiya."))
            await env.send_text("Uning kursi qaysi?", user_id=LEADER)
            messages = env.api.requests[-1]["messages"]
            assert [m["role"] for m in messages] == ["user", "assistant", "user"]
            assert "Nodira" in messages[1]["content"]

            # kunlik chegara
            env.ctx.settings = dataclasses.replace(env.ctx.settings, leader_daily_limit=2)
            before = len(env.api.requests)
            await env.send_text("Yana bitta savol", user_id=LEADER)
            assert len(env.api.requests) == before and "chegarasi (2 ta)" in env.session.of("SendMessage")[-1].text

            # /start va matnsiz xabar — yo'riqnoma (AI'siz)
            await env.send_text("/start", user_id=LEADER)
            assert "Savolingizni shu yerga yozing" in env.session.of("SendMessage")[-1].text
            assert len(env.api.requests) == before
        finally:
            await env.close()
    run(go())


def test_leader_with_password_gets_masked_tools(tmp_path):
    """Rahbar parol bilan kirgan (a'zo) bo'lsa — oddiy AI suhbatida ham hisobot vositalari, lekin telefonsiz."""
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(SAT, "20:00")
            await reports.store.add_person("leader", username=None, user_id=LEADER, name="Dilshod aka")
            await env.db.add_member(LEADER, "Dilshod", None)
            env.api.responses += [fakes.tool_answer("report_leads", {"period": "this_week"}), fakes.simple_answer("Tayyor.")]
            await env.send_text("Bu hafta lidlar?", user_id=LEADER)
            first, second = env.api.requests[-2:]
            assert "report_leads" in [t.get("name") for t in first["tools"]]
            assert tool_result(second)["leads"][0]["phone"] == "+998 90 ***-**-67"
        finally:
            await env.close()
    run(go())


def test_report_tools_without_groups(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            tools = await tools_for(env)
            data = json.loads(await tools.run("report_stats", {}))
            assert "Lidlar guruhi hali ulanmagan" in data["error"]
            tasks = json.loads(await tools.run("task_status", {}))
            assert tasks["total"] == 0 and tasks["tasks"] == []
            assert TZ.key == "Asia/Tashkent" and LEADERS < 0 and MALIKA
        finally:
            await env.close()
    run(go())

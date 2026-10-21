"""v1.8: vazifalar — «#vazifa» belgisi, ijrochi, muddat (AI), «Bajardingizmi?» so'rovi, takror, eslatma va egasi tugmalari."""
import json
from datetime import date

import fakes
from bot.reports.tasks import CANCELLED, DONE, OPEN, task_body
from test_flow import OWNER, run
from test_reports import LEADS, MALIKA, SARDOR, WED, Group, local, person, start, ts

TASKS = -1004444444444
THU = date(2026, 10, 22)
MON = date(2026, 10, 26)


def parsed(text, due="", repeat=""):
    return fakes.json_answer(json.dumps({"text": text, "due": due, "repeat": repeat}, ensure_ascii=False))


def mention(text, *names):
    """«@name» lar uchun Telegram entity'lari (UTF-16 offset)."""
    entities = []
    for name in names:
        start = text.index("@" + name)
        entities.append({"type": "mention", "offset": len(text[:start].encode("utf-16-le")) // 2,
                         "length": len(name) + 1})
    return entities


class Office:
    """«📋 Vazifalar» guruhi + yordamchilar."""

    def __init__(self, env):
        self.env = env
        self.g = Group(env)

    async def setup(self, chat_id=TASKS, title="Ofis"):
        await self.g.join(chat_id, title)
        await self.env.press(f"rg:{chat_id}:tasks")
        await self.env.press(f"rg:{chat_id}:done")
        return self

    async def say(self, user, text, when, chat_id=TASKS, entities=None, reply_to=None):
        self.g.mid += 1
        message = {"message_id": self.g.mid, "date": when, "chat": self.g.chat(chat_id, "Ofis"), "from": user, "text": text}
        if entities:
            message["entities"] = entities
        if reply_to is not None:
            message["reply_to_message"] = reply_to
        await self.env._feed({"message": message})
        return message

    async def press(self, data, user, message_id, chat_id=TASKS):
        await self.env._feed({"callback_query": {
            "id": str(self.env.update_id + 1), "from": user, "chat_instance": "ci", "data": data,
            "message": {"message_id": message_id, "date": ts(WED, "10:00"), "chat": self.g.chat(chat_id, "Ofis"),
                        "text": "📋 Vazifa"},
        }})
        return self.env.session.of("AnswerCallbackQuery")[-1]


def at(env, day, hhmm):
    env.ctx.reports.now = lambda: local(day, hhmm)
    return local(day, hhmm)


def polls(env, chat_id=TASKS):
    return [m for m in env.session.of("SendMessage") if m.chat_id == chat_id]


def dms(env, chat_id=OWNER):
    return [m for m in env.session.of("SendMessage") if m.chat_id == chat_id]


OWNER_USER = person(OWNER, "Shohrux", "shohrux")
MALIKA_USER = person(MALIKA, "Malika", "malika_op")
SARDOR_USER = person(SARDOR, "Sardor", "sardor_op")


# ---------------------------------------------------------------- belgi
def test_task_marker():
    assert task_body("#vazifa @malika ertaga hisobot") == "@malika ertaga hisobot"
    assert task_body("  #Vazifa: shartnoma") == "shartnoma"
    assert task_body("Vazifa: @x jumagacha") == "@x jumagacha"
    assert task_body("/vazifa@TTA_yordamchi_bot @x bugun") == "@x bugun"
    assert task_body("/vazifa") == "" and task_body("#vazifa") == ""
    assert task_body("#vazifalar ro'yxati") is None and task_body("vazifa bor edi") is None
    assert task_body("/vazifalar") is None and task_body(None) is None


# ---------------------------------------------------------------- guruhda vazifa → so'rov → Yo'q → Ha
def test_group_task_poll_no_then_yes(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            office = await Office(env).setup()
            await office.say(MALIKA_USER, "Salom hammaga", ts(WED, "09:00"))  # bot Malikani ko'rdi (ID)
            at(env, WED, "15:00")
            env.api.responses.append(parsed("Oylik hisobotni topshirish", "2026-10-22 15:00"))
            text = "#vazifa @malika_op ertaga 15:00 gacha oylik hisobotni topshirsin"
            order = await office.say(OWNER_USER, text, ts(WED, "15:00"), entities=mention(text, "malika_op"))

            reaction = env.session.of("SetMessageReaction")[-1]
            assert reaction.chat_id == TASKS and reaction.message_id == order["message_id"]
            assert reaction.reaction[0].emoji == "✍"
            req = env.api.requests[-1]
            assert "Hozir: 2026-10-21 15:00 (chorshanba)" in req["system"] and req["model"] == "claude-haiku-5-5"
            confirm = dms(env)[-1]
            assert "Vazifa qayd etildi" in confirm.text and "Malika" in confirm.text
            assert "Oylik hisobotni topshirish" in confirm.text and "22.10 (pa) 15:00" in confirm.text
            assert "«Ofis» guruhida" in confirm.text and not polls(env)  # guruhga hozircha hech narsa yozilmadi
            assert [b.text for row in confirm.reply_markup.inline_keyboard for b in row] == [
                "⏰ Muddatni o'zgartirish", "🗑 Bekor qilish"]

            tasks = env.ctx.reports.tasks
            task = (await tasks.store.tasks(OPEN))[0]
            assert (task.assignee_id, task.assignee_username, task.due_at) == (MALIKA, "malika_op", "2026-10-22T10:00:00")
            assert await tasks.tick(env.bot, at(env, THU, "14:59")) == 0
            assert await tasks.tick(env.bot, at(env, THU, "15:00")) == 1
            poll = polls(env)[-1]
            assert "@malika_op" in poll.text and "Oylik hisobotni topshirish" in poll.text and "Bajardingizmi?" in poll.text
            assert [b.callback_data for b in poll.reply_markup.inline_keyboard[0]] == [f"tq:{task.id}:y", f"tq:{task.id}:n"]
            poll_id = env.session.next_id

            alert = await office.press(f"tq:{task.id}:y", SARDOR_USER, poll_id)  # boshqa odam bosa olmaydi
            assert alert.show_alert and "Malika uchun" in alert.text
            assert (await tasks.store.get(task.id)).status == OPEN

            at(env, THU, "15:10")
            answer = await office.press(f"tq:{task.id}:n", MALIKA_USER, poll_id)
            assert "keyinroq" in answer.text
            edited = env.session.of("EditMessageText")[-1]
            assert edited.message_id == poll_id and "Hali bajarilmagan · 18:10 da yana so'rayman" in edited.text
            task = await tasks.store.get(task.id)
            assert (task.no_count, task.asks, task.next_ask_at) == (1, 0, "2026-10-22T13:10:00")

            assert await tasks.tick(env.bot, at(env, THU, "18:10")) == 1
            assert not [e for e in env.session.of("EditMessageText") if "Javob bo'lmadi" in e.text]  # javob berilgani tegilmaydi
            second = env.session.next_id
            at(env, THU, "18:20")
            await office.press(f"tq:{task.id}:y", MALIKA_USER, second)
            task = await tasks.store.get(task.id)
            assert task.status == DONE and task.done_by == MALIKA
            assert "✅ <b>Bajarildi</b> · 22.10 18:20" in env.session.of("EditMessageText")[-1].text
            assert "Malika</b> vazifani bajardi" in dms(env)[-1].text
            assert await tasks.tick(env.bot, at(env, THU, "18:59")) == 0
            kinds = [h["kind"] for h in await tasks.store.history(task.id)]
            assert kinds == ["created", "asked", "no", "asked", "yes"]
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- lichkada vazifa, muddat tugmalari
def test_private_task_default_due_and_presets(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await Office(env).setup()
            at(env, WED, "15:00")
            env.api.responses.append(parsed("Shartnomani tayyorlash"))
            text = "#vazifa @sardor_op shartnomani tayyorlasin"
            await env.send_text(text, entities=mention(text, "sardor_op"))
            assert len(env.api.requests) == 1 and "vazifa xabaridan" in env.api.requests[0]["system"]  # AI suhbatiga ketmadi
            confirm = dms(env)[-1]
            assert "muddat aytilmadi" in confirm.text and "22.10 (pa) 18:00" in confirm.text
            codes = [b.callback_data.rsplit(":", 1)[-1] for row in confirm.reply_markup.inline_keyboard for b in row]
            tasks = env.ctx.reports.tasks
            task = (await tasks.store.tasks(OPEN))[0]
            assert codes == ["t", "m10", "m", "3d", "1w", "x"] and not task.due_set and task.poll_chat == TASKS
            assert task.assignee_id is None and task.assignee_username == "sardor_op"
            reaction = env.session.of("SetMessageReaction")[-1]
            assert reaction.chat_id == OWNER

            await env.press(f"tk:{task.id}:d:m10")
            task = await tasks.store.get(task.id)
            assert task.due_set and task.due_at == "2026-10-22T05:00:00" and task.next_ask_at == "2026-10-22T05:00:00"
            assert "22.10 (pa) 10:00" in env.session.of("EditMessageText")[-1].text

            # ijrochi hali ko'rinmagan: username bilan bosganda ID bog'lanadi
            await tasks.tick(env.bot, at(env, THU, "10:00"))
            office = Office(env)
            await office.press(f"tq:{task.id}:y", SARDOR_USER, env.session.next_id)
            task = await tasks.store.get(task.id)
            assert task.status == DONE and task.assignee_id == SARDOR
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- belgi qoidalari
def test_task_rules_and_hints(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            office = await Office(env).setup()
            at(env, WED, "15:00")
            tasks = env.ctx.reports.tasks
            # begona «#vazifa» — vazifa emas
            text = "#vazifa @malika_op hisobot"
            await office.say(SARDOR_USER, text, ts(WED, "15:00"), entities=mention(text, "malika_op"))
            assert await tasks.store.tasks() == [] and not env.session.of("SetMessageReaction")
            # kimga ekani aytilmagan
            await office.say(OWNER_USER, "#vazifa hisobotni tekshiring", ts(WED, "15:01"))
            assert "Kimga?" in dms(env)[-1].text and await tasks.store.tasks() == []
            # reply — ijrochi xabar egasi
            source = await office.say(MALIKA_USER, "Shartnoma qachon kerak?", ts(WED, "15:02"))
            env.api.responses.append(parsed("Shartnomani tayyorlash", "2026-10-23 18:00"))
            await office.say(OWNER_USER, "#vazifa jumagacha tayyorlang", ts(WED, "15:03"), reply_to=source)
            task = (await tasks.store.tasks(OPEN))[0]
            assert (task.assignee_id, task.assignee_name) == (MALIKA, "Malika")
            # Vazifalar turi yo'q guruhda — maslahat, vazifa qayd etilmaydi
            g = Group(env)
            await g.join(LEADS, "Lidlar")
            await env.press(f"rg:{LEADS}:leads")
            await env.press(f"rg:{LEADS}:done")
            await office.say(OWNER_USER, "#vazifa @malika_op x", ts(WED, "15:04"), chat_id=LEADS,
                             entities=mention("#vazifa @malika_op x", "malika_op"))
            assert "vazifalar uchun belgilanmagan" in dms(env)[-1].text and len(await tasks.store.tasks()) == 1
            # AI ishlamasa — matn tozalanadi, muddat standart
            env.api.responses.append((500, {"type": "error", "error": {"type": "api_error", "message": "boom"}}))
            text = "#vazifa @sardor_op, zalni tozalasin!"
            await office.say(OWNER_USER, text, ts(WED, "15:05"), entities=mention(text, "sardor_op"))
            last = (await tasks.store.tasks())[0]
            assert last.text == "Zalni tozalasin!" and not last.due_set
            # a'zo lichkada «#vazifa» yozsa — odatdagi AI suhbati
            await env.db.add_member(555, "Ali", "ali")
            env.api.responses.append(fakes.simple_answer("Tushunarli."))
            await env.send_text("#vazifa @x bugun", user_id=555)
            assert len(await tasks.store.tasks()) == 2 and env.api.requests[-1]["model"] != "claude-haiku-5-5"
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- javobsiz — 3 marta, keyin egasiga
def test_silent_assignee_escalates(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            office = await Office(env).setup()
            await office.say(MALIKA_USER, "salom", ts(WED, "09:00"))
            at(env, WED, "15:00")
            env.api.responses.append(parsed("Hisobot", "2026-10-22 10:00"))
            text = "#vazifa @malika_op ertaga 10 da hisobot"
            await office.say(OWNER_USER, text, ts(WED, "15:00"), entities=mention(text, "malika_op"))
            tasks = env.ctx.reports.tasks
            task = (await tasks.store.tasks(OPEN))[0]
            for hhmm in ("10:00", "12:00", "14:00"):
                assert await tasks.tick(env.bot, at(env, THU, hhmm)) == 1
            assert len(polls(env)) == 3
            stale = [e for e in env.session.of("EditMessageText") if "Javob bo'lmadi" in e.text]
            assert len(stale) == 2  # oldingi ikkita so'rovning tugmalari olindi
            await tasks.tick(env.bot, at(env, THU, "16:00"))
            alert = dms(env)[-1]
            assert "3 marta javob bermadi" in alert.text and "So'rash to'xtatildi" in alert.text
            assert [b.callback_data for b in alert.reply_markup.inline_keyboard[0]] == [
                f"tk:{task.id}:ask", f"tk:{task.id}:ok", f"tk:{task.id}:x"]
            assert await tasks.tick(env.bot, at(env, THU, "18:00")) == 0 and len(polls(env)) == 3
            await env.press(f"tk:{task.id}:ask")  # egasi: yana so'rash
            assert len(polls(env)) == 4
            task = await tasks.store.get(task.id)
            assert (task.asks, task.escalated) == (1, False)
            await env.press(f"tk:{task.id}:ok")
            assert (await tasks.store.get(task.id)).status == DONE
            edits = env.session.of("EditMessageText")
            assert "rahbar yopdi" in edits[-2].text and edits[-2].chat_id == TASKS  # guruhdagi so'rov yopildi
            assert "Bajarildi deb yopildi" in edits[-1].text  # egasining xabari
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- takroriy vazifa
def test_recurring_task_and_cancel(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            office = await Office(env).setup()
            await office.say(MALIKA_USER, "salom", ts(WED, "09:00"))
            at(env, WED, "15:00")
            env.api.responses.append(parsed("Lidlar ro'yxatini tekshirish", "2026-10-26 10:00", "weekly:0"))
            text = "Vazifa: @malika_op har dushanba 10:00 da lidlar ro'yxatini tekshirsin"
            await office.say(OWNER_USER, text, ts(WED, "15:00"), entities=mention(text, "malika_op"))
            assert "🔁 har dushanba" in dms(env)[-1].text
            tasks = env.ctx.reports.tasks
            first = (await tasks.store.tasks(OPEN))[0]
            assert first.repeat == "weekly:0"
            await tasks.tick(env.bot, at(env, MON, "10:00"))
            opened = await tasks.store.tasks(OPEN)
            assert len(opened) == 2
            second = next(t for t in opened if t.id != first.id)
            assert (second.due_at, second.parent_id, second.repeat) == ("2026-11-02T05:00:00", first.id, "weekly:0")
            await office.press(f"tq:{first.id}:y", MALIKA_USER, env.session.next_id)
            await env.press(f"tk:{second.id}:x")  # keyingisini bekor qilish — takror to'xtaydi
            assert (await tasks.store.get(second.id)).status == CANCELLED
            assert await tasks.tick(env.bot, at(env, date(2026, 11, 2), "10:00")) == 0
            assert [t.status for t in await tasks.store.tasks()] == [CANCELLED, DONE]
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- ish vaqti va guruh yo'qligi
def test_work_hours_and_missing_group(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            at(env, WED, "15:00")
            env.api.responses.append(parsed("Hisobot", "2026-10-21 20:30"))
            text = "#vazifa @malika_op bugun 20:30 gacha hisobot"
            await env.send_text(text, entities=mention(text, "malika_op"))
            assert "Vazifalar» guruhi belgilanmagan" in dms(env)[-1].text
            tasks = env.ctx.reports.tasks
            task = (await tasks.store.tasks(OPEN))[0]
            assert task.next_ask_at == "2026-10-22T04:00:00"  # 20:30 ish vaqtidan keyin — ertangi 09:00
            assert await tasks.tick(env.bot, at(env, WED, "21:00")) == 0
            await tasks.tick(env.bot, at(env, THU, "09:00"))
            assert "so'rov yuborilmadi" in dms(env)[-1].text
            count = len(dms(env))
            await tasks.tick(env.bot, at(env, THU, "10:00"))
            assert len(dms(env)) == count  # ogohlantirish bir marta
            await Office(env).setup()  # guruh belgilandi — keyingi urinishda so'rov chiqadi
            assert await tasks.tick(env.bot, at(env, THU, "11:00")) == 1
            assert "@malika_op" in polls(env)[-1].text
        finally:
            await env.close()
    run(go())


def test_tasks_list_command(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await Office(env).setup()
            at(env, WED, "15:00")
            await env.send_text("/vazifalar")
            assert "Ochiq vazifa yo'q" in dms(env)[-1].text
            await env.send_text("/vazifa")
            assert "Vazifa berish" in dms(env)[-1].text
            env.api.responses.append(parsed("Hisobot", "2026-10-21 16:00"))
            text = "/vazifa @malika_op bugun 16:00 gacha hisobot"
            await env.send_text(text, entities=mention(text, "malika_op"))
            at(env, WED, "17:00")
            await env.send_text("/vazifalar")
            listing = dms(env)[-1].text
            assert "Ochiq vazifalar · 1" in listing and "🔴 21.10 (ch) 16:00" in listing and "Hisobot" in listing
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- Mini App API
def test_tasks_api(tmp_path):
    async def go():
        from test_reports_api import Api

        api = await Api().start(tmp_path)
        try:
            env = api.env
            office = await Office(env).setup()
            await office.say(MALIKA_USER, "salom", ts(WED, "09:00"))
            await env.ctx.reports.store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            empty = await api.call("GET", "/api/report/tasks")
            assert empty["items"] == [] and empty["has_group"] and empty["default_due"] == "2026-10-22T18:00"
            assert {"username": "malika_op", "name": "Malika"} in empty["people"]
            bad = await api.call("POST", "/api/report/tasks", {"who": "@malika_op", "text": "x", "due": "2026-10-20T10:00"}, status=400)
            assert "o'tib ketgan" in bad["message"]
            await api.call("POST", "/api/report/tasks", {"who": "a b", "text": "x"}, status=400)
            made = await api.call("POST", "/api/report/tasks", {"who": "@malika_op", "text": "Hisobot  tayyorlash",
                                                              "due": "2026-10-22T11:00", "repeat": "weekly:3"})
            task = made["task"]
            assert (task["who"], task["text"], task["due_label"], task["repeat_label"], task["group"]) == (
                "Malika", "Hisobot tayyorlash", "22.10 (pa) 11:00", "har payshanba", "Ofis")
            assert task["state"] == "waiting" and task["user_id"] == MALIKA

            leader = (801, "rahbar_1")
            seen = await api.call("GET", "/api/report/tasks", user=leader)  # rahbar ko'radi
            assert len(seen["items"]) == 1 and "people" not in seen
            await api.call("POST", "/api/report/tasks", {"who": "@malika_op", "text": "x"}, user=leader, status=403)
            await api.call("PATCH", f"/api/report/tasks/{task['id']}", {"status": "done"}, user=leader, status=403)

            env.ctx.reports.now = lambda: local(THU, "12:00")
            detail = await api.call("GET", f"/api/report/tasks/{task['id']}")
            assert detail["state"] == "overdue" and detail["history"][0]["label"] == "qayd etildi"
            sent = await api.call("POST", f"/api/report/tasks/{task['id']}/ask", {})
            assert sent == {"ok": True} and "Hisobot tayyorlash" in polls(env)[-1].text
            moved = await api.call("PATCH", f"/api/report/tasks/{task['id']}", {"due": "2026-10-23T16:30"})
            assert moved["task"]["due_label"] == "23.10 (ju) 16:30" and moved["task"]["state"] == "waiting"
            stopped = await api.call("PATCH", f"/api/report/tasks/{task['id']}", {"repeat": ""})
            assert stopped["task"]["repeat"] == ""
            closed = await api.call("PATCH", f"/api/report/tasks/{task['id']}", {"status": "done"})
            assert closed["task"]["status"] == "done" and closed["task"]["closed_label"]
            assert "rahbar yopdi" in env.session.of("EditMessageText")[-1].text  # guruhdagi so'rov yopildi
            assert (await api.call("GET", "/api/report/tasks?status=done"))["items"][0]["id"] == task["id"]
            await api.call("POST", f"/api/report/tasks/{task['id']}/ask", {}, status=409)
            reopened = await api.call("PATCH", f"/api/report/tasks/{task['id']}", {"status": "open"})
            assert reopened["task"]["status"] == "open"
            overview = await api.call("GET", "/api/report/overview")
            assert overview["tasks"] == {"open": 1, "overdue": 0}
        finally:
            await api.close()
    run(go())

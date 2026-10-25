"""v1.7: Mini App «Hisobotlar» API — kirish huquqlari (egasi / rahbar / a'zo) va sozlamalar."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from bot.webapp import WebApp
from test_flow import OWNER, run
from test_reports import CARD_1, LEADERS, LEADS, MALIKA, WED, Group, local, person, start, ts

TOKEN = "123456:TESTTOKEN"
LEADER = 801
MEMBER = 802
STRANGER = 803


def init_data(user_id: int, username: str | None = None) -> str:
    user = {"id": user_id, "first_name": "Odam"}
    if username:
        user["username"] = username
    fields = {"auth_date": str(int(time.time())), "query_id": "AAEqq",
              "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class Api:
    async def start(self, tmp_path):
        self.env = await start(tmp_path, webapp_url="https://bepulkurs.uz/yordamchi/")
        self.web = WebApp(self.env.ctx, self.env.bot)
        self.client = TestClient(TestServer(self.web.app))
        await self.client.start_server()
        return self

    async def close(self):
        await self.client.close()
        await self.env.close()

    async def call(self, method, path, body=None, user=(OWNER, "shohrux"), status=200):
        headers = {"Authorization": "tma " + init_data(*user)}
        res = await self.client.request(method, path, json=body if body is not None else None, headers=headers)
        assert res.status == status, (res.status, await res.text())
        return await res.json()


def test_report_access_by_role(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            store = api.env.ctx.reports.store
            await store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            await api.env.db.add_member(MEMBER, "Ali", "ali")
            leader, member, stranger = (LEADER, "Rahbar_1"), (MEMBER, "ali"), (STRANGER, "begona")

            me = await api.call("GET", "/api/me")
            assert me["access"] == {"ai": True, "report": True} and me["report"] is None  # lidlar guruhi hali yo'q
            leader_me = await api.call("GET", "/api/me", user=leader)
            assert leader_me["access"] == {"ai": False, "report": True} and "settings" not in leader_me
            assert leader_me["user"]["is_owner"] is False
            member_me = await api.call("GET", "/api/me", user=member)
            assert member_me["access"] == {"ai": True, "report": False}
            data = await api.call("GET", "/api/me", user=stranger, status=403)
            assert data["error"] == "no_access" and "parol" in data["message"]

            # rahbar: hisobotni ko'radi, o'zgartira olmaydi, AI bo'limi yopiq
            overview = await api.call("GET", "/api/report/overview", user=leader)
            assert overview["role"] == "leader" and overview["has_groups"] is False
            await api.call("GET", "/api/report/people", user=leader)
            denied = await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@x_user"}, user=leader, status=403)
            assert "egasi" in denied["message"]
            await api.call("GET", "/api/report/settings", user=leader, status=403)
            await api.call("PATCH", "/api/report/schedule", {"time": "18:00"}, user=leader, status=403)
            ai = await api.call("GET", "/api/conversations", user=leader, status=403)
            assert "AI bo'limi" in ai["message"]
            await api.call("GET", "/api/admin", user=leader, status=403)
            # a'zo: AI bor, hisobot yo'q
            closed = await api.call("GET", "/api/report/overview", user=member, status=403)
            assert "Hisobotlar" in closed["message"]
            await api.call("GET", "/api/conversations", user=member)
            # rahbar birinchi kirishda ID bilan bog'landi — username o'zgarsa ham kiradi
            assert (await store.people("leader"))[0].user_id == LEADER
            await api.call("GET", "/api/report/overview", user=(LEADER, "yangi_nom"))
            await api.call("GET", "/api/report/overview", user=(LEADER, None))
        finally:
            await api.close()
    run(go())


def test_people_statuses_schedule_and_groups(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            res = await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@Malika_Op", "name": " Malika  "})
            assert res["person"]["label"] == "Malika"
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "https://t.me/sardor_op"})
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "123456789", "name": "ID bilan"})
            bad = await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@a b"}, status=400)
            assert "Username" in bad["message"]
            await api.call("POST", "/api/report/people", {"kind": "boshliq", "who": "@xx_yy"}, status=400)
            people = await api.call("GET", "/api/report/people")
            assert [(p["username"], p["user_id"], p["label"]) for p in people["operators"]] == [
                ("malika_op", None, "Malika"), ("sardor_op", None, "@sardor_op"), (None, 123456789, "ID bilan")]
            # istisnoga qo'shilsa — operatorlardan olinadi
            await api.call("POST", "/api/report/people", {"kind": "excluded", "who": "sardor_op"})
            people = await api.call("GET", "/api/report/people")
            assert [p["username"] for p in people["operators"]] == ["malika_op", None]
            assert [p["username"] for p in people["excluded"]] == ["sardor_op"]
            malika = people["operators"][0]["id"]
            await api.call("PATCH", f"/api/report/people/{malika}", {"name": "Malika A."})
            await api.call("DELETE", f"/api/report/people/{people['excluded'][0]['id']}")
            await api.call("DELETE", "/api/report/people/9999", status=404)
            people = await api.call("GET", "/api/report/people")
            assert people["operators"][0]["label"] == "Malika A." and people["excluded"] == []

            settings = await api.call("GET", "/api/report/settings")
            assert [s["emoji"] for s in settings["statuses"]] == ["👍", "👎", "🆒", "🤔", "🤝", "💔"]
            assert settings["schedule"]["time"] == "19:00" and settings["schedule"]["days_label"] == "Du–Sh"
            assert settings["groups"] == [] and settings["model"] == "Haiku 5.5"
            assert settings["role_labels"] == {"leads": "Lidlar", "leaders": "Rahbarlar", "tasks": "Vazifalar"}
            fire = await api.call("POST", "/api/report/statuses", {"emoji": "🔥", "label": "juda qiziqdi", "positive": True})
            await api.call("POST", "/api/report/statuses", {"emoji": "🔥", "label": "takror"}, status=409)
            await api.call("POST", "/api/report/statuses", {"emoji": "", "label": "x"}, status=400)
            await api.call("PATCH", f"/api/report/statuses/{fire['status']['id']}", {"label": "qiziqdi", "positive": False})
            dislike = settings["statuses"][1]["id"]
            await api.call("DELETE", f"/api/report/statuses/{dislike}")
            settings = await api.call("GET", "/api/report/settings")
            assert [(s["emoji"], s["label"], s["positive"]) for s in settings["statuses"]][-1] == ("🔥", "qiziqdi", False)
            assert "👎" not in [s["emoji"] for s in settings["statuses"]]
            assert "🔥" in (await api.env.ctx.reports.known_statuses()).values()  # kesh yangilandi

            res = await api.call("PATCH", "/api/report/schedule", {"time": "18:30", "days": [0, 1, 2, 3, 4], "remind_minutes": 15})
            assert res["schedule"]["time"] == "18:30" and res["schedule"]["days_label"] == "Du–Ju"
            await api.call("PATCH", "/api/report/schedule", {"days": []}, status=400)
            await api.call("PATCH", "/api/report/schedule", {}, status=400)
            assert (await api.env.ctx.reports.schedule()).remind_minutes == 15

            g = Group(api.env)
            await g.join(LEADS, "Lidlar guruhi")
            settings = await api.call("GET", "/api/report/settings")
            assert [(x["chat_id"], x["roles"], x["active"]) for x in settings["groups"]] == [(LEADS, [], True)]
            res = await api.call("PATCH", f"/api/report/groups/{LEADS}", {"roles": ["leads", "nimadir"]})
            assert res["roles"] == ["leads"] and res["is_admin"] is True
            await api.call("PATCH", "/api/report/groups/12345", {"roles": []}, status=404)
            res = await api.call("PATCH", f"/api/report/groups/{LEADS}", {"active": False})
            assert res["active"] is False
        finally:
            await api.close()
    run(go())


def test_overview_payload_and_send_now(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            env = api.env
            await api.call("POST", "/api/report/send", {}, status=409)  # lidlar guruhi yo'q
            g = Group(env)
            await g.join(LEADS, "Lidlar")
            await api.call("PATCH", f"/api/report/groups/{LEADS}", {"roles": ["leads"]})
            await g.join(LEADERS, "Rahbarlar")
            await api.call("PATCH", f"/api/report/groups/{LEADERS}", {"roles": ["leaders"]})
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@malika_op", "name": "Malika"})
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.react(lead["message_id"], person(MALIKA, "Malika", "malika_op"), "👍", ts(WED, "09:30"))

            data = await api.call("GET", "/api/report/overview")
            assert data["day"] == "2026-10-21" and data["live"] is True and data["next"] is None
            assert data["prev"] == "2026-10-20" and data["label"] == "21-oktabr, chorshanba"
            assert data["start"] == "2026-10-20T19:00" and data["end"] == "2026-10-21T19:00"
            assert data["rows"][0]["name"] == "Malika" and data["rows"][0]["counts"] == {"👍": 1}
            assert data["rows"][0]["positive"] == 1 and data["totals"]["total"] == 1
            assert data["leads"] == {"new": 1, "unmarked": 0} and data["role"] == "owner" and data["has_groups"]
            assert data["columns"] == ["👍", "👎", "🆒", "🤔", "🤝", "💔"] and data["sent"] is False
            assert data["courses"] == [{"course": "kompyuter savodxonligi", "total": 1, "positive": 1}]
            old = await api.call("GET", "/api/report/overview?day=2026-10-20")
            assert old["live"] is False and old["next"] == "2026-10-21" and old["rows"][0]["total"] == 0
            future = await api.call("GET", "/api/report/overview?day=2030-01-01")
            assert future["day"] == "2026-10-21"
            await api.call("GET", "/api/report/overview?day=21.10", status=400)

            me = await api.call("GET", "/api/me")
            assert me["report"] == {"calls": 1, "positive": 1, "positive_emoji": "👍", "new_leads": 1, "unmarked": 0,
                                    "tasks_open": 0, "tasks_overdue": 0}

            env.session.clear()
            res = await api.call("POST", "/api/report/send", {})
            assert res == {"ok": True, "sent": 1, "target": "leaders"}
            msg = env.session.of("SendMessage")[-1]
            assert msg.chat_id == LEADERS and "HISOBOT (oraliq)" in msg.text
            assert not await env.ctx.reports.store.report_for_day("2026-10-21")  # oraliq hisobot davrni yopmaydi

            # kunlik hisobot yuborilgach — sabablar guruhlangan holda ko'rinadi
            await env.ctx.reports.tick(env.bot, local(WED, "19:02"))
            env.ctx.reports.now = lambda: local(WED, "19:05")
            data = await api.call("GET", "/api/report/overview?day=2026-10-21")
            assert data["sent"] is True and data["live"] is False and data["next"] == "2026-10-22"
            # 19:00 dan yarim tungacha standart ko'rinish — hozirgina tugagan bugungi davr (keyingisi hali bo'sh)
            current = await api.call("GET", "/api/report/overview")
            assert current["day"] == "2026-10-21" and current["live"] is False and current["next"] == "2026-10-22"
            upcoming = await api.call("GET", "/api/report/overview?day=2026-10-22")
            assert upcoming["live"] is True and upcoming["rows"][0]["total"] == 0 and upcoming["next"] is None
        finally:
            await api.close()
    run(go())

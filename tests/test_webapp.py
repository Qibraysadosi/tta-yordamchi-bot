"""Mini App (v1.5): initData tekshiruvi va JSON API — haqiqiy aiohttp server + soxta Telegram/Anthropic."""
import hashlib
import hmac
import io
import json
import time
from urllib.parse import urlencode

import docx
import pytest
from aiohttp.test_utils import TestClient, TestServer

import fakes
from bot.filestore import FileStore
from bot.webapp import AuthError, WebApp, validate_init_data
from test_flow import OWNER, Env, run

TOKEN = "123456:TESTTOKEN"
MEMBER = 424242
STRANGER = 777


def init_data(user_id: int, token: str = TOKEN, auth_date: int | None = None, name: str = "Shohrux") -> str:
    fields = {
        "auth_date": str(int(auth_date or time.time())), "query_id": "AAEqq",
        "user": json.dumps({"id": user_id, "first_name": name, "username": "shohrux"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_validate_init_data():
    data = validate_init_data(init_data(5), TOKEN)
    assert data["user"]["id"] == 5 and data["query_id"] == "AAEqq"
    with pytest.raises(AuthError, match="imzo"):
        validate_init_data(init_data(5, token="1:OTHER"), TOKEN)
    with pytest.raises(AuthError, match="eskirgan"):
        validate_init_data(init_data(5, auth_date=int(time.time()) - 100_000), TOKEN)
    with pytest.raises(AuthError):
        validate_init_data("", TOKEN)
    with pytest.raises(AuthError, match="hash"):
        validate_init_data("auth_date=1&user=%7B%7D", TOKEN)
    tampered = init_data(5).replace("Shohrux", "Hacker")
    with pytest.raises(AuthError, match="imzo"):
        validate_init_data(tampered, TOKEN)


class App:
    """Soxta Telegram sessiyali Env + Mini App serveri."""

    async def start(self, tmp_path, responses=None, **over):
        self.env = await Env().start(tmp_path, responses, webapp_url="https://bepulkurs.uz/yordamchi/", **over)
        self.env.ctx.files = FileStore(self.env.db, tmp_path / "data" / "files", 50 * 1024 * 1024)
        self.web = WebApp(self.env.ctx, self.env.bot)
        self.client = TestClient(TestServer(self.web.app))
        await self.client.start_server()
        return self

    async def close(self):
        await self.client.close()
        await self.env.close()

    def headers(self, user_id=OWNER):
        return {"Authorization": "tma " + init_data(user_id)}

    async def get(self, path, user_id=OWNER, status=200):
        res = await self.client.get(path, headers=self.headers(user_id))
        assert res.status == status, (res.status, await res.text())
        return await res.json()

    async def post(self, path, body=None, user_id=OWNER, status=200, method="POST"):
        res = await self.client.request(method, path, json=body or {}, headers=self.headers(user_id))
        assert res.status == status, (res.status, await res.text())
        return await res.json()


def test_auth_and_me(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        try:
            res = await app.client.get("/api/me")
            assert res.status == 401
            res = await app.client.get("/api/me", headers={"Authorization": "tma auth_date=1&hash=zz"})
            assert res.status == 401 and "tekshiruvi" in (await res.json())["message"]
            data = await app.get("/api/me", user_id=STRANGER, status=403)
            assert data["error"] == "no_access" and "parol" in data["message"]

            me = await app.get("/api/me")
            assert me["user"] == {"id": OWNER, "name": "Shohrux", "username": "shohrux", "is_owner": True}
            assert me["version"] == "1.1.0" and me["settings"]["model"] == "claude-opus-5-5"
            assert [m["id"] for m in me["models"]][:2] == ["auto", "claude-fable-5-1"]
            assert me["modes"][0]["key"] == "oddiy" and me["budget"]["monthly"] == 100.0
            assert me["conversation"]["title"] == "Yangi suhbat" and me["webapp_url"].endswith("/yordamchi/")

            # a'zo: kirgan bo'lsa — kiradi, admin bo'limi yopiq, chiqarilsa — 403
            await app.env.db.add_member(MEMBER, "Ali", "ali")
            member_me = await app.get("/api/me", user_id=MEMBER)
            assert member_me["user"]["is_owner"] is False
            await app.get("/api/admin", user_id=MEMBER, status=403)
            await app.env.db.set_member_active(MEMBER, False)
            data = await app.get("/api/me", user_id=MEMBER, status=403)
            assert "yopgan" in data["message"]

            page = await app.client.get("/")
            assert page.status == 200 and "telegram-web-app.js" in await page.text() and "v=1.1.0" in await page.text()
            assert "Content-Security-Policy" in page.headers
            health = await app.client.get("/health")
            assert (await health.json())["ok"] is True
            static = await app.client.get("/static/app.js")
            assert static.status == 200 and "api/me" in await static.text()
        finally:
            await app.close()
    run(go())


def test_settings_patch(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        try:
            data = await app.post("/api/settings", {"model": "auto", "effort": "high", "web_search": False, "mode": "rasmiy_xat", "show_cost": False}, method="PATCH")
            s = data["settings"]
            assert s["model"] == "auto" and s["effort"] == "high" and s["web_search"] is False and s["mode"] == "rasmiy_xat" and s["show_cost"] is False
            stored = await app.env.ctx.user_settings(OWNER)
            assert stored.model == "auto" and stored.mode == "rasmiy_xat"
            data = await app.post("/api/settings", {"effort": "default"}, method="PATCH")
            assert data["settings"]["effort"] is None
            err = await app.post("/api/settings", {"model": "gpt-9"}, method="PATCH", status=400)
            assert "Noma'lum model" in err["message"]
            await app.post("/api/settings", {"mode": "yoq"}, method="PATCH", status=400)
            await app.post("/api/settings", {}, method="PATCH", status=400)
        finally:
            await app.close()
    run(go())


def test_conversations_ask_and_resume(tmp_path):
    async def go():
        app = await App().start(tmp_path, [
            fakes.simple_answer("Birinchi javob."),
            fakes.simple_answer("Ilovadan kelgan savolga javob."),
        ])
        env = app.env
        try:
            await env.send_text("Birinchi savol")
            data = await app.get("/api/conversations")
            assert data["total"] == 1 and data["current"] == 1 and data["items"][0]["title"] == "Birinchi savol"
            detail = await app.get("/api/conversations/1")
            assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
            assert detail["messages"][1]["text"] == "Birinchi javob."
            await app.get("/api/conversations/99", status=404)

            # ilovadan savol: chatga «Ilovadan savol» xabari, keyin javob
            res = await app.post("/api/ask", {"text": "Ilovadan savol"})
            assert res["ok"] is True
            for _ in range(50):
                if any("Ilovadan kelgan savolga javob" in t for _, t in env.session.outgoing()):
                    break
                await __import__("asyncio").sleep(0.05)
            texts = [t for _, t in env.session.outgoing()]
            assert any("📨 <b>Ilovadan savol:</b>\nIlovadan savol" == t for t in texts)
            assert any("Ilovadan kelgan savolga javob" in t for t in texts)
            hist = await env.db.history(OWNER, 1, 0)
            assert len(hist) == 4 and hist[2].content[0]["text"] == "Ilovadan savol"
            await app.post("/api/ask", {"text": ""}, status=400)

            # yangi suhbat va qaytish
            data = await app.post("/api/conversations/new")
            assert data["settings"]["conv_id"] == 2
            assert "Yangi suhbat boshlandi" in env.session.last_out()
            data = await app.post("/api/conversations/1/resume")
            assert data["conversation"]["title"] == "Birinchi savol"
            assert (await env.ctx.user_settings(OWNER)).conv_id == 1
            assert "davom ettirilmoqda" in env.session.last_out()
            await app.post("/api/conversations/55/resume", status=404)
            search = await app.get("/api/conversations?q=birinchi")
            assert search["total"] == 1
            assert (await app.get("/api/conversations?q=yoqnarsa"))["items"] == []
        finally:
            await app.close()
    run(go())


def test_files_gallery(tmp_path):
    async def go():
        app = await App().start(tmp_path, [
            fakes.tool_answer("create_document", {"format": "docx", "filename": "Hisobot", "content": "# A\n\nmatn"}),
            fakes.simple_answer("Tayyor."),
        ])
        env = app.env
        try:
            await env.send_text("Word qil")
            data = await app.get("/api/files")
            assert data["total"] == 1
            item = data["items"][0]
            assert item["name"] == "Hisobot.docx" and item["kind"] == "docx" and item["downloadable"] and item["sendable"]
            assert item["conv_title"] == "Word qil"
            stored = await env.db.file(item["id"])
            assert stored.file_id.startswith("doc-") and (tmp_path / "data" / "files" / stored.path).exists()

            await app.post(f"/api/files/{item['id']}/send")
            assert env.session.resent == [stored.file_id]  # file_id bilan, qayta yuklamasdan

            link = await app.get(f"/api/files/{item['id']}/link")
            assert link["url"].startswith("https://bepulkurs.uz/yordamchi/files/") and link["name"] == "Hisobot.docx"
            path = link["url"].split("/yordamchi", 1)[1]
            res = await app.client.get(path)
            assert res.status == 200 and (await res.read())[:2] == b"PK"
            assert 'filename="Hisobot.docx"' in res.headers["Content-Disposition"]
            bad = await app.client.get(path.replace("&s=", "&s=0"))
            assert bad.status == 403
            await app.get(f"/api/files/{item['id']}/link", user_id=MEMBER, status=403)  # a'zo emas

            await app.env.db.add_member(MEMBER, "Ali", None)
            await app.get(f"/api/files/{item['id']}/link", user_id=MEMBER, status=404)  # boshqa odamning fayli
            await app.post(f"/api/files/{item['id']}", method="DELETE")
            assert (await app.get("/api/files"))["total"] == 0 and not (tmp_path / "data" / "files" / stored.path).exists()
        finally:
            await app.close()
    run(go())


def test_file_store_prunes_and_word_button(tmp_path):
    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Javob matni.")])
        env = app.env
        store = FileStore(env.db, tmp_path / "data" / "files", 10)  # 10 bayt chegara
        env.ctx.files = store
        try:
            await store.save(OWNER, 1, "a.txt", b"123456", "f1")
            second = await store.save(OWNER, 1, "b.txt", b"123456", "f2")
            items, _ = await env.db.files(OWNER)
            assert [i.path is not None for i in items] == [True, False]  # eng eskisi diskdan o'chdi, yozuv qoldi
            assert store.read(await env.db.file(second)) == b"123456"

            await env.send_text("Savol")
            await env.press("ans:word:2")
            items, total = await env.db.files(OWNER)
            assert total == 3 and items[0].name == "javob-2.docx" and items[0].kind == "docx"
        finally:
            await app.close()
    run(go())


def test_stats_templates_and_admin(tmp_path):
    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Javob.")])
        env = app.env
        try:
            await env.send_text("Savol")
            await env.db.add_member(MEMBER, "Ali", "ali")
            await env.db.log_usage(MEMBER, "claude-haiku-5-5", {"input": 100, "output": 50}, 0.02)
            stats = await app.get("/api/stats")
            assert len(stats["days"]) == 30 and stats["days"][-1]["requests"] == 1 and stats["days"][-1]["cost"] > 0
            assert stats["totals"]["today_requests"] == 1 and stats["by_model"][0]["label"] == "Opus 5.5"
            assert stats["everyone"]["cycle"] >= 0.02 and {u["name"] for u in stats["everyone"]["by_user"]} == {"Siz", "Ali @ali"}
            member_stats = await app.get("/api/stats", user_id=MEMBER)
            assert "everyone" not in member_stats and member_stats["totals"]["cycle"] == 0.02

            data = await app.post("/api/templates", {"name": "Post", "text": "Instagram post yoz"})
            tid = data["item"]["id"]
            assert (await app.get("/api/templates"))["items"][0]["name"] == "Post"
            await app.post("/api/templates", {"name": "", "text": "x"}, status=400)
            await app.get("/api/templates", user_id=MEMBER)
            await app.post(f"/api/templates/{tid}", method="DELETE", user_id=MEMBER, status=404)  # boshqaning shabloni
            await app.post(f"/api/templates/{tid}", method="DELETE")
            assert (await app.get("/api/templates"))["items"] == []

            admin = await app.get("/api/admin")
            assert admin["password_set"] is False and admin["members"][0]["name"] == "Ali" and admin["version"] == "1.1.0"
            assert admin["backups"] == [] and admin["budget"]["monthly"] == 100.0
            await app.post("/api/admin/password", {"password": "123"}, status=400)
            await app.post("/api/admin/password", {"password": "Sinov@2026"})
            assert (await app.get("/api/admin"))["password_set"] is True
            from bot.access import verify_password
            assert verify_password("Sinov@2026", await env.db.get_config("password_hash"))
            await app.post("/api/admin/password", {"remove": True})
            assert (await app.get("/api/admin"))["password_set"] is False

            await app.post(f"/api/admin/members/{MEMBER}", {"active": False})
            assert (await env.db.member(MEMBER)).active is False
            await app.get("/api/me", user_id=MEMBER, status=403)
            await app.post(f"/api/admin/members/{MEMBER}", {"active": True})
            await app.get("/api/me", user_id=MEMBER)
            await app.post("/api/admin/members/9", {"active": True}, status=404)

            await app.post("/api/admin/backup")
            assert env.session.documents[-1][0].startswith("backup-") and env.session.documents[-1][0].endswith(".db")
            err = await app.post("/api/admin/restore", status=409)
            assert "zaxira" in err["message"].lower() or "yo'q" in err["message"].lower()
        finally:
            await app.close()
    run(go())


def test_restore_from_app_sends_confirmation(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        env = app.env
        try:
            # oldingi versiya zaxirasi: haqiqiy yangilanishni taqlid qilamiz
            backups = tmp_path / "data" / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            import shutil
            target = backups / "20260101-000000-v1.0.0"
            shutil.copytree(env.app_dir / "bot", target / "bot")
            shutil.copytree(env.app_dir / "prompts", target / "prompts")
            shutil.copy2(env.app_dir / "requirements.txt", target / "requirements.txt")
            (target / "VERSION").write_text("1.0.0\n")
            admin = await app.get("/api/admin")
            assert admin["backups"][0]["version"] == "1.0.0"
            res = await app.post("/api/admin/restore")
            assert res["version"] == "1.0.0"
            last = env.session.of("SendMessage")[-1]
            assert "v1.1.0 → <b>v1.0.0</b>" in last.text and last.reply_markup.inline_keyboard[0][0].callback_data.startswith("up:")
            assert OWNER in env.ctx.staged
        finally:
            await app.close()
    run(go())


def test_docx_sanity():
    buf = io.BytesIO()
    docx.Document().save(buf)
    assert buf.getvalue()[:2] == b"PK"


def test_bot_side_app_button_and_command(tmp_path):
    async def go():
        env = await Env().start(tmp_path, webapp_url="https://bepulkurs.uz/yordamchi/")
        try:
            await env.send_text("/start")
            photo = env.session.of("SendPhoto")[-1]
            last_row = photo.reply_markup.inline_keyboard[-1]
            assert last_row[0].text == "📱 Ilova" and last_row[0].web_app.url == "https://bepulkurs.uz/yordamchi/"
            assert last_row[1].callback_data == "menu:admin"
            await env.send_text("/ilova")
            msg = env.session.of("SendMessage")[-1]
            assert "ilovasi" in msg.text and msg.reply_markup.inline_keyboard[0][0].web_app.url.endswith("/yordamchi/")
        finally:
            await env.close()

        env = await Env().start(tmp_path / "b")
        try:
            await env.send_text("/start")
            photo = env.session.of("SendPhoto")[-1]
            assert all(b.web_app is None for row in photo.reply_markup.inline_keyboard for b in row)
            await env.send_text("/ilova")
            assert "WEBAPP_URL" in env.session.of("SendMessage")[-1].text
        finally:
            await env.close()
    run(go())


def test_config_webapp_url(monkeypatch):
    from bot.config import ConfigError, load_settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:T")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OWNER_IDS", "5")
    monkeypatch.setenv("WEBAPP_URL", "https://bepulkurs.uz/yordamchi")
    assert load_settings().webapp_url == "https://bepulkurs.uz/yordamchi/"
    monkeypatch.setenv("WEBAPP_URL", "off")
    assert load_settings().webapp_url is None
    monkeypatch.setenv("WEBAPP_URL", "http://bepulkurs.uz/x")
    with pytest.raises(ConfigError, match="https"):
        load_settings()

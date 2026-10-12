"""Uchidan-uchiga sinov: haqiqiy launcher.py + haqiqiy `python -m bot` + soxta Telegram serveri.

Tekshiriladi: parol, /yangilash (.zip → tasdiq → 75 kodi → qayta ishga tushish → xabar),
buzuq yangilanishda avtomatik tiklash, /qaytarish, SIGTERM bilan to'xtash.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import signal
import sys
import time
import zipfile
from pathlib import Path

from aiohttp import web

PROJECT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TOKEN = "123456:TESTtokenTESTtokenTESTtokenTEST1234"
OWNER = 5591487950
GUEST = 777


class FakeTelegram:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.update_id = 5000
        self.calls: list[tuple[str, dict]] = []
        self.files: dict[str, bytes] = {}
        self.message_id = 100
        self.event = asyncio.Event()
        self.cursor = 0

    # --- foydalanuvchi harakatlari
    def push(self, payload: dict) -> None:
        self.update_id += 1
        self.updates.append({"update_id": self.update_id, **payload})
        self.event.set()

    def _message(self, user_id: int, **fields) -> dict:
        self.message_id += 1
        return {
            "message_id": self.message_id, "date": int(time.time()),
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Sinov" if user_id != OWNER else "Egasi"},
            **fields,
        }

    def text(self, text: str, user_id: int = OWNER) -> None:
        self.push({"message": self._message(user_id, text=text)})

    def document(self, data: bytes, name: str, caption: str | None = None) -> None:
        file_id = f"doc{len(self.files)}"
        self.files[file_id] = data
        fields = {"document": {"file_id": file_id, "file_unique_id": "u" + file_id, "file_name": name,
                               "mime_type": "application/zip", "file_size": len(data)}}
        if caption:
            fields["caption"] = caption
        self.push({"message": self._message(OWNER, **fields)})

    def press(self, data: str, message_id: int, photo: bool = False) -> None:
        message = {"message_id": message_id, "date": int(time.time()), "chat": {"id": OWNER, "type": "private"}}
        if photo:
            message.update(caption="...", photo=[{"file_id": "p", "file_unique_id": "pu", "width": 1280, "height": 720}])
        else:
            message["text"] = "..."
        self.push({"callback_query": {
            "id": f"cb{self.update_id}", "from": {"id": OWNER, "is_bot": False, "first_name": "Egasi"},
            "chat_instance": "ci", "data": data, "message": message,
        }})

    # --- kutish
    async def wait(self, predicate, what: str, timeout: float = 40) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while self.cursor < len(self.calls):
                method, params = self.calls[self.cursor]
                self.cursor += 1
                if predicate(method, params):
                    return params
            await asyncio.sleep(0.05)
        raise AssertionError(f"Kutilgan narsa kelmadi: {what}")

    async def wait_text(self, needle: str, chat_id: int = OWNER, timeout: float = 40) -> dict:
        return await self.wait(
            lambda m, p: m in ("sendMessage", "editMessageText", "sendPhoto", "editMessageCaption")
            and needle in p.get("text", "")
            and int(p.get("chat_id", 0)) == chat_id,
            f"{needle!r} → {chat_id}", timeout,
        )

    # --- HTTP
    async def api(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        form = await request.post()
        params = {k: (v if isinstance(v, str) else v) for k, v in form.items()}
        if method == "getUpdates":
            return self._ok(await self._get_updates(params))
        self.calls.append((method, {k: v for k, v in params.items() if isinstance(v, str)}))
        now = int(time.time())
        if method == "getMe":
            return self._ok({"id": 999, "is_bot": True, "first_name": "Bot", "username": "tta_test_bot"})
        if method in ("sendMessage", "sendDocument"):
            self.message_id += 1
            self.calls[-1][1]["message_id"] = str(self.message_id)
            msg = {"message_id": self.message_id, "date": now,
                   "chat": {"id": int(params["chat_id"]), "type": "private"}}
            if method == "sendMessage":
                msg["text"] = params["text"]
            else:
                upload = params["document"]
                if isinstance(upload, str) and upload.startswith("attach://"):
                    upload = params[upload.removeprefix("attach://")]
                data = upload.file.read()
                self.files["sent-" + upload.filename] = data
                msg["document"] = {"file_id": "x", "file_unique_id": "x", "file_name": upload.filename}
            return self._ok(msg)
        if method == "sendPhoto":
            self.message_id += 1
            photo = params["photo"]
            uploaded = isinstance(photo, str) and photo.startswith("attach://")
            if uploaded:
                upload = params[photo.removeprefix("attach://")]
                self.files["photo-" + str(self.message_id)] = upload.file.read()
            self.calls[-1][1].update(message_id=str(self.message_id), text=params.get("caption", ""),
                                     uploaded=str(uploaded))
            return self._ok({"message_id": self.message_id, "date": now,
                             "chat": {"id": int(params["chat_id"]), "type": "private"},
                             "caption": params.get("caption", ""),
                             "photo": [{"file_id": f"photo-{self.message_id}", "file_unique_id": "u",
                                        "width": 1280, "height": 720}]})
        if method == "setMyProfilePhoto":
            spec = json.loads(params["photo"])
            assert spec["type"] == "static" and spec["photo"].startswith("attach://"), spec
            data = params[spec["photo"].removeprefix("attach://")].file.read()
            assert data[:3] == b"\xff\xd8\xff", "profil rasmi JPEG emas"
            return self._ok(True)
        if method == "editMessageCaption":
            self.calls[-1][1]["text"] = params.get("caption", "")
            return self._ok({"message_id": int(params["message_id"]), "date": now,
                             "chat": {"id": int(params["chat_id"]), "type": "private"},
                             "caption": params.get("caption", "")})
        if method == "editMessageText":
            return self._ok({"message_id": int(params["message_id"]), "date": now,
                             "chat": {"id": int(params["chat_id"]), "type": "private"}, "text": params["text"]})
        if method == "getFile":
            return self._ok({"file_id": params["file_id"], "file_unique_id": "u", "file_path": params["file_id"]})
        return self._ok(True)

    async def _get_updates(self, params: dict) -> list[dict]:
        offset = int(params.get("offset") or 0)
        if offset:
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
        deadline = time.monotonic() + min(float(params.get("timeout") or 0), 1.0)
        while not self.updates and time.monotonic() < deadline:
            self.event.clear()
            try:
                await asyncio.wait_for(self.event.wait(), max(deadline - time.monotonic(), 0.01))
            except asyncio.TimeoutError:
                break
        return list(self.updates[:100])

    async def file(self, request: web.Request) -> web.Response:
        return web.Response(body=self.files[request.match_info["path"]])

    @staticmethod
    def _ok(result) -> web.Response:
        return web.json_response({"ok": True, "result": result})


def make_zip(app: Path, version: str, *, marker: str = "", broken: bool = False, notes: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in ("bot", "prompts"):
            for f in sorted((app / item).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    data = f.read_bytes()
                    if f.name == "models.py" and marker:
                        data += f"\nRELEASE_MARKER = {marker!r}\n".encode()
                    if f.name == "app.py" and broken:
                        data = data.replace(b"from .claude import", b"import nonexistent_module_xyz  # ataylab\nfrom .claude import", 1)
                    zf.writestr(f"claude-telegram-bot/{f.relative_to(app).as_posix()}", data)
        zf.writestr("claude-telegram-bot/requirements.txt", (app / "requirements.txt").read_bytes())
        zf.writestr("claude-telegram-bot/VERSION", version + "\n")
        if notes:
            zf.writestr("claude-telegram-bot/UPDATE_NOTES.md", notes)
    return buf.getvalue()


def keyboard_data(params: dict) -> tuple[str, str]:
    markup = json.loads(params["reply_markup"])
    ok, no = markup["inline_keyboard"][0]
    return ok["callback_data"], no["callback_data"]


async def main() -> None:
    work = HERE / "run"
    shutil.rmtree(work, ignore_errors=True)
    app_dir = work / "app"
    app_dir.mkdir(parents=True)
    for item in ("bot", "prompts"):
        shutil.copytree(PROJECT / item, app_dir / item, ignore=shutil.ignore_patterns("__pycache__"))
    for item in ("requirements.txt", "launcher.py"):
        shutil.copy2(PROJECT / item, app_dir / item)
    (app_dir / "VERSION").write_text("1.1.0\n")
    stamp = work / "requirements.sha256"
    stamp.write_text(hashlib.sha256((app_dir / "requirements.txt").read_bytes()).hexdigest())

    tg = FakeTelegram()
    web_app = web.Application(client_max_size=50 * 1024 * 1024)
    web_app.router.add_post("/bot{token}/{method}", tg.api)
    web_app.router.add_get("/file/bot{token}/{path:.*}", tg.file)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(
        FAKE_TG=f"http://127.0.0.1:{port}", APP_DIR=str(app_dir), REQ_STAMP=str(stamp),
        BOT_COMMAND=f"{sys.executable} {HERE / 'wrapper.py'}", TELEGRAM_BOT_TOKEN=TOKEN,
        ANTHROPIC_API_KEY="sk-ant-api03-test", OWNER_IDS=str(OWNER), PYTHONUNBUFFERED="1", BACKUP_TIME="off",
        PYTHONDONTWRITEBYTECODE="1",
    )
    log_file = open(work / "launcher.log", "w")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(app_dir / "launcher.py"), cwd=app_dir, env=env, stdout=log_file, stderr=log_file
    )
    step = lambda text: print(f"✔ {text}", flush=True)  # noqa: E731
    try:
        await tg.wait(lambda m, p: m == "setMyCommands" and "scope" in p, "egasi menyusi")
        step("v1.1.0 ishga tushdi, egasi uchun alohida menyu o'rnatildi")

        # parol
        tg.text("salom", GUEST)
        await tg.wait_text("Bu shaxsiy bot", GUEST)
        tg.text("/parol Test#12345")
        await tg.wait(lambda m, p: m == "deleteMessage", "parol xabari o'chirildi")
        await tg.wait_text("Parol o'rnatildi")
        tg.text("Salom", GUEST)
        await tg.wait_text("parol bilan himoyalangan", GUEST)  # rasm (banner) izohida
        tg.text("Test#12345", GUEST)
        await tg.wait_text("xush kelibsiz", GUEST)
        await tg.wait_text("Salom, Sinov!", GUEST)  # kirgandan keyin xush kelibsiz rasmi
        await tg.wait_text("Yangi foydalanuvchi parol bilan kirdi")
        step("parol: begona rad etildi, banner bilan parol so'raldi, kirgach xush kelibsiz rasmi chiqdi")

        profile = [m for m, _ in tg.calls if m in ("setMyDescription", "setMyShortDescription", "setMyProfilePhoto")]
        assert sorted(profile) == ["setMyDescription", "setMyProfilePhoto", "setMyShortDescription"], profile
        step("profil: tavsif, qisqa tavsif va profil rasmi (JPEG, attach orqali) o'rnatildi")

        tg.text("/start")
        start = await tg.wait_text("Salom, Egasi!")
        buttons = [b["callback_data"] for row in json.loads(start["reply_markup"])["inline_keyboard"] for b in row]
        assert "menu:admin" in buttons and start["uploaded"] == "False", (buttons, start["uploaded"])
        tg.press("menu:search", int(start["message_id"]), photo=True)
        await tg.wait(lambda m, p: m == "editMessageCaption" and "qidiruv: o'chiq" in p.get("text", ""), "qidiruv tugmasi")
        tg.press("menu:admin", int(start["message_id"]), photo=True)
        await tg.wait_text("Parol: o'rnatilgan ✅")
        step("/start: banner keshdan (qayta yuklanmadi), tugmalar va admin panel ishlaydi")

        # 1) to'g'ri yangilanish
        tg.text("/yangilash")
        await tg.wait_text("Yangilanish arxivini (.zip)")
        tg.document(make_zip(app_dir, "1.2.0", marker="v12", notes="Sinov: yangi versiya"), "update-1.2.0.zip")
        summary = await tg.wait_text("v1.1.0 → <b>v1.2.0</b>")
        ok, _ = keyboard_data(summary)
        tg.press(ok, int(summary["message_id"]))
        await tg.wait_text("v1.2.0 o'rnatildi")
        notice = await tg.wait_text("✅ Bot yangilandi: v1.1.0 → v1.2.0")
        assert "Sinov: yangi versiya" in notice["text"]
        assert "RELEASE_MARKER = 'v12'" in (app_dir / "bot" / "models.py").read_text()
        step("/yangilash: arxiv tekshirildi → tasdiq → qayta ishga tushdi → «✅ Bot yangilandi» keldi")
        await asyncio.sleep(1.5)
        stale = [p for m, p in tg.calls if m == "answerCallbackQuery" and "eskirgan" in p.get("text", "")]
        assert not stale, "tasdiq tugmasi yangi versiyaga qayta kelib qoldi"
        step("tasdiq tugmasi yangi versiyaga qayta kelmadi (offset tasdiqlangan)")

        # 2) buzuq yangilanish → avtomatik tiklash
        tg.document(make_zip(app_dir, "1.3.0", broken=True), "update-1.3.0.zip", caption="/yangilash")
        summary = await tg.wait_text("v1.2.0 → <b>v1.3.0</b>")
        ok, _ = keyboard_data(summary)
        tg.press(ok, int(summary["message_id"]))
        failure = await tg.wait_text("❌ v1.3.0 ishga tushmadi — v1.2.0 avtomatik tiklandi", timeout=60)
        assert "nonexistent_module_xyz" in failure["text"]
        assert "nonexistent_module_xyz" not in (app_dir / "bot" / "app.py").read_text()
        step("buzuq v1.3.0: yiqildi → launcher v1.2.0 ni tikladi → egasiga xato matni bilan xabar keldi")

        tg.text("/versiya")
        versions = await tg.wait_text("Versiya: <b>v1.2.0</b>")
        assert "• v1.1.0" in versions["text"] and "v1.3.0" not in versions["text"]
        step("/versiya: v1.2.0, qaytish uchun v1.1.0 zaxirasi bor")

        # 3) /qaytarish
        tg.text("/qaytarish")
        confirm = await tg.wait_text("v1.2.0 → <b>v1.1.0</b>")
        ok, _ = keyboard_data(confirm)
        tg.press(ok, int(confirm["message_id"]))
        await tg.wait_text("↩️ Oldingi versiya tiklandi: v1.2.0 → v1.1.0")
        assert "RELEASE_MARKER" not in (app_dir / "bot" / "models.py").read_text()
        step("/qaytarish: v1.1.0 ga qaytdi")
        profile = [m for m, _ in tg.calls if m in ("setMyDescription", "setMyShortDescription", "setMyProfilePhoto")]
        assert len(profile) == 3, profile
        step("3 marta qayta ishga tushishdan keyin ham profil rasmi qayta yuklanmadi")

        # a'zo qayta ishga tushishdan keyin ham a'zo
        tg.text("/start", GUEST)
        start = await tg.wait_text("Salom, Sinov!", GUEST)
        buttons = [b["callback_data"] for row in json.loads(start["reply_markup"])["inline_keyboard"] for b in row]
        assert "menu:admin" not in buttons, buttons
        step("parol bilan kirgan foydalanuvchi qayta ishga tushishlardan keyin ham kira oladi")

        # /kod
        tg.text("/tarix")
        await tg.wait_text("Suhbatlar tarixi")
        tg.text("/rejim")
        rejim = await tg.wait_text("Ish rejimi")
        tg.press("mode:tarjimon", int(rejim["message_id"]))
        await tg.wait_text("Tanlandi: <b>🌐 Tarjimon</b>")
        tg.text("/kirill Salom dunyo")
        await tg.wait_text("Салом дунё")
        step("/tarix, /rejim (tanlash) va /kirill ishladi")

        tg.text("/kod")
        await tg.wait(lambda m, p: m == "sendDocument", "/kod arxivi")
        sent = tg.files["sent-claude-telegram-bot-v1.1.0.zip"]
        names = zipfile.ZipFile(io.BytesIO(sent)).namelist()
        assert "claude-telegram-bot/launcher.py" in names and not any("data/" in n for n in names)
        step("/kod: joriy kod .zip qilib yuborildi (data/ va .env siz)")

        proc.send_signal(signal.SIGTERM)
        code = await asyncio.wait_for(proc.wait(), 20)
        assert code == 0, code
        step("SIGTERM: bot va launcher toza to'xtadi (kod 0)")
        print("\nHAMMASI O'TDI ✅")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        log_file.close()
        await runner.cleanup()
        (work / "calls.json").write_text(json.dumps(tg.calls, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\n❌ {exc}\n--- launcher.log (oxiri) ---")
        print("".join((HERE / "run" / "launcher.log").read_text().splitlines(keepends=True)[-60:]))
        sys.exit(1)

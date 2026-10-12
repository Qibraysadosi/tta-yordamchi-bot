"""Parol bilan kirish, a'zolar, /yangilash, /qaytarish, /kod va launcher sinovlari."""
import asyncio
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from aiogram import Bot
from aiogram.types import Update

import fakes
from bot import app as bot_app
from bot.access import AttemptLimiter, hash_password, verify_password
from bot.config import ConfigError
from bot.updater import UpdateError, UpdateManager
from test_flow import OWNER, PROJECT, Env, make_app_dir, run

GUEST = 111


def release_zip(app_dir: Path, version: str, *, marker: str = "", extra: dict | None = None,
                root: str = "claude-telegram-bot", with_version: bool = True) -> bytes:
    prefix = f"{root}/" if root else ""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for item in ("bot", "prompts"):
            for f in sorted((app_dir / item).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    data = f.read_bytes()
                    if marker and f.name == "models.py":
                        data += f"\nRELEASE_MARKER = {marker!r}\n".encode()
                    zf.writestr(prefix + f.relative_to(app_dir).as_posix(), data)
        if "requirements.txt" not in (extra or {}):
            zf.writestr(prefix + "requirements.txt", (app_dir / "requirements.txt").read_bytes())
        if with_version:
            zf.writestr(prefix + "VERSION", version + "\n")
        zf.writestr(prefix + ".env", "SECRET=1\n")  # e'tiborsiz qolishi kerak
        zf.writestr(prefix + "launcher.py", "print('hack')\n")  # e'tiborsiz qolishi kerak
        for name, content in (extra or {}).items():
            zf.writestr(prefix + name, content)
    return buf.getvalue()


def simple_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ----------------------------------------------------------------------------- parol


def test_password_hash_and_limiter():
    stored = hash_password("TTA@2026")
    assert stored.startswith("pbkdf2_sha256$200000$") and "TTA@2026" not in stored
    assert verify_password("TTA@2026", stored)
    assert not verify_password("tta@2026", stored)
    assert not verify_password("x", "garbage") and not verify_password("x", "md5$1$aa$bb")
    assert hash_password("TTA@2026") != stored  # tuz har safar yangi

    now = [0.0]
    lim = AttemptLimiter(max_attempts=3, lock_seconds=60, clock=lambda: now[0])
    assert [lim.fail(1), lim.fail(1), lim.fail(1)] == [2, 1, 0]
    assert lim.locked_for(1) == 61 and lim.locked_for(2) == 0
    now[0] = 59.5
    assert lim.locked_for(1) == 1
    now[0] = 61
    assert lim.locked_for(1) == 0
    assert lim.fail(1) == 2  # hisob yangidan boshlanadi
    lim.reset(1)
    assert lim.fail(1) == 2


def test_password_gate_and_members(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Salom, mehmon!")])

        def sent():
            return env.session.of("SendMessage")[-1].text

        try:
            # parol o'rnatilmagan: begona odam kira olmaydi
            await env.send_text("salom", user_id=GUEST)
            assert "Bu shaxsiy bot" in sent() and str(GUEST) in sent()

            # egasi parol o'rnatadi; xabari o'chiriladi, bazada faqat xesh
            await env.send_text("/parol 123")
            assert "kamida 6" in sent()
            env.session.clear()
            await env.send_text("/parol TTA@2026")
            assert "Parol o'rnatildi" in sent()
            assert len(env.session.of("DeleteMessage")) == 1
            stored = await env.db.get_config("password_hash")
            assert stored and "TTA@2026" not in stored
            await env.send_text("/parol")
            assert "o'rnatilgan ✅" in sent()

            # mehmon: avval taklif, keyin xato va to'g'ri parol (har urinish o'chiriladi)
            await env.send_text("/start", user_id=GUEST)
            assert "parol bilan himoyalangan" in env.session.last_out()
            assert env.session.of("SendPhoto")[-1].chat_id == GUEST
            env.session.clear()
            await env.send_text("tta@2026", user_id=GUEST)
            assert "Parol noto'g'ri. Yana 4 ta urinish qoldi." in sent()
            assert len(env.session.of("DeleteMessage")) == 1
            env.session.clear()
            await env.send_text("TTA@2026", user_id=GUEST)
            texts = [(m.chat_id, m.text) for m in env.session.of("SendMessage")]
            assert any(cid == GUEST and "xush kelibsiz" in t for cid, t in texts)
            welcome = env.session.of("SendPhoto")[-1]
            assert welcome.chat_id == GUEST and "Salom, Shohrux!" in welcome.caption
            assert "menu:admin" not in [b.callback_data for row in welcome.reply_markup.inline_keyboard for b in row]
            assert any(cid == OWNER and "Yangi foydalanuvchi" in t and str(GUEST) in t for cid, t in texts)
            assert len(env.session.of("DeleteMessage")) == 1
            assert (await env.db.member(GUEST)).active

            # a'zo bot bilan ishlaydi, admin buyruqlari esa yopiq
            await env.send_text("Salom", user_id=GUEST)
            assert len(env.api.requests) == 1 and "Salom, mehmon!" in env.last_edit().text
            for command in ("/parol yangiparol", "/yangilash", "/qaytarish", "/kod", "/foydalanuvchilar", "/versiya"):
                await env.send_text(command, user_id=GUEST)
                assert "faqat bot egasi uchun" in sent(), command
            await env.send_text("/yordam", user_id=GUEST)
            assert "/model" in sent() and "/yangilash" not in sent()
            await env.press("menu:admin", user_id=GUEST)
            assert "Faqat bot egasi" in env.session.of("AnswerCallbackQuery")[-1].text
            await env.press(f"rm:{GUEST}", user_id=GUEST)
            assert "Ruxsat yo'q" in env.session.of("AnswerCallbackQuery")[-1].text
            assert (await env.db.member(GUEST)).active

            # egasi ro'yxatni ko'radi va chiqarib yuboradi
            await env.send_text("/yordam")
            assert "/yangilash" in sent() and "/kod" in sent() and "Parol: o'rnatilgan ✅" in sent()
            assert "Foydalanuvchilar: 1 ta" in sent()
            await env.send_text("/foydalanuvchilar")
            listing = env.session.of("SendMessage")[-1]
            assert "Shohrux" in listing.text
            assert listing.reply_markup.inline_keyboard[0][0].callback_data == f"rm:{GUEST}"
            await env.press(f"rm:{GUEST}")
            assert not (await env.db.member(GUEST)).active and "Chiqarilganlar" in env.last_edit().text
            await env.send_text("Salom", user_id=GUEST)
            assert "kirishingizni yopgan" in sent()
            await env.send_text("TTA@2026", user_id=GUEST)  # parol bilan qayta kira olmaydi
            assert "kirishingizni yopgan" in sent()
            await env.press(f"rs:{GUEST}")
            assert (await env.db.member(GUEST)).active

            # parol o'chirilsa: yangi odam kira olmaydi, avval kirgan qoladi
            await env.send_text("/parol off")
            assert "Parol o'chirildi" in sent()
            await env.send_text("salom", user_id=222)
            assert "Bu shaxsiy bot" in sent()
            assert (await env.db.member(GUEST)).active
        finally:
            await env.close()
    run(go())


def test_password_lockout(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        try:
            await env.send_text("/parol TTA@2026")
            await env.send_text("/start", user_id=333)
            for i in range(5):
                await env.send_text(f"xato{i}", user_id=333)
            assert "30 daqiqadan keyin" in env.session.of("SendMessage")[-1].text
            await env.send_text("TTA@2026", user_id=333)  # blok paytida to'g'ri parol ham o'tmaydi
            assert "daqiqadan keyin qayta urinib" in env.session.of("SendMessage")[-1].text
            assert await env.db.member(333) is None
            env.ctx.limiter.reset(333)
            await env.send_text("TTA@2026", user_id=333)
            assert (await env.db.member(333)).active
        finally:
            await env.close()
    run(go())


def test_first_contact_is_not_a_failed_attempt(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        try:
            await env.send_text("/parol TTA@2026")
            env.session.clear()
            await env.send_text("Assalomu alaykum", user_id=444)  # birinchi xabar — salomlashish
            assert "parolni shu chatga yozib yuboring" in env.session.last_out()
            assert env.session.of("DeleteMessage") == []
            await env.send_text("TTA@2026", user_id=444)
            assert (await env.db.member(444)).active
            await env.send_text("TTA@2026", user_id=555)  # birinchi xabarning o'zi parol bo'lsa — darhol kiradi
            assert (await env.db.member(555)).active
            await env.send_text("salom", user_id=666)
            await env.send_text("salom", user_id=666)  # ikkinchisi endi xato urinish
            assert "Yana 4 ta urinish qoldi" in env.session.of("SendMessage")[-1].text
        finally:
            await env.close()
    run(go())


def test_owner_cost_report_shows_everyone(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        try:
            await env.db.add_member(GUEST, "Ali", "ali_uz")
            await env.db.log_usage(OWNER, "claude-opus-5-5", {"input": 10}, 1.25)
            await env.db.log_usage(GUEST, "claude-fable-5-1", {"input": 10}, 3.5)
            await env.send_text("/xarajat")
            text = env.session.of("SendMessage")[-1].text
            assert "Barcha foydalanuvchilar, joriy davr: <b>$4.75</b> / $100 (5%)" in text
            assert "• Ali @ali_uz — $3.50 (1 ta)" in text and "• Siz — $1.25 (1 ta)" in text
            await env.send_text("/xarajat", user_id=GUEST)
            member_text = env.session.of("SendMessage")[-1].text
            assert "$3.50" in member_text and "Barcha foydalanuvchilar" not in member_text
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- yangilash


def test_stage_validation(tmp_path):
    app = make_app_dir(tmp_path / "app")
    upd = UpdateManager(app, tmp_path / "data")
    with pytest.raises(UpdateError, match="zip arxiv emas"):
        upd.stage(b"bu zip emas")
    with pytest.raises(UpdateError, match="bo'sh"):
        upd.stage(simple_zip({}))
    for evil in ("../x.py", "/etc/passwd", "claude-telegram-bot/bot/../../x.py", "bot\\evil.py"):
        with pytest.raises(UpdateError, match="fayl yo'li"):
            upd.stage(simple_zip({evil: "x", "VERSION": "1"}))
    assert not (tmp_path / "x.py").exists()
    with pytest.raises(UpdateError, match="to'liq emas"):
        upd.stage(simple_zip({"VERSION": "2.0.0", "bot/__init__.py": ""}))
    with pytest.raises(UpdateError, match="VERSION fayli yo'q"):
        upd.stage(release_zip(app, "2.0.0", with_version=False))
    with pytest.raises(UpdateError, match="Sintaksis xatosi: bot/broken.py, 1-qator"):
        upd.stage(release_zip(app, "2.0.0", extra={"bot/broken.py": "def x(:\n"}))
    assert not list((tmp_path / "data" / "update").glob("staging-*"))  # rad etilganlar tozalanadi

    # papkasiz arxiv ham, papkali ham bo'ladi; bajariladigan bit saqlanadi
    info = zipfile.ZipInfo("install.sh")
    info.external_attr = 0o100755 << 16
    buf = io.BytesIO(release_zip(app, "2.0.0", root=""))
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr(info, "#!/bin/bash\necho hi\n")
    staged = upd.stage(buf.getvalue())
    assert staged.version == "2.0.0" and not staged.requirements_changed
    assert sorted(staged.skipped) == [".env", "launcher.py"]
    assert os.access(staged.path / "install.sh", os.X_OK)
    upd.discard(staged)
    staged = upd.stage(release_zip(app, "2.0.0", extra={"requirements.txt": "aiogram==9.9\n"}))
    assert staged.requirements_changed
    upd.discard(staged)


def test_apply_failure_restores_everything(tmp_path, monkeypatch):
    app = make_app_dir(tmp_path / "app")
    upd = UpdateManager(app, tmp_path / "data")
    staged = upd.stage(release_zip(app, "1.3.0", marker="v13"))

    def boom(source, target):
        raise OSError("disk to'ldi")

    monkeypatch.setattr(UpdateManager, "_merge_dir", staticmethod(boom))
    with pytest.raises(OSError):
        upd.apply(staged, chat_id=1)
    assert upd.version == "1.1.0"
    assert "RELEASE_MARKER" not in (app / "bot" / "models.py").read_text()
    assert not upd.pending_file.exists() and upd.backups() == []


def test_prune_backups(tmp_path):
    app = make_app_dir(tmp_path / "app")
    upd = UpdateManager(app, tmp_path / "data")
    for i in range(7):
        (upd.backups_dir / f"20261012-00000{i}-v1.0.{i}").mkdir(parents=True)
    upd.prune_backups()
    assert [p.name[-5:] for p in upd.backups()] == ["1.0.6", "1.0.5", "1.0.4", "1.0.3", "1.0.2"]


def test_update_and_restore_flow(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        app = env.app_dir
        upd = env.ctx.updater

        def sent():
            return env.session.of("SendMessage")[-1].text

        try:
            assert upd.version == "1.1.0"
            await env.send_text("/yangilash")
            assert "v1.1.0" in sent() and ".zip" in sent()
            await env.send_document("f1", b"hello", "notes.txt", "text/plain")
            assert ".zip arxiv bo'lishi kerak" in sent()
            # kutilmagan paytdagi .zip — oddiy fayl (Claude'ga ham bormaydi)
            await env.send_document("f2", b"PK..", "arxiv.zip", "application/zip")
            assert "o'qiy olmayman" in sent() and env.api.requests == []

            bad = release_zip(app, "1.2.0", extra={"bot/broken.py": "def x(:\n"})
            await env.send_document("f3", bad, "upd.zip", "application/zip", caption="/yangilash")
            assert "rad etildi: Sintaksis xatosi: bot/broken.py" in env.last_edit().text

            good = release_zip(app, "1.2.0", marker="v12", extra={"UPDATE_NOTES.md": "Yangi funksiya: /kod"})
            await env.send_text("/yangilash")
            await env.send_document("f4", good, "claude-telegram-bot-v1.2.0.zip", "application/zip")
            summary = env.last_edit()
            assert "v1.1.0 → <b>v1.2.0</b>" in summary.text and "Yangi funksiya: /kod" in summary.text
            assert ".env" in summary.text and "launcher.py" in summary.text
            ok_data, no_data = (b.callback_data for b in summary.reply_markup.inline_keyboard[0])
            assert ok_data.startswith("up:ok:") and no_data.startswith("up:no:")

            await env.press(no_data)
            assert "Bekor qilindi" in env.last_edit().text
            assert not list((tmp_path / "data" / "update").glob("staging-*"))
            await env.press(ok_data)  # eskirgan tugma
            alert = env.session.of("AnswerCallbackQuery")[-1]
            assert alert.show_alert and "eskirgan" in alert.text and upd.version == "1.1.0"

            # javob yozilayotganda o'rnatilmaydi
            await env.send_document("f5", good, "u.zip", "application/zip", caption="/yangilash")
            ok_data = env.last_edit().reply_markup.inline_keyboard[0][0].callback_data
            busy = asyncio.get_running_loop().create_future()
            env.ctx.active[GUEST] = busy
            await env.press(ok_data)
            assert "javob yozilmoqda" in env.session.of("AnswerCallbackQuery")[-1].text
            busy.set_result(None)
            env.ctx.active.clear()

            await env.press(ok_data)
            assert env.ctx.restart_requested and "v1.2.0 o'rnatildi" in env.last_edit().text
            assert upd.version == "1.2.0"
            assert "RELEASE_MARKER = 'v12'" in (app / "bot" / "models.py").read_text()
            assert (app / "launcher.py").read_text() != "print('hack')\n"
            assert not (app / ".env").exists() and not (app / "UPDATE_NOTES.md").exists()
            pending = json.loads(upd.pending_file.read_text())
            assert (pending["version"], pending["previous"], pending["chat_id"]) == ("1.2.0", "1.1.0", OWNER)
            backups = upd.backups()
            assert len(backups) == 1 and (backups[0] / "VERSION").read_text().strip() == "1.1.0"
            assert "RELEASE_MARKER" not in (backups[0] / "bot" / "models.py").read_text()

            # yangi versiya ishga tushdi deb faraz qilamiz
            env.session.clear()
            await bot_app.announce_update(env.bot, env.ctx)
            note = env.session.of("SendMessage")[-1]
            assert note.chat_id == OWNER and note.text.startswith("✅ Bot yangilandi: v1.1.0 → v1.2.0")
            assert "Yangi funksiya: /kod" in note.text and not upd.pending_file.exists()
            await bot_app.announce_update(env.bot, env.ctx)
            assert len(env.session.of("SendMessage")) == 1

            await env.send_text("/versiya")
            assert "v1.2.0" in sent() and "• v1.1.0 — " in sent()

            # /qaytarish
            env.ctx.restart_requested = False
            await env.send_text("/qaytarish")
            confirm = env.session.of("SendMessage")[-1]
            assert "v1.2.0 → <b>v1.1.0</b>" in confirm.text
            await env.press(confirm.reply_markup.inline_keyboard[0][0].callback_data)
            assert env.ctx.restart_requested and "v1.1.0 tiklandi" in env.last_edit().text
            assert upd.version == "1.1.0" and "RELEASE_MARKER" not in (app / "bot" / "models.py").read_text()
            env.session.clear()
            await bot_app.announce_update(env.bot, env.ctx)
            assert env.session.of("SendMessage")[-1].text.startswith("↩️ Oldingi versiya tiklandi: v1.2.0 → v1.1.0")
            assert upd.backups() == []
            await env.send_text("/qaytarish")
            assert "zaxira nusxa yo'q" in sent()

            # /kod: maxfiy narsa yo'q, qayta yuklasa ham ishlaydi
            (app / ".env").write_text("TELEGRAM_BOT_TOKEN=SEKRET7TOKEN\n")
            (app / "data").mkdir(exist_ok=True)
            (app / "data" / "bot.db").write_text("db")
            env.session.clear()
            await env.send_text("/kod")
            doc = env.session.of("SendDocument")[-1]
            assert doc.document.filename == "claude-telegram-bot-v1.1.0.zip"
            names = zipfile.ZipFile(io.BytesIO(doc.document.data)).namelist()
            assert "claude-telegram-bot/bot/app.py" in names and "claude-telegram-bot/launcher.py" in names
            assert not any(n.endswith(".env") or "/data/" in n or "__pycache__" in n for n in names)
            assert all(b"SEKRET7TOKEN" not in zipfile.ZipFile(io.BytesIO(doc.document.data)).read(n) for n in names)
            staged = upd.stage(doc.document.data)
            assert staged.version == "1.1.0" and "launcher.py" in staged.skipped
            upd.discard(staged)
        finally:
            await env.close()
    run(go())


def test_restart_stops_real_polling(tmp_path):
    """Tasdiq tugmasi haqiqiy start_polling ichida: polling to'xtaydi va tugma Telegram'da tasdiqlanadi."""
    async def go():
        env = await Env().start(tmp_path)
        env.ctx.restart_delay = 0.2
        try:
            staged = env.ctx.updater.stage(release_zip(env.app_dir, "1.2.0"))
            env.ctx.staged[OWNER] = staged
            update = Update.model_validate({"update_id": 77, "callback_query": {
                "id": "cb77", "from": {"id": OWNER, "is_bot": False, "first_name": "Shohrux"},
                "chat_instance": "ci", "data": f"up:ok:{staged.id}",
                "message": {"message_id": 1, "date": 1760240000, "chat": {"id": OWNER, "type": "private"},
                            "text": "menyu"},
            }}, context={"bot": env.bot})
            env.session.updates.append(update)
            await asyncio.wait_for(env.dp.start_polling(env.bot, handle_signals=False), timeout=10)
            assert env.ctx.restart_requested and env.ctx.updater.version == "1.2.0"
            assert 78 in [c.offset for c in env.session.of("GetUpdates")]
        finally:
            await env.close()
    run(go())


def test_connect_sets_owner_menu():
    async def go():
        session = fakes.FakeSession()
        await bot_app.connect(Bot("1:T", session=session), frozenset({OWNER}))
        calls = session.of("SetMyCommands")
        assert len(calls) == 2 and calls[0].scope is None
        assert "yangilash" not in [c.command for c in calls[0].commands]
        assert calls[1].scope.chat_id == OWNER and "yangilash" in [c.command for c in calls[1].commands]
    run(go())


def test_main_exit_codes(monkeypatch):
    import bot.__main__ as entry

    async def restart():
        return True

    async def normal():
        return False

    async def broken():
        raise ConfigError("OWNER_IDS bo'sh")

    monkeypatch.setattr(entry, "run", restart)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 75
    monkeypatch.setattr(entry, "run", normal)
    entry.main()
    monkeypatch.setattr(entry, "run", broken)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 2


# ----------------------------------------------------------------------------- launcher

FAKE_BOT = """
import pathlib, sys
app = pathlib.Path.cwd()
runs = app / "runs.txt"
n = int(runs.read_text()) + 1 if runs.exists() else 1
runs.write_text(str(n))
marker = (app / "bot" / "marker.txt").read_text().strip()
print(f"run {n} marker {marker}", flush=True)
if marker == "broken":
    print("Traceback (most recent call last):")
    print("ImportError: cannot import name 'X' from 'bot.handlers'", flush=True)
    sys.exit(1)
if marker == "restart" and n == 1:
    sys.exit(75)
sys.exit(int((app / "exit_code.txt").read_text()))
"""


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "bot").mkdir(parents=True)
    (app / "exit_code.txt").write_text("4")
    script = tmp_path / "fake_bot.py"
    script.write_text(FAKE_BOT)
    spec = importlib.util.spec_from_file_location("launcher_under_test", PROJECT / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "APP", app)
    monkeypatch.setattr(mod, "UPDATE_DIR", app / "data" / "update")
    monkeypatch.setattr(mod, "PENDING", app / "data" / "update" / "pending.json")
    monkeypatch.setattr(mod, "RESULT", app / "data" / "update" / "result.json")
    monkeypatch.setattr(mod, "REQ_STAMP", tmp_path / "stamp")
    monkeypatch.setattr(mod, "BOT_COMMAND", [sys.executable, str(script)])
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield mod, app
    for s, handler in saved.items():
        signal.signal(s, handler)


def test_launcher_restarts_on_75(launcher):
    mod, app = launcher
    (app / "bot" / "marker.txt").write_text("restart")
    assert mod.main() == 4
    assert (app / "runs.txt").read_text() == "2"


def test_launcher_rolls_back_broken_update(launcher):
    mod, app = launcher
    (app / "bot" / "marker.txt").write_text("broken")
    (app / "VERSION").write_text("1.2.0\n")
    backup = app / "data" / "backups" / "20261012-000000-v1.1.0"
    (backup / "bot").mkdir(parents=True)
    (backup / "bot" / "marker.txt").write_text("ok")
    (backup / "VERSION").write_text("1.1.0\n")
    (app / "data" / "update").mkdir(parents=True)
    (app / "data" / "update" / "pending.json").write_text(json.dumps(
        {"version": "1.2.0", "previous": "1.1.0", "backup": str(backup), "chat_id": 42}))

    assert mod.main() == 4  # tiklangan eski versiya ishladi
    assert (app / "bot" / "marker.txt").read_text() == "ok" and (app / "VERSION").read_text() == "1.1.0\n"
    assert not (app / "data" / "update" / "pending.json").exists() and not backup.exists()
    result = json.loads((app / "data" / "update" / "result.json").read_text())
    assert result["status"] == "rolled_back" and result["chat_id"] == 42 and "ImportError" in result["error"]

    notice = UpdateManager(app, app / "data").finalize()
    assert notice.chat_id == 42 and not notice.ok
    assert notice.text.startswith("❌ v1.2.0 ishga tushmadi — v1.1.0 avtomatik tiklandi")
    assert "ImportError" in notice.text


def test_launcher_refuses_backup_outside_data(launcher, tmp_path):
    mod, app = launcher
    (app / "bot" / "marker.txt").write_text("broken")
    outside = tmp_path / "outside"
    (outside / "bot").mkdir(parents=True)
    (outside / "bot" / "marker.txt").write_text("ok")
    (app / "data" / "update").mkdir(parents=True)
    (app / "data" / "update" / "pending.json").write_text(json.dumps({"backup": str(outside)}))
    assert mod.main() == 1
    assert (app / "bot" / "marker.txt").read_text() == "broken" and outside.exists()
    assert not (app / "data" / "update" / "pending.json").exists()


def test_launcher_requirements(launcher, monkeypatch):
    mod, app = launcher
    (app / "requirements.txt").write_text("aiogram==3.31.0\n")
    calls = []
    codes = iter([1, 0])
    monkeypatch.setattr(mod.subprocess, "call", lambda cmd: calls.append(cmd) or next(codes))
    mod.ensure_requirements()  # pip xato: belgi yozilmaydi
    assert len(calls) == 1 and "-r" in calls[0] and not mod.REQ_STAMP.exists()
    mod.ensure_requirements()  # pip muvaffaqiyatli
    assert len(calls) == 2 and mod.REQ_STAMP.exists()
    mod.ensure_requirements()  # o'zgarmagan: pip chaqirilmaydi
    assert len(calls) == 2


def test_launcher_forwards_sigterm(tmp_path):
    app = tmp_path / "app"
    (app / "bot").mkdir(parents=True)
    script = tmp_path / "sleepy.py"
    script.write_text("import time\nprint('started', flush=True)\ntime.sleep(30)\n")
    env = dict(os.environ, APP_DIR=str(app), REQ_STAMP=str(tmp_path / "stamp"),
               BOT_COMMAND=f"{sys.executable} {script}")
    proc = subprocess.Popen([sys.executable, str(PROJECT / "launcher.py")], env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "started"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()

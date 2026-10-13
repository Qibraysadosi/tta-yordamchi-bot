"""infra tuzatishlari: ishga tushishda osilib qolish, zaxira hajmi, havola o'qish, yangilash, jadval (DST)."""
import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from aiogram import Bot

import fakes
from bot import app as bot_app
from bot import links, media, scheduler
from bot.config import ConfigError, load_settings
from bot.db import Database
from bot.updater import UpdateManager
from test_admin import release_zip
from test_flow import OWNER, PROJECT, PROMPT, Env, make_app_dir, run

PIP_ERROR_FILE = "pip_error.json"  # launcher.py yozadigan fayl


# ----------------------------------------------------------------------------- ishga tushish


def _base_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name in ("WEBAPP_URL", "BACKUP_TIME", "TIMEZONE", "GITHUB_TOKEN"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TESTTOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("OWNER_IDS", str(OWNER))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "bot.db"))
    monkeypatch.setenv("SYSTEM_PROMPT_FILE", str(PROMPT))
    monkeypatch.setenv("GITHUB_REPO", "off")


@pytest.mark.parametrize("broken", ["build_dispatcher", "PromptLoader"])
def test_startup_error_closes_database(tmp_path, monkeypatch, broken):
    """Baza ochilgandan keyingi xato ham bazani yopadi — aks holda aiosqlite oqimi jarayonni osib qo'yardi."""
    _base_env(monkeypatch, tmp_path)
    opened = []

    class SpyDatabase(Database):
        closed = False

        @classmethod
        async def open(cls, path):
            db = await super().open(path)
            opened.append(db)
            return db

        async def close(self):
            self.closed = True
            await super().close()

    def boom(*args, **kwargs):
        raise RuntimeError("yangi versiyada xato")

    monkeypatch.setattr(bot_app, "Database", SpyDatabase)
    monkeypatch.setattr(bot_app, broken, boom)
    try:
        with pytest.raises(RuntimeError, match="yangi versiyada xato"):
            run(bot_app.run())
        assert len(opened) == 1 and opened[0].closed
    finally:
        for db in opened:  # eski kodda ham pytest osilib qolmasin
            if not db.closed:
                run(db.close())


def test_config_validates_timezone_and_token(tmp_path, monkeypatch):
    _base_env(monkeypatch, tmp_path)
    assert load_settings().timezone == "Asia/Tashkent"
    monkeypatch.setenv("TIMEZONE", "Asia/Toshkent")
    with pytest.raises(ConfigError, match="TIMEZONE"):
        load_settings()
    monkeypatch.setenv("TIMEZONE", "Europe/Berlin")
    assert load_settings().timezone == "Europe/Berlin"
    for token in ("123456789AAHtokenbezikki", "123:AA H", "abc:def"):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN") as exc:
            load_settings()
        assert token not in str(exc.value)  # token xabarga tushmaydi


# ----------------------------------------------------------------------------- zaxira hajmi


def test_backup_copy_drops_attachments_live_db_untouched(tmp_path):
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        try:
            pdf = media.pdf_block(b"%PDF-1.4\n" + os.urandom(2_000_000), "ma'ruza.pdf")
            image = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(os.urandom(300_000)).decode()}}
            await db.add_turn(OWNER, 1, [pdf, image, {"type": "text", "text": "nima bu?"}], "javob")
            data, name = await scheduler.make_backup(db, tmp_path / "bot.db", "x")
            assert name == "backup-x.db" and data[:16] == b"SQLite format 3\x00"
            assert len(data) < 300_000  # 3 MB dan ortiq base64 nusxaga tushmadi
            assert not list(tmp_path.glob("backup-*"))
            copy = tmp_path / "copy.db"
            copy.write_bytes(data)
            conn = sqlite3.connect(copy)
            content = json.loads(conn.execute("SELECT content FROM messages WHERE role = 'user'").fetchone()[0])
            conn.close()
            assert content[0]["type"] == "document" and content[0]["title"] == "ma'ruza.pdf"
            assert content[0]["source"] == {"type": "text", "media_type": "text/plain", "data": scheduler.ATTACHMENT_PLACEHOLDER}
            assert content[1] == {"type": "text", "text": scheduler.ATTACHMENT_PLACEHOLDER}
            assert content[2] == {"type": "text", "text": "nima bu?"}
            history = await db.history(OWNER, 1, 0)  # asl baza o'zgarmagan
            assert history[0].content[0]["source"]["data"] == pdf["source"]["data"]
            assert history[0].content[1] == image
        finally:
            await db.close()
    run(go())


def test_backup_zipped_when_big_and_owner_told_when_too_big(tmp_path, monkeypatch):
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        session = fakes.FakeSession()
        bot = Bot("123456:TESTTOKEN", session=session)
        try:
            await db.add_turn(OWNER, 1, [{"type": "text", "text": "a" * 400_000}], "javob")
            monkeypatch.setattr(scheduler, "BACKUP_MAX_BYTES", 300_000)
            assert await scheduler.send_backup(bot, db, tmp_path / "bot.db", [OWNER], "Asia/Tashkent", reason="Kunlik zaxira")
            name, data, caption = session.documents[-1]
            assert name.endswith(".zip") and "zip ichidagi" in caption
            inner = zipfile.ZipFile(io.BytesIO(data))
            assert inner.namelist() == [name.removesuffix(".zip") + ".db"]
            assert inner.read(inner.namelist()[0])[:16] == b"SQLite format 3\x00"

            # siqilmaydigan katta matn: zip ham sig'maydi — egasiga aniq xabar, hujjat yuborilmaydi
            await db.set_config(scheduler.BACKUP_STAMP_KEY, "2000-01-01")
            await db.add_turn(OWNER, 1, [{"type": "text", "text": base64.b64encode(os.urandom(600_000)).decode()}], "j")
            session.clear()
            ok = await scheduler.send_backup(bot, db, tmp_path / "bot.db", [OWNER], "Asia/Tashkent", reason="Kunlik zaxira")
            assert not ok and not session.of("SendDocument")
            notice = session.of("SendMessage")[-1]
            assert notice.chat_id == OWNER and "sig'madi" in notice.text and "bot.db" in notice.text
            assert await db.get_config(scheduler.BACKUP_STAMP_KEY) == "2000-01-01"
        finally:
            await db.close()
    run(go())


# ----------------------------------------------------------------------------- havolalar


def test_page_inside_single_form_keeps_text():
    page = ('<html><head><title>Yangiliklar</title></head><body><form method="post" id="form1">'
            '<div class="aspNetHidden"><input type="hidden" name="__VIEWSTATE" value="xyz"></div>'
            '<div><h1>Yangilik</h1><p>' + "Matn. " * 60 + '</p>'
            '<select name="lang"><option>Rus tili</option></select><textarea>izoh</textarea></div></form></body></html>')
    title, text = links.html_to_text(page)
    assert title == "Yangiliklar" and text.startswith("# Yangilik\n\nMatn. Matn.")
    assert "Rus tili" not in text and "izoh" not in text


def test_html_charset_from_meta(monkeypatch):
    body = "<p>" + "Сегодня в Ташкенте прошла конференция. " * 10 + "</p>"
    page = ("<html><head><meta http-equiv='Content-Type' content='text/html; charset=windows-1251'>"
            "<title>Новости Ташкента</title></head><body>" + body + "</body></html>").encode("cp1251")
    plain_cp1251 = ("<html><head><title>Янгиликлар</title></head><body>" + body + "</body></html>").encode("cp1251")

    async def handler(request):
        if request.url.path == "/meta":
            return httpx2.Response(200, headers={"content-type": "text/html"}, content=page)
        if request.url.path == "/header":
            return httpx2.Response(200, headers={"content-type": "text/html; charset=windows-1251"}, content=page)
        return httpx2.Response(200, headers={"content-type": "text/html"}, content=plain_cp1251)

    async def go():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            for path in ("/meta", "/header"):
                result = await links.fetch(f"https://example.uz{path}", 10_000_000, client)
                assert result.title == "Новости Ташкента"
                assert "Сегодня в Ташкенте" in result.block["source"]["data"] and "\ufffd" not in result.block["source"]["data"]
            result = await links.fetch("https://example.uz/plain", 10_000_000, client)
            assert result.title == "Янгиликлар" and "\ufffd" not in result.block["source"]["data"]
    run(go())
    # UTF-8 sahifadagi bitta buzuq bayt butun matnni cp1251 ga aylantirmaydi
    assert links.decode_html("<p>Oʻzbekiston</p>".encode() + b"\xff" + b"x" * 300, None).startswith("<p>Oʻzbekiston")


def test_page_without_closing_head():
    article = "<article><h1>Sarlavha</h1><p>" + "Matn. " * 60 + "</p></article>"
    for page in (
        "<!doctype html><html><head><meta charset=utf-8><title>Sahifa</title><body>" + article,
        '<html lang="uz"><head prefix="og: https://ogp.me/ns#"><meta charset="utf-8"><title>Sahifa</title>'
        '<script>var a = "yashirin";</script>' + article,
    ):
        title, text = links.html_to_text(page)
        assert title == "Sahifa" and text.startswith("# Sarlavha\n\nMatn.") and "yashirin" not in text


def test_url_with_parentheses():
    text = ("https://en.wikipedia.org/wiki/Python_(programming_language) va "
            "(qarang: https://kun.uz/news/1). (https://uz.wikipedia.org/wiki/Toshkent_(shahar)).")
    assert links.find_links(text) == [
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "https://kun.uz/news/1",
        "https://uz.wikipedia.org/wiki/Toshkent_(shahar)",
    ]


def test_pre_keeps_code_layout():
    _, text = links.html_to_text(
        "<p>Kod:</p><pre><code>def f(x):\n    if x:\n        return 1\n    return 2</code></pre><p>Tamom</p>"
    )
    assert text == "Kod:\n\ndef f(x):\n    if x:\n        return 1\n    return 2\n\nTamom"
    _, text = links.html_to_text(
        '<pre><span class="k">def</span> <span class="nf">f</span>(x):\n    <span class="k">return</span> 2</pre>'
    )
    assert text == "def f(x):\n    return 2"
    assert links.html_to_text("<p><b>Salom</b> <i>dunyo</i></p>")[1] == "Salom dunyo"


def test_svg_title_not_in_page_title():
    title, text = links.html_to_text(
        '<head><title>Real Title</title></head><body><a><svg role="img"><title>Facebook icon</title></svg></a>'
        "<svg><title>Telegram</title></svg><p>Body text</p></body>"
    )
    assert (title, text) == ("Real Title", "Body text")


# ----------------------------------------------------------------------------- yangilash


def _load_launcher(monkeypatch, app, stamp):
    spec = importlib.util.spec_from_file_location("launcher_fix_infra", PROJECT / "launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "APP", app)
    monkeypatch.setattr(mod, "UPDATE_DIR", app / "data" / "update")
    monkeypatch.setattr(mod, "REQ_STAMP", stamp)
    return mod


def _pending(upd: UpdateManager) -> None:
    upd.update_dir.mkdir(parents=True, exist_ok=True)
    upd.pending_file.write_text(json.dumps({"version": "1.2.0", "previous": "1.1.0", "chat_id": OWNER,
                                            "requirements_changed": True}))


def test_pip_failure_not_reported_as_installed(tmp_path, monkeypatch):
    app = make_app_dir(tmp_path / "app")
    upd = UpdateManager(app, app / "data")
    stamp = tmp_path / "requirements.sha256"
    monkeypatch.setenv("REQ_STAMP", str(stamp))
    digest = hashlib.sha256((app / "requirements.txt").read_bytes()).hexdigest()

    # launcher pip xatosini yozadi; bot egasiga «o'rnatildi» demaydi
    launcher = _load_launcher(monkeypatch, app, stamp)
    stamp.write_text("eski-belgi")
    codes = iter([1, 0])
    monkeypatch.setattr(launcher.subprocess, "call", lambda cmd: next(codes))
    launcher.ensure_requirements()
    error = json.loads((upd.update_dir / PIP_ERROR_FILE).read_text())
    assert error == {"code": 1, "sha256": digest}
    _pending(upd)
    text = upd.finalize().text
    assert "o'rnatildi" not in text and "kutubxonalar o'rnatilmadi" in text

    # belgi mos kelmasa (eski launcher, xato fayli yo'q) — baribir ogohlantiradi
    (upd.update_dir / PIP_ERROR_FILE).unlink()
    _pending(upd)
    assert "kutubxonalar o'rnatilmadi" in upd.finalize().text

    # pip muvaffaqiyatli: xato fayli o'chadi, egasiga «o'rnatildi»
    launcher.ensure_requirements()
    assert stamp.read_text() == digest and not (upd.update_dir / PIP_ERROR_FILE).exists()
    _pending(upd)
    assert "📦 Kerakli kutubxonalar ham o'rnatildi." in upd.finalize().text


def test_stage_keeps_other_staged_updates(tmp_path):
    async def go():
        env = await Env().start(tmp_path)
        upd = env.ctx.updater
        try:
            good = release_zip(env.app_dir, "1.2.0", marker="v12")
            await env.send_document("f1", good, "u.zip", "application/zip", caption="/yangilash")
            ok_data = env.last_edit().reply_markup.inline_keyboard[0][0].callback_data
            assert len(ok_data.encode()) <= 64
            first = env.ctx.staged[OWNER]

            # rad etilgan arxiv, boshqa ega va /qaytarish tayyorlovi birinchisini o'chirmaydi
            bad = release_zip(env.app_dir, "1.3.0", extra={"bot/broken.py": "def x(:\n"})
            await env.send_document("f2", bad, "b.zip", "application/zip", caption="/yangilash")
            assert "rad etildi" in env.last_edit().text
            other = upd.stage(release_zip(env.app_dir, "1.3.0"))
            assert other.id != first.id and other.path != first.path
            upd.backup()
            restore = upd.stage_restore()
            assert first.path.is_dir() and other.path.is_dir() and restore.path.is_dir()
            upd.discard(other)
            upd.discard(restore)
            shutil.rmtree(upd.backups_dir)

            await env.press(ok_data)
            assert "v1.2.0 o'rnatildi" in env.last_edit().text and upd.version == "1.2.0"
            assert "RELEASE_MARKER = 'v12'" in (env.app_dir / "bot" / "models.py").read_text()
        finally:
            await env.close()
    run(go())


def _fake_backup(upd: UpdateManager, app, stamp: str, version: str) -> None:
    target = upd.backups_dir / f"{stamp}-v{version}"
    target.mkdir(parents=True)
    shutil.copytree(app / "bot", target / "bot", ignore=shutil.ignore_patterns("__pycache__"))
    (target / "VERSION").write_text(version + "\n")


def test_restore_does_not_lose_oldest_backup(tmp_path):
    app = make_app_dir(tmp_path / "app")
    upd = UpdateManager(app, tmp_path / "data")
    for i in range(5):
        _fake_backup(upd, app, f"2026010{i}-000000", f"1.0.{i}")
    assert [v for v, _ in upd.backup_versions()] == ["1.0.4", "1.0.3", "1.0.2", "1.0.1", "1.0.0"]

    staged = upd.stage_restore()
    upd.apply(staged, chat_id=OWNER)
    assert upd.version == "1.0.4"
    assert "tiklandi" in upd.finalize().text
    assert [v for v, _ in upd.backup_versions()] == ["1.0.3", "1.0.2", "1.0.1", "1.0.0"]

    # oddiy yangilanish: eski zaxiralar baribir 5 tagacha qisqaradi (finalize da)
    _fake_backup(upd, app, "20260105-000000", "1.0.5")
    staged = upd.stage(release_zip(app, "1.2.0"))
    upd.apply(staged, chat_id=OWNER)
    assert len(upd.backups()) == 6
    upd.finalize()
    assert len(upd.backups()) == 5 and upd.backup_versions()[0][0] == "1.0.4"


# ----------------------------------------------------------------------------- jadval (DST)


class _Stop(Exception):
    pass


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 10, 24, 3, 30, 1), 25 * 3600 - 1),  # qishki vaqtga o'tish: kun 25 soat
    (datetime(2026, 3, 28, 3, 30, 1), 23 * 3600 - 1),  # yozgi vaqtga o'tish: kun 23 soat
    (datetime(2026, 6, 1, 3, 30, 1), 24 * 3600 - 1),
])
def test_daily_job_wait_uses_absolute_time(monkeypatch, now, expected):
    tz = ZoneInfo("Europe/Berlin")
    waits = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.replace(tzinfo=tz)

    async def fake_sleep(seconds):
        waits.append(seconds)
        raise _Stop

    async def job_run():
        pass

    monkeypatch.setattr(scheduler, "datetime", FrozenDateTime)
    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)
    job = scheduler.DailyJob("t", "03:30", "Europe/Berlin", job_run)
    assert job.tz == tz
    with pytest.raises(_Stop):
        job._loop().send(None)
    assert waits == [expected]

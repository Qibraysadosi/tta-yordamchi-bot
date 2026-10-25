"""v1.10 tuzatishlari: Mini App (yuklab olish, kiritish tekshiruvi, rahbarga raqamsiz ma'lumot), ishga tushish va zaxira."""
import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramServerError

import fakes
from bot import app as bot_app
from bot import scheduler
from bot.claude import ClaudeError
from bot.db import Database
from bot.reports.archive import WEEK, period_key
from test_build_release import load_module, run_main
from test_flow import OWNER, PROJECT, run
from test_reports import (
    BOSS, CARD_1, CARD_2, LEADERS, LEADS, MALIKA, SARDOR, WED, local, person, setup_groups, start, ts,
)
from test_reports_api import LEADER, Api
from test_reports_review import FailingSession, failing_env, texts
from test_tasks import TASKS, Office
from test_webapp import TOKEN, App


def signed(fields: dict) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_data(user_id: int, age: int = 0) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAEqq",
              "user": json.dumps({"id": user_id, "first_name": "Odam"}, separators=(",", ":"))}
    fields["hash"] = signed(fields)
    return urlencode(fields)


# ----------------------------------------------------------------------------- Mini App: kiritish tekshiruvi
def test_bad_input_gives_4xx_not_500(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        try:
            for path in ("/api/conversations?offset=abc", "/api/conversations?limit=1.5", "/api/files?limit=x"):
                res = await app.client.get(path, headers=app.headers())
                assert res.status == 400, path
                assert "son" in (await res.json())["message"]
            assert (await app.get("/api/files?limit=999999"))["total"] == 0  # chegaraga tushiriladi

            # imzo o'rnida ASCII bo'lmagan belgi — 401 (avval: TypeError → 500)
            fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": OWNER})}
            res = await app.client.get("/api/me", headers={"Authorization": "tma " + urlencode(dict(fields, hash="ш" * 64))})
            assert res.status == 401
            # initData 5 soat oldin olingan — eskirgan (chegara 4 soat)
            res = await app.client.get("/api/me", headers={"Authorization": "tma " + init_data(OWNER, age=5 * 3600)})
            assert res.status == 401 and "eskirgan" in (await res.json())["message"]
            res = await app.client.get("/api/me", headers={"Authorization": "tma " + init_data(OWNER, age=3 * 3600)})
            assert res.status == 200

            page = await app.client.get("/")
            csp = page.headers["Content-Security-Policy"]
            assert "base-uri 'none'" in csp and "object-src 'none'" in csp and "form-action 'self'" in csp
            assert page.headers["X-Content-Type-Options"] == "nosniff"
        finally:
            await app.close()
    run(go())


def test_download_streams_file_and_checks_path(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        env = app.env
        try:
            file_pk = await env.ctx.files.save(OWNER, 1, "Отчёт 2026.txt", b"salom dunyo", "f1")
            link = await app.get(f"/api/files/{file_pk}/link")
            path = link["url"].split("/yordamchi", 1)[1]
            res = await app.client.get(path)
            assert res.status == 200 and await res.read() == b"salom dunyo"
            assert res.headers["Content-Type"] == "application/octet-stream"
            assert res.headers["X-Content-Type-Options"] == "nosniff"
            disposition = res.headers["Content-Disposition"]
            assert disposition.startswith("attachment; filename=") and "filename*=UTF-8''%D0%9E%D1%82" in disposition

            # imzoda ASCII bo'lmagan belgi — 403 (avval: TypeError → 500)
            bad = await app.client.get(path.split("&s=")[0] + "&s=" + "ш" * 40)
            assert bad.status == 403

            # bazadagi yo'l data/files dan tashqariga ko'rsatsa — fayl berilmaydi
            outside = tmp_path / "data" / "maxfiy.txt"
            outside.write_text("parol")
            await env.db.set_file_path(file_pk, "../maxfiy.txt")
            res = await app.client.get(path)
            assert res.status == 410 and "parol" not in await res.text()
            assert env.ctx.files.read(await env.db.file(file_pk)) is None
        finally:
            await app.close()
    run(go())


# ----------------------------------------------------------------------------- Hisobotlar API
def test_leader_sees_numbers_hidden(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        env = api.env
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            lead = await g.post(CARD_1, ts(WED, "09:10"))
            await g.react(lead["message_id"], person(MALIKA, "Malika 90 123 45 67", "malika_op"), "👍", ts(WED, "10:00"))
            await api.call("POST", "/api/report/tasks", {"who": "@malika_op", "text": "+998 90 765 43 21 ga qayta qo'ng'iroq"})
            leader = (LEADER, "rahbar_1")

            def names(ov):
                return [r["name"] for r in ov["rows"] + ov["extras"]]
            owner_view = await api.call("GET", "/api/report/overview")
            leader_view = await api.call("GET", "/api/report/overview", user=leader)
            assert any("90 123 45 67" in n for n in names(owner_view))
            assert "123 45 67" not in json.dumps(leader_view, ensure_ascii=False)
            assert any("[raqam]" in n for n in names(leader_view))

            owner_tasks = await api.call("GET", "/api/report/tasks")
            assert "765 43 21" in owner_tasks["items"][0]["text"]
            leader_tasks = await api.call("GET", "/api/report/tasks", user=leader)
            assert "765" not in json.dumps(leader_tasks) and "[raqam]" in leader_tasks["items"][0]["text"]
            detail = await api.call("GET", f"/api/report/tasks/{owner_tasks['items'][0]['id']}", user=leader)
            assert "[raqam]" in detail["text"] and "765" not in detail["text"]

            key = period_key(WEEK, WED.replace(day=19))  # 19–25 oktabr (joriy hafta)
            owner_week = await api.call("GET", f"/api/report/archive/{key}")
            assert any("90 123 45 67" in str(t[0]) for t in owner_week["summary"]["top"])
            leader_week = await api.call("GET", f"/api/report/archive/{key}", user=leader)
            assert leader_week["masked"] and "123 45 67" not in json.dumps(leader_week, ensure_ascii=False)
            # egasining keyingi so'rovi — xotiradagi ko'rsatkichlar rahbar uchun o'zgartirilmagan
            again = await api.call("GET", f"/api/report/archive/{key}")
            assert again["summary"]["top"] == owner_week["summary"]["top"]
            people = await api.call("GET", "/api/report/people", user=leader)
            assert "123 45 67" not in json.dumps(people, ensure_ascii=False)
        finally:
            await api.close()
    run(go())


def test_overview_rejects_far_past_day(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            for day in ("0001-01-01", "1999-12-31", "2026-13-01"):
                res = await api.call("GET", f"/api/report/overview?day={day}", status=400)
                assert "Sana" in res["message"]
            ok = await api.call("GET", "/api/report/overview?day=2026-10-20")
            assert ok["day"] == "2026-10-20"
        finally:
            await api.close()
    run(go())


def test_archive_detail_is_cached(tmp_path, monkeypatch):
    async def go():
        api = await Api().start(tmp_path)
        try:
            archive = api.env.ctx.reports.archive
            real, calls = archive.summarize, []

            async def counting(*args, **kwargs):
                calls.append(args[0].key)
                return await real(*args, **kwargs)
            monkeypatch.setattr(archive, "summarize", counting)
            key = period_key(WEEK, WED.replace(day=12))  # o'tgan hafta (yopilgan)
            first = await api.call("GET", f"/api/report/archive/{key}")
            second = await api.call("GET", f"/api/report/archive/{key}")
            assert len(calls) == 1 and first["summary"] == second["summary"]
            await api.call("POST", "/api/report/statuses", {"emoji": "🦄", "label": "sinov", "positive": True})
            await api.call("GET", f"/api/report/archive/{key}")
            assert len(calls) == 2  # statuslar o'zgardi — qayta hisoblandi
        finally:
            await api.close()
    run(go())


# ----------------------------------------------------------------------------- ishga tushish
class Flaky(fakes.FakeSession):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    async def make_request(self, bot, method, timeout=None):
        if self.errors:
            raise self.errors.pop(0)(method)
        return await super().make_request(bot, method, timeout)


def test_connect_retries_server_errors_and_flood_wait(monkeypatch):
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr(bot_app.asyncio, "sleep", no_sleep)

    def server_error(method):
        return TelegramServerError(method=method, message="Bad Gateway")

    def flood(method):
        return TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=7)

    async def go():
        me = await bot_app.connect(Bot("1:T", session=Flaky([server_error, flood, server_error])))
        assert me.username == "claude_test_bot"
        assert slept == [5, 8, 15]  # 5 s, «7 s kuting» + 1, 15 s
        with pytest.raises(TelegramServerError):
            await bot_app.connect(Bot("1:T", session=Flaky([server_error] * 5)), attempts=3)
    run(go())


def test_update_is_confirmed_only_after_probation(tmp_path, monkeypatch):
    pending = tmp_path / "pending.json"
    pending.write_text("{}")
    announced = []

    async def announce(bot, ctx):
        announced.append(time.monotonic())
        pending.unlink()
    monkeypatch.setattr(bot_app, "announce_update", announce)
    ctx = SimpleNamespace(updater=SimpleNamespace(pending_file=pending))

    async def go():
        started = time.monotonic()
        task = asyncio.create_task(bot_app.announce_after_probation(None, ctx, delay=0.3))
        await asyncio.sleep(0.1)
        assert pending.exists() and not announced  # hali sinov muddati — yiqilsa, launcher qaytaradi
        await task
        assert announced and announced[0] - started >= 0.3 and not pending.exists()
        # yangilanish bo'lmagan oddiy ishga tushish — kutmasdan
        announced.clear()
        await bot_app.announce_after_probation(None, ctx, delay=30)
        assert announced
    run(go())


def test_release_items_cover_installed_files():
    from bot.updater import ALLOWED_TOP, NOT_INSTALLED, RELEASE_ITEMS
    launcher_source = (PROJECT / "launcher.py").read_text(encoding="utf-8")
    assert set(RELEASE_ITEMS) == set(ALLOWED_TOP - NOT_INSTALLED)  # o'rnatilgan har narsa orqaga qaytariladi
    for item in RELEASE_ITEMS:
        assert f'"{item}"' in launcher_source.split("RELEASE_ITEMS", 1)[1].split(")", 1)[0]


# ----------------------------------------------------------------------------- zaxira
def test_backup_works_inside_open_transaction(tmp_path):
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        try:
            await db.add_turn(OWNER, 1, [{"type": "text", "text": "salom"}], "javob")
            await db.conn.execute("BEGIN")  # masalan, izohlar tahlili yozayotgan payt
            await db.conn.execute("INSERT INTO bot_config (key, value) VALUES ('x', 'y')")
            first, second = await asyncio.gather(
                scheduler.make_backup(db, tmp_path / "bot.db", "20261025-0300"),
                scheduler.make_backup(db, tmp_path / "bot.db", "20261025-0300"),
            )
            await db.conn.rollback()
            for data, name in (first, second):
                assert name == "backup-20261025-0300.db" and data[:16] == b"SQLite format 3\x00"
            assert not list(tmp_path.glob("*backup-*"))  # vaqtinchalik nusxalar o'chirildi
            copy = tmp_path / "copy.db"
            copy.write_bytes(first[0])
            conn = sqlite3.connect(copy)
            assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM bot_config WHERE key = 'x'").fetchone()[0] == 0  # yozilmagan
            conn.close()
        finally:
            await db.close()
    run(go())


def test_backup_failure_is_reported(tmp_path, monkeypatch):
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        session = fakes.FakeSession()
        bot = Bot("123456:TESTTOKEN", session=session)

        async def broken(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")
        monkeypatch.setattr(scheduler, "make_backup", broken)
        try:
            assert not await scheduler.send_backup(bot, db, tmp_path / "bot.db", [OWNER], "Asia/Tashkent",
                                                   reason="Kunlik zaxira")
            notice = session.of("SendMessage")[-1]
            assert notice.chat_id == OWNER and "zaxira nusxa olinmadi" in notice.text and "OperationalError" in notice.text
        finally:
            await db.close()
    run(go())


def test_backup_caption_mentions_wal(tmp_path):
    async def go():
        db = await Database.open(tmp_path / "bot.db")
        session = fakes.FakeSession()
        bot = Bot("123456:TESTTOKEN", session=session)
        try:
            assert await scheduler.send_backup(bot, db, tmp_path / "bot.db", [OWNER], "Asia/Tashkent", reason="Zaxira")
            caption = session.documents[-1][2]
            assert "docker compose stop" in caption and "bot.db-wal" in caption and "docker compose start" in caption
        finally:
            await db.close()
    run(go())


# ----------------------------------------------------------------------------- arxiv yig'ish
def test_release_notes_are_scanned_for_secrets(tmp_path, monkeypatch):
    mod = load_module("build_release_notes_scan")
    monkeypatch.setattr(mod, "iter_files", lambda: [PROJECT / "VERSION"])
    notes = tmp_path / "notes.md"
    notes.write_text("Yangilanish\n- kalit: sk-ant-" + "x" * 40 + "\n", encoding="utf-8")  # soxta, ish vaqtida yasaladi
    assert run_main(mod, monkeypatch, tmp_path, notes) == 1
    assert not list((tmp_path / "dist").glob("*.zip"))
    notes.write_text("Yangilanish\n- oddiy izoh\n", encoding="utf-8")
    assert run_main(mod, monkeypatch, tmp_path, notes) == 0


# ----------------------------------------------------------------------------- hisobotlar: nima lid, nima qo'ng'iroq

SAT, SUN, MON = date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)
PHOTO = [{"file_id": "p1", "file_unique_id": "up1", "width": 90, "height": 90}]


async def feed(env, g, **fields):
    g.mid += 1
    message = {"message_id": g.mid, "chat": g.chat(LEADS, "KOMMENTARIYA TA LIDLAR"), **fields}
    await env._feed({"message": message})
    return message


def channel_post(when, **fields):
    return dict(date=when, is_automatic_forward=True, sender_chat={"id": -1003333333333, "type": "channel", "title": "K"},
                **{"from": {"id": 777000, "is_bot": False, "first_name": "Telegram"}}, **fields)


def test_staff_media_and_channel_notices_are_not_leads(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            boss = person(BOSS, "Rahbar", "boss")
            # rahbar: reja (hujjat) va «Yangi narxlar» rasmi — raqamsiz
            plan = await feed(env, g, date=ts(WED, "09:00"), **{"from": boss},
                              document={"file_id": "d1", "file_unique_id": "ud1", "file_name": "reja.pdf"})
            prices = await feed(env, g, date=ts(WED, "09:01"), **{"from": boss}, photo=PHOTO, caption="Yangi narxlar")
            # kanal: e'lon, albomning izohsiz rasmi
            notice = await feed(env, g, **channel_post(ts(WED, "09:02"), text="📢 Diqqat! Ertaga markaz dam oladi."))
            sibling = await feed(env, g, **channel_post(ts(WED, "09:03"), media_group_id="77", photo=PHOTO))
            # haqiqiy kartochkalar: raqamli va raqamsiz (kamida 2 kalit), rasm + raqamli izoh
            card = await feed(env, g, **channel_post(ts(WED, "09:04"), text=CARD_2))
            plain = await feed(env, g, **channel_post(ts(WED, "09:05"), text="Ism: Dilnoza\nKurs: Ingliz tili\nYoshi: 20"))
            scan = await feed(env, g, date=ts(WED, "09:06"), **{"from": boss}, photo=PHOTO, caption="Yangi lid 90 123 45 67")
            malika, sardor = person(MALIKA, "Malika", "malika_op"), person(SARDOR, "Sardor", "sardor")
            for msg in (plan, prices, notice, sibling, card, plain, scan):
                await g.react(msg["message_id"], malika, "👍", ts(WED, "10:00"))
            await g.comment(notice, sardor, "tushunarli", ts(WED, "10:05"))  # e'lon ostidagi izoh — qo'ng'iroq emas

            for msg in (plan, prices, notice, sibling):
                assert await store.lead_by_message(LEADS, msg["message_id"]) is None
            assert (await store.lead_by_message(LEADS, card["message_id"])).name == "Bobur"
            assert (await store.lead_by_message(LEADS, plain["message_id"])).course == "Ingliz tili"
            assert (await store.lead_by_message(LEADS, scan["message_id"])).source == "stub"  # raqamli rasm — sanaladi
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, r.total) for r in ov.rows + ov.extras] == [("Malika", 3)]
            assert ov.new_leads == 2  # kanal kartochkalari; e'lon va albom rasmi lid emas
        finally:
            await env.close()
    run(go())


def test_weekend_leads_are_reminded_on_monday(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_person("operator", username="malika_op", user_id=MALIKA, name="Malika")
            await store.set_dm_ok(MALIKA)
            await g.post(CARD_1, ts(SAT, "18:40"))  # shanba ish vaqti tugashiga oz qolganda
            await g.post(CARD_2, ts(SAT, "21:00"))
            await g.post("Ism: Kamola\nTelefon: 90 555 44 33\nKurs: Massaj", ts(SUN, "08:00"))  # dam kuni
            env.session.clear()
            assert await env.ctx.reports.check_reminders(env.bot, local(SAT, "19:10")) == 0  # ish vaqtidan keyin
            assert await env.ctx.reports.check_reminders(env.bot, local(MON, "09:00")) == 1
            text = env.session.of("SendMessage")[-1].text
            assert "3 ta lid hali belgilanmagan" in text
            assert "24.10 18:40" in text and "24.10 21:00" in text and "25.10 08:00" in text  # eski lidlarda sana
        finally:
            await env.close()
    run(go())


def test_reminder_not_marked_when_nobody_got_it(tmp_path):
    async def go():
        env = await failing_env(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_person("operator", username="malika_op", user_id=MALIKA, name="Malika")
            await store.set_dm_ok(MALIKA)
            await g.post(CARD_1, ts(WED, "09:00"))
            env.session.fail[MALIKA] = "network"
            assert await env.ctx.reports.check_reminders(env.bot, local(WED, "09:40")) == 0
            assert not await env.ctx.reports.check_reminders(env.bot, local(WED, "09:42"))  # 5 daqiqa kutiladi
            env.session.fail.clear()
            assert await env.ctx.reports.check_reminders(env.bot, local(WED, "09:46")) == 1  # lid hali eslatilmagan
        finally:
            await env.close()
    run(go())


def test_leaders_report_retried_on_transient_error_then_owner(tmp_path):
    async def go():
        env = await failing_env(tmp_path)
        try:
            await setup_groups(env)
            service = env.ctx.reports
            env.session.fail[LEADERS] = "network"
            moments = [local(WED, "19:02") + timedelta(minutes=5 * i) for i in range(6)]
            for moment in moments[:5]:
                assert await service.check_daily(env.bot, moment)
                assert not await service.store.report_for_day("2026-10-21")
                assert not [t for t in texts(env, OWNER) if "KUNLIK HISOBOT" in t]  # hali egasiga o'tkazilmagan
            assert await service.check_daily(env.bot, moments[5])  # 6-urinish — egasiga
            owner = [t for t in texts(env, OWNER) if "KUNLIK HISOBOT" in t]
            assert len(owner) == 1 and "rahbarlar guruhiga yuborilmadi" in owner[0]
            assert await service.store.report_for_day("2026-10-21")
        finally:
            await env.close()
    run(go())


def test_report_retry_skips_chats_that_already_got_it(tmp_path):
    async def go():
        env = await failing_env(tmp_path)
        try:
            await setup_groups(env)
            store = env.ctx.reports.store
            second = -1005555555555
            await store.upsert_group(second, "Rahbarlar 2")
            await store.set_group_roles(second, ["leaders"])
            env.session.fail[second] = "network"
            service = env.ctx.reports
            assert await service.check_daily(env.bot, local(WED, "19:02"))
            assert len(texts(env, LEADERS)) == 1 and not await store.report_for_day("2026-10-21")
            env.session.fail.clear()
            assert await service.check_daily(env.bot, local(WED, "19:07"))
            # birinchi guruhga qayta yuborilmadi; ikkinchisiga — xato urinish + yetkazilgan nusxa
            assert len(texts(env, LEADERS)) == 1 and len(texts(env, second)) == 2
            assert await store.report_for_day("2026-10-21")
        finally:
            await env.close()
    run(go())


def test_report_after_closing_time_shows_finished_day(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.react(lead["message_id"], person(MALIKA, "Malika", "malika_op"), "👍", ts(WED, "10:00"))
            env.ctx.reports.now = lambda: local(WED, "19:30")
            env.session.clear()
            await env.send_text("/hisobot")
            text = env.session.of("SendMessage")[-1].text
            assert "KUNLIK HISOBOT · 21.10.2026" in text and "oraliq" not in text and "Malika" in text
            await env.press("rp:send")
            sent = texts(env, LEADERS)
            assert "KUNLIK HISOBOT · 21.10.2026" in sent[-1]  # bo'sh keyingi davr emas
            # kunlik hisobot hali yuborilmagan edi — qo'lda yuborilgani o'sha kun hisoboti bo'ladi, takror kelmaydi
            assert await env.ctx.reports.store.report_for_day("2026-10-21")
            assert not await env.ctx.reports.check_daily(env.bot, local(WED, "19:31"))
            await env.press("rp:send")  # yana bosilsa — qayta yuboriladi (kun allaqachon yopilgan)
            assert len(texts(env, LEADERS)) == len(sent) + 1
        finally:
            await env.close()
    run(go())


def test_status_emoji_is_escaped_in_report(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_status("<3", "yoqdi", True)
            env.ctx.reports.statuses_changed()
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.react(lead["message_id"], person(JASUR_ID, "Jasur", "jasur"), "<3", ts(WED, "10:00"))
            _, text, _ = await env.ctx.reports.compose(WED, local(WED, "15:00"), interim=True)
            assert "&lt;3" in text and "<3" not in text.replace("<pre>", "").replace("</pre>", "")
        finally:
            await env.close()
    run(go())


JASUR_ID = 503


def test_analysis_waits_for_batch_and_warns_owner_once(tmp_path, monkeypatch):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            service = env.ctx.reports
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.comment(lead, person(MALIKA, "Malika", "malika_op"), "ertaga keladi", ts(WED, "10:00"))
            requests = len(env.api.requests)
            # fon sikli: bitta yangi izoh — to'plam yig'ilishi kutiladi
            assert await service.analyze_pending(2, now=local(WED, "10:01")) == 0
            assert len(env.api.requests) == requests

            calls = []

            async def broken(*args, **kwargs):
                calls.append(1)
                raise ClaudeError("Model topilmadi (404)")
            monkeypatch.setattr("bot.reports.analysis.classify_comments", broken)
            env.session.clear()
            await service.analyze_pending(2, bot=env.bot, now=local(WED, "10:05"))  # 2 daqiqadan eski — so'raladi
            warn = [m for m in env.session.of("SendMessage") if m.chat_id == OWNER]
            assert calls and len(warn) == 1 and "REPORT_MODEL" in warn[0].text and "404" in warn[0].text
            service._backoff_until = 0
            await service.analyze_pending(2, bot=env.bot, now=local(WED, "10:10"))
            assert len([m for m in env.session.of("SendMessage") if m.chat_id == OWNER]) == 1  # bir marta
        finally:
            await env.close()
    run(go())


def test_new_operator_gets_welcome_when_reachable(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        env = api.env
        try:
            await env.ctx.reports.store.remember_user(MALIKA, "malika_op", "Malika")  # guruhda ko'ringan
            env.session.clear()
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@malika_op", "name": "Malika"})
            hello = env.session.of("SendMessage")[-1]
            assert hello.chat_id == MALIKA and "operatori sifatida" in hello.text
            assert (await env.ctx.reports.store.people("operator"))[0].dm_ok
            env.session.clear()
            await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@malika_op", "name": "Malika"})
            assert not env.session.of("SendMessage")  # takror qo'shilsa — qayta yozilmaydi
        finally:
            await api.close()
    run(go())


# ----------------------------------------------------------------------------- vazifalar: so'rovlar navbati
class FloodSession(FailingSession):
    flood = False

    async def make_request(self, bot, method, timeout=None):
        if self.flood and type(method).__name__ == "SendMessage" and method.chat_id == TASKS:
            from aiogram.exceptions import TelegramRetryAfter
            raise TelegramRetryAfter(method=method, message="Too Many Requests: retry after 40", retry_after=40)
        return await super().make_request(bot, method, timeout)


def test_task_polls_are_paced_and_flood_is_silent(tmp_path):
    async def go():
        env = await start(tmp_path)
        env.session = FloodSession()
        env.bot.session = env.session
        try:
            await Office(env).setup()
            tasks = env.ctx.reports.tasks
            now = local(date(2026, 10, 22), "10:00")
            env.ctx.reports.now = lambda: now
            who = await tasks.resolve(MALIKA, "malika_op", "Malika")
            for i in range(8):
                await tasks.create(creator_id=OWNER, assignee=who, text=f"vazifa {i}", due=now, repeat=None,
                                   poll_chat=TASKS, now=now)

            def polls():
                return [m for m in env.session.of("SendMessage") if m.chat_id == TASKS]
            assert await tasks.tick(env.bot, now) == 5 and len(polls()) == 5  # bir siklda ko'pi bilan 5 ta
            assert await tasks.tick(env.bot, now + timedelta(seconds=30)) == 3
            # yangi so'rovlar: Telegram «40 s kuting» desa — egasiga xabar yo'q, keyinroq yuboriladi
            for i in range(2):
                await tasks.create(creator_id=OWNER, assignee=who, text=f"yana {i}", due=now, repeat=None,
                                   poll_chat=TASKS, now=now)
            env.session.flood = True
            env.session.clear()
            later = now + timedelta(minutes=1)
            assert await tasks.tick(env.bot, later) == 0
            assert not [m for m in env.session.of("SendMessage") if m.chat_id == OWNER]
            assert tasks.flood_until == later + timedelta(seconds=41)
            env.session.flood = False
            assert await tasks.tick(env.bot, later + timedelta(seconds=30)) == 0  # hali kutish vaqti
            assert await tasks.tick(env.bot, later + timedelta(seconds=45)) == 2
        finally:
            await env.close()
    run(go())


def test_transient_poll_error_is_retried_without_alert(tmp_path):
    async def go():
        env = await start(tmp_path)
        env.session = FailingSession()
        env.bot.session = env.session
        try:
            await Office(env).setup()
            tasks = env.ctx.reports.tasks
            now = local(date(2026, 10, 22), "10:00")
            who = await tasks.resolve(MALIKA, "malika_op", "Malika")
            task = await tasks.create(creator_id=OWNER, assignee=who, text="hisobot", due=now, repeat=None,
                                      poll_chat=TASKS, now=now)
            env.session.fail[TASKS] = "network"
            env.session.clear()
            assert await tasks.tick(env.bot, now) == 0
            assert not [m for m in env.session.calls if type(m).__name__ == "SendMessage" and m.chat_id == OWNER]
            assert (await tasks.store.get(task.id)).next_ask_at == "2026-10-22T05:05:00"  # 5 daqiqadan keyin
            env.session.fail.clear()
            assert await tasks.tick(env.bot, now + timedelta(minutes=5)) == 1
        finally:
            await env.close()
    run(go())


def test_album_caption_on_second_item_keeps_comments_and_reactions(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            first = await feed(env, g, **channel_post(ts(WED, "09:00"), media_group_id="a1", photo=PHOTO))
            await feed(env, g, **channel_post(ts(WED, "09:00"), media_group_id="a1", photo=PHOTO, caption=CARD_1))
            malika = person(MALIKA, "Malika", "malika_op")
            await g.react(first["message_id"], malika, "👍", ts(WED, "10:00"))  # reaksiya albomning 1-rasmida
            await g.comment(first, person(SARDOR, "Sardor", "sardor"), "ertaga keladi", ts(WED, "10:05"))
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert ov.new_leads == 1  # albom — bitta lid
            assert sorted((r.name, r.total) for r in ov.rows + ov.extras) == [("Malika", 1), ("Sardor", 1)]
            assert await store.lead_by_message(LEADS, first["message_id"]) is None  # alohida lid ochilmadi
        finally:
            await env.close()
    run(go())

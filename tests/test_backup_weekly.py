"""v1.10.1: avtomatik zaxira haftada bir marta (BACKUP_DAY, standart — dushanba)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot import handlers, scheduler
from bot.config import ConfigError, _backup_day
from test_flow import Env, run
from test_webapp import App

TZ = ZoneInfo("Asia/Tashkent")


@pytest.mark.parametrize("raw,expected", [
    (None, 0), ("dushanba", 0), ("Yakshanba", 6), ("7", 6), ("1", 0), ("juma", 4), ("monday", 0),
    ("har kuni", None), ("Har  kuni", None), ("daily", None),
])
def test_backup_day_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("BACKUP_DAY", raising=False)
    else:
        monkeypatch.setenv("BACKUP_DAY", raw)
    assert _backup_day() == expected


@pytest.mark.parametrize("raw", ["bozor", "8", "0"])
def test_backup_day_rejects_wrong_value(monkeypatch, raw):
    monkeypatch.setenv("BACKUP_DAY", raw)
    with pytest.raises(ConfigError, match="BACKUP_DAY"):
        _backup_day()


def test_weekly_job_runs_on_monday():
    job = scheduler.DailyJob("backup", "03:30", "Asia/Tashkent", lambda: None, weekday=0)
    sunday = datetime(2026, 10, 25, 8, 0, tzinfo=TZ)
    assert job.next_run(sunday) == datetime(2026, 10, 26, 3, 30, tzinfo=TZ)
    assert job.next_run(datetime(2026, 10, 26, 3, 0, tzinfo=TZ)) == datetime(2026, 10, 26, 3, 30, tzinfo=TZ)
    assert job.next_run(datetime(2026, 10, 26, 3, 30, tzinfo=TZ)) == datetime(2026, 11, 2, 3, 30, tzinfo=TZ)
    daily = scheduler.DailyJob("backup", "03:30", "Asia/Tashkent", lambda: None)
    assert daily.next_run(sunday) == datetime(2026, 10, 26, 3, 30, tzinfo=TZ)
    assert daily.next_run(datetime(2026, 10, 26, 4, 0, tzinfo=TZ)) == datetime(2026, 10, 27, 3, 30, tzinfo=TZ)
    assert scheduler.schedule_label("03:30", 0) == "har dushanba 03:30 da"
    assert scheduler.schedule_label("03:30", None) == "har kuni 03:30 da"


def test_missed_weekly_backup(tmp_path, monkeypatch):
    moment = {"now": datetime(2026, 10, 28, 10, 0)}  # chorshanba

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment["now"].replace(tzinfo=tz)
    monkeypatch.setattr(scheduler, "datetime", Frozen)

    async def go():
        env = await Env().start(tmp_path)
        try:
            db = env.db
            await db.set_config(scheduler.BACKUP_STAMP_KEY, "2026-10-24")  # shanba — dushanbagi zaxira o'tkazib yuborilgan
            assert await scheduler.missed_backup(db, "Asia/Tashkent", "03:30", 0)
            await db.set_config(scheduler.BACKUP_STAMP_KEY, "2026-10-26")  # dushanba yuborilgan
            assert not await scheduler.missed_backup(db, "Asia/Tashkent", "03:30", 0)
            moment["now"] = datetime(2026, 10, 26, 2, 0)  # dushanba, hali 03:30 bo'lmagan — o'tgan haftaniki hisob
            await db.set_config(scheduler.BACKUP_STAMP_KEY, "2026-10-24")
            assert not await scheduler.missed_backup(db, "Asia/Tashkent", "03:30", 0)
            await db.set_config(scheduler.BACKUP_STAMP_KEY, "2026-10-18")  # o'tgan dushanba (19-oktabr) ham o'tib ketgan
            assert await scheduler.missed_backup(db, "Asia/Tashkent", "03:30", 0)
        finally:
            await env.close()
    run(go())


def test_owner_sees_weekly_schedule(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        try:
            text = await handlers.admin_panel_text(app.env.ctx)
            assert "/zaxira — bazaning zaxira nusxasini hozir olish (har dushanba 03:30 da avtomatik ham keladi)" in text
            admin = await app.get("/api/admin")
            assert admin["backup_label"] == "har dushanba 03:30 da"
        finally:
            await app.close()
    run(go())

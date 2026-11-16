"""v1.16: reklama nazorati — lid oqimi to'xtasa ogohlantirish (kalitsiz) va Meta Ads (sarf, lid / «keladi» / vaucher
narxi, kampaniyalar, rad etilgan reklamalar) — faqat egasiga: /reklama, kundalik, kunlik tavsiya, AI suhbati, Mini App."""
import hashlib
import hmac
import json
from datetime import date, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot import meta_ads
from bot.config import load_settings
from bot.meta_ads import PROBLEM_STATUSES, AdAccount, AdObject, GraphMetaAds, InsightRow, MetaAdsError, lead_count
from bot.reports.ads import money
from bot.reports.stats import Schedule
from bot.reports.watch import Profile, duration
from test_ai_tools_v113 import ask, owner_tools
from test_archive import MON, SAT, THU, queue_analysis, seed_week
from test_assistant import LEADER
from test_fix_miniapp import chromium, go_hash, settled
from test_flow import OWNER, run
from test_reports import LEADS, WED, local, setup_groups, start
from test_reports_api import Api

TUE = date(2026, 10, 20)
SUN = date(2026, 10, 25)
MEMBER = 777


def insight(level, name, spend, leads, *, impressions=1000, clicks=20, day=None, campaign=""):
    return InsightRow(level=level, id=name, name=name, campaign=campaign, adset="", day=day, spend=spend,
                      impressions=impressions, reach=impressions // 2, clicks=clicks, leads=leads, frequency=2.0)


class FakeMeta:
    """Soxta Meta Ads provayderi (MetaAdsClient)."""

    def __init__(self):
        self.account_data = AdAccount("act_1", "TTA kabinet", "USD", "Asia/Tashkent", 1)
        self.rows = {"account": [insight("account", "TTA kabinet", 50.0, 8, impressions=10000, clicks=250)],
                     "campaign": [insight("campaign", "Dizayn", 20.0, 3), insight("campaign", "Massaj", 30.0, 5)],
                     "adset": [insight("adset", "Toshkent 25-45", 30.0, 5, campaign="Massaj")],
                     "ad": [insight("ad", "Massaj video", 30.0, 5, campaign="Massaj")]}
        self.daily = [insight("account", "TTA kabinet", 10.0 + n, n, day=f"2026-10-{n:02d}") for n in range(8, 22)]
        self.objects_list = [AdObject("ad", "9", "Massaj video", "DISAPPROVED", campaign="Massaj",
                                      issue="Matnda taqiqlangan da'vo"),
                             AdObject("adset", "5", "Toshkent 25-45", "ACTIVE", campaign="Massaj", daily_budget=25.0)]
        self.error = None
        self.calls = 0

    async def account(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.account_data

    async def insights(self, level, since, until, *, by_day=False):
        self.calls += 1
        if self.error:
            raise self.error
        if by_day:
            return [r for r in self.daily if since.isoformat() <= r.day <= until.isoformat()]
        return list(self.rows.get(level, []))

    async def objects(self, level, statuses, *, cents=100):
        self.calls += 1
        if self.error:
            raise self.error
        return [o for o in self.objects_list if o.level == level and o.status in statuses]


def connect(env, fake=None):
    fake = fake or FakeMeta()
    env.ctx.reports.ads.client = fake
    env.ctx.reports.ads._cache.clear()
    return fake


def owner_texts(env):
    return [m.text for m in env.session.of("SendMessage") if m.chat_id == OWNER]


# --------------------------------------------------------------------------- Meta mijozi (Graph API)
def graph_app(log):
    async def handle(request):
        log.append((request.path, dict(request.query), request.headers.get("Authorization")))
        path, query = request.path, request.query
        if path == "/v26.0/act_123":
            return web.json_response({"id": "act_123", "name": "TTA kabinet", "currency": "USD",
                                      "timezone_name": "Asia/Tashkent", "account_status": 1, "disable_reason": 0,
                                      "amount_spent": "9500", "spend_cap": "10000"})
        if path == "/v26.0/act_123/insights":
            if query.get("after") == "c1":
                return web.json_response({"data": [{
                    "campaign_id": "2", "campaign_name": "Dizayn", "spend": "5.5", "impressions": "800",
                    "reach": "700", "frequency": "1.14", "clicks": "9", "date_start": "2026-10-20", "date_stop": "2026-10-21",
                    "actions": [{"action_type": "onsite_conversion.lead_grouped", "value": "2"}]}]})
            return web.json_response({
                "data": [{"campaign_id": "1", "campaign_name": "Massaj", "spend": "30.25", "impressions": "5000",
                          "reach": "4000", "frequency": "1.25", "clicks": "120", "date_start": "2026-10-20",
                          "date_stop": "2026-10-21",
                          "actions": [{"action_type": "link_click", "value": "120"},
                                      {"action_type": "onsite_conversion.lead_grouped", "value": "6"},
                                      {"action_type": "lead", "value": "6"}]}],
                "paging": {"cursors": {"before": "b0", "after": "c1"}, "next": "https://graph.facebook.com/next"}})
        if path == "/v26.0/act_123/ads":
            return web.json_response({"data": [{
                "id": "9", "name": "Massaj video", "effective_status": "DISAPPROVED", "adset": {"name": "Toshkent 25-45"},
                "campaign": {"name": "Massaj"}, "issues_info": [{"error_summary": "Matnda taqiqlangan da'vo"}]}]})
        if path == "/v26.0/act_123/adsets":
            return web.json_response({"data": [{"id": "5", "name": "Toshkent 25-45", "effective_status": "ACTIVE",
                                                "daily_budget": "2500", "campaign": {"name": "Massaj"}}]})
        if path == "/v26.0/act_999":
            return web.json_response({"error": {"message": "Invalid OAuth access token.", "code": 190}}, status=400)
        if path == "/v26.0/act_777":
            return web.json_response({"error": {"message": "User request limit reached", "code": 17}}, status=400)
        return web.json_response({"error": {"message": "Unsupported get request. Object does not exist", "code": 100}},
                                 status=400)

    app = web.Application()
    app.router.add_get("/{tail:.*}", handle)
    return app


def test_graph_client_reads_account_insights_and_problems():
    async def go():
        log = []
        server = TestServer(graph_app(log))
        await server.start_server()
        try:
            base = str(server.make_url("")).rstrip("/")
            client = GraphMetaAds("EAAtoken123", "123", base_url=base, app_secret="sirr")
            account = await client.account()
            assert (account.id, account.name, account.currency, account.timezone) == (
                "act_123", "TTA kabinet", "USD", "Asia/Tashkent")
            assert account.active and account.status_label == "faol" and account.cap_share == pytest.approx(0.95)

            rows = await client.insights("campaign", TUE, WED)
            assert [(r.name, r.spend, r.leads, r.clicks) for r in rows] == [("Massaj", 30.25, 6, 120), ("Dizayn", 5.5, 2, 9)]
            assert rows[0].day is None and rows[0].cpl == pytest.approx(30.25 / 6) and rows[0].ctr == pytest.approx(2.4)
            path, query, auth = log[1]
            assert path == "/v26.0/act_123/insights" and auth == "Bearer EAAtoken123"
            assert "access_token" not in query and "EAAtoken123" not in json.dumps(query)  # token faqat sarlavhada
            assert query["level"] == "campaign" and "time_increment" not in query
            assert json.loads(query["time_range"]) == {"since": "2026-10-20", "until": "2026-10-21"}
            assert query["appsecret_proof"] == hmac.new(b"sirr", b"EAAtoken123", hashlib.sha256).hexdigest()
            assert log[2][1]["after"] == "c1"  # ikkinchi sahifa — cursor bilan
            await client.insights("account", TUE, WED, by_day=True)
            assert log[-1][1]["time_increment"] == "1"

            ads = await client.objects("ad", PROBLEM_STATUSES)
            assert json.loads(log[-1][1]["effective_status"]) == list(PROBLEM_STATUSES)
            assert (ads[0].name, ads[0].status_label, ads[0].adset, ads[0].issue) == (
                "Massaj video", "rad etildi", "Toshkent 25-45", "Matnda taqiqlangan da'vo")
            assert (await client.objects("adset", ("ACTIVE",)))[0].daily_budget == 25.0  # sent → $

            for account_id, check in (("act_999", lambda e: e.auth and "token" in str(e)),
                                      ("act_777", lambda e: e.retryable),
                                      ("act_555", lambda e: e.auth and "META_AD_ACCOUNT_ID" in str(e))):
                with pytest.raises(MetaAdsError) as err:
                    await GraphMetaAds("EAAtoken123", account_id, base_url=base).account()
                assert check(err.value), (account_id, err.value)
            with pytest.raises(MetaAdsError) as err:  # server ishlamayapti
                await GraphMetaAds("EAAtoken123", "act_123", base_url="http://127.0.0.1:9").account()
            assert err.value.retryable
        finally:
            await server.close()
    run(go())


def test_lead_count_money_and_settings(monkeypatch):
    assert lead_count([{"action_type": "onsite_conversion.lead_grouped", "value": "4"},
                       {"action_type": "lead", "value": "5"}]) == 5  # umumiy «lead» birinchi, ikki marta sanalmaydi
    assert lead_count([{"action_type": "offsite_conversion.fb_pixel_lead", "value": "3"}]) == 3
    assert lead_count([{"action_type": "link_click", "value": "30"}]) == 0 and lead_count(None) == 0
    assert (money(12.5, "USD"), money(15000, "USD"), money(125000, "UZS"), money(None, "USD")) == (
        "$12.50", "$15 000", "125 000 UZS", "—")
    assert (duration(timedelta(minutes=50)), duration(timedelta(hours=2, minutes=5)), duration(timedelta(hours=3))) == (
        "50 daqiqa", "2 soat 5 daqiqa", "3 soat")
    flat = Profile([2.0] * 24, 12, 576)
    assert flat.expected(local(WED, "10:30"), local(WED, "12:00")) == pytest.approx(3.0)
    assert flat.per_hour(Schedule()) == 2.0 and flat.typical_wait(Schedule()) == timedelta(hours=2.3)

    for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_APP_SECRET", "META_API_VERSION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("OWNER_IDS", "5591487950")
    s = load_settings()
    assert (s.meta_access_token, s.meta_ad_account_id, s.meta_problem, s.meta_api_version) == (None, None, None, "v26.0")
    assert meta_ads.build(s) is None
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAB" + "x" * 40)
    s = load_settings()
    assert s.meta_access_token is None and s.meta_problem == "META_AD_ACCOUNT_ID yozilmagan"
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "1234567")
    s = load_settings()
    assert s.meta_ad_account_id == "act_1234567" and s.meta_problem is None
    client = meta_ads.build(s)
    assert isinstance(client, GraphMetaAds) and client.account_id == "act_1234567" and client.version == "v26.0"
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_98765")
    assert load_settings().meta_ad_account_id == "act_98765"
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "mening kabinetim")  # xato — bot ishlayveradi, ulanmagan bo'ladi
    monkeypatch.setenv("META_API_VERSION", "26")
    s = load_settings()
    assert s.meta_access_token is None and "META_AD_ACCOUNT_ID" in s.meta_problem and "META_API_VERSION" in s.meta_problem
    assert s.meta_api_version == "v26.0"


# --------------------------------------------------------------------------- lid oqimi
async def steady_flow(env):
    """7–20 oktabr ish kunlari (12 kun): 09:00–19:00 har soatda 2 ta lid — soatiga 2.0 ta."""
    store, mid = env.ctx.reports.store, 50_000
    for n in range(14):
        day = date(2026, 10, 7 + n)
        if day.weekday() == 6:
            continue
        for hour in range(9, 19):
            for minute in ("10", "40"):
                mid += 1
                await store.save_lead(LEADS, mid, source="post", created_at=_iso(local(day, f"{hour}:{minute}")),
                                      name="Lid", course="Kurs")
    return mid


def _iso(moment):
    from bot.reports.store import iso
    return iso(moment)


async def add_lead(env, moment, mid):
    await env.ctx.reports.store.save_lead(LEADS, mid, source="post", created_at=_iso(moment), name="Yangi", course="Kurs")


def test_silence_alert_and_recovery(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            mid = await steady_flow(env)
            reports = env.ctx.reports
            watch = reports.watch
            await add_lead(env, local(WED, "10:05"), mid + 1)
            env.session.clear()

            profile = await watch.profile(local(WED, "11:00"), await reports.schedule())
            assert (profile.days, profile.leads, profile.hourly[9], profile.hourly[20]) == (12, 240, 2.0, 0.0)
            assert "soatiga ~2.0 ta lid" in await watch.status(local(WED, "11:00"))
            assert not await watch.check(env.bot, local(WED, "11:00"))  # 55 daqiqa: ~1.8 ta kutilgan — erta
            assert await watch.check(env.bot, local(WED, "12:30"))  # 2 soat 25 daqiqa: ~4.8 ta
            text = owner_texts(env)[-1]
            assert "Lid kelmay qoldi" in text and "10:05 dan beri (2 soat 25 daqiqa)" in text and "~5 ta" in text
            assert "Meta Ads ulansa" in text and "/reklama o'chir" in text
            assert not await watch.check(env.bot, local(WED, "12:40"))  # shu jimlik — bir marta
            assert len(owner_texts(env)) == 1

            await add_lead(env, local(WED, "13:00"), mid + 2)
            assert not await watch.check(env.bot, local(WED, "13:05"))
            assert owner_texts(env)[-1] == ("✅ <b>Lidlar yana tushyapti</b>: 13:00 da yangi lid. Tanaffus — 2 soat 55 "
                                            "daqiqa (10:05 dan).")

            # ertasiga ertalabdan lid yo'q; kechqurun va yakshanba — tekshirilmaydi
            assert not await watch.check(env.bot, local(WED, "19:30"))
            assert await watch.check(env.bot, local(THU, "12:00"))
            assert "Ish boshlanganidan (09:00) beri (3 soat; oxirgi lid — 21.10 13:00)" in owner_texts(env)[-1]
            assert not await watch.check(env.bot, local(SUN, "12:00"))

            # o'chirilsa — ogohlantirish yo'q
            await reports.save_schedule({"ads_alerts": False})
            watch._next = None
            await env.ctx.db.set_config("report:silence", "")
            assert not await watch.check(env.bot, local(THU, "15:00"))
            assert "o'chiq" in await watch.status(local(THU, "15:00"))
        finally:
            await env.close()
    run(go())


def test_silence_needs_enough_history(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            for n in range(10):  # 14 kunda 10 ta lid — oqim siyrak
                await add_lead(env, local(date(2026, 10, 8 + n), "10:00"), 60_000 + n)
            watch = env.ctx.reports.watch
            assert await watch.profile(local(WED, "16:00"), await env.ctx.reports.schedule()) is None
            assert not await watch.check(env.bot, local(WED, "16:00"))
            assert "ma'lumot kam" in await watch.status(local(WED, "16:00"))
        finally:
            await env.close()
    run(go())


def test_silence_alert_explains_with_meta(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            mid = await steady_flow(env)
            await add_lead(env, local(WED, "09:20"), mid + 1)
            fake = connect(env)
            fake.rows["account"] = [insight("account", "TTA kabinet", 0.0, 0)]
            watch = env.ctx.reports.watch
            assert await watch.check(env.bot, local(WED, "12:00"))
            text = owner_texts(env)[-1]
            assert "Meta: kabinet faol · bugun $0.00 sarflandi · Meta bo'yicha 0 ta lid (guruhga 1 ta tushdi)" in text
            assert "Bugun reklamaga pul sarflanmagan" in text and "Muammoli reklamalar: 1" in text

            fake.rows["account"] = [insight("account", "TTA kabinet", 25.0, 12)]  # reklama ishlayapti, lid kelmayapti
            env.ctx.reports.ads._cache.clear()
            await env.ctx.db.set_config("report:silence", "")
            watch._next = None
            assert await watch.check(env.bot, local(WED, "12:30"))
            assert "forma → Telegram ulanishini tekshiring" in owner_texts(env)[-1]
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- Meta Ads: hisob-kitob va matnlar
def test_summary_journal_and_command(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            reports.now = lambda: local(SAT, "21:00")
            await env.db.add_member(MEMBER, "Ali", "ali")

            # ulanmagan
            env.session.clear()
            await env.send_text("/reklama")
            text = env.session.of("SendMessage")[-1].text
            assert "<b>Meta Ads</b>: ulanmagan" in text and "META_ACCESS_TOKEN" in text and "Lid oqimi" in text
            assert "Reklama (Meta)" not in await reports.archive.journal_text(local(WED, "20:00"))

            fake = connect(env)
            week = await reports.ads.summary(MON, SAT, local(SAT, "21:00"))
            assert (week["spend"], week["meta_leads"], week["our_leads"]) == (50.0, 8, 5)
            assert (week["cost_per_meta_lead"], week["cost_per_our_lead"], week["ctr_pct"], week["cpm"]) == (6.25, 10.0, 2.5, 5.0)
            if week["main_result"]:
                assert week["cost_per_main_result"] == round(50 / week["main_result"], 2)
            assert "vouchers" not in week  # vaucher guruhi yo'q

            journal = await reports.archive.journal_text(local(WED, "20:00"))
            assert "📣 <b>Reklama (Meta)</b>" in journal and "<b>Bugun</b>: $50.00 · Meta 8 lid · guruhga 1 ($50.00)" in journal
            assert "Meta'da bugun 8 ta lid, guruhga 1 ta tushdi" in journal and "«Massaj video» (rad etildi)" in journal
            fake.account_data = AdAccount("act_1", "TTA kabinet", "USD", "America/Los_Angeles", 1)
            reports.ads._cache.clear()
            journal = await reports.archive.journal_text(local(WED, "20:00"))
            assert "kabinet vaqti (America/Los_Angeles)" in journal and "Meta'da bugun" not in journal
            fake.account_data = AdAccount("act_1", "TTA kabinet", "USD", "Asia/Tashkent", 1)
            reports.ads._cache.clear()

            env.session.clear()
            await env.send_text("/reklama")
            assert env.session.of("SendMessage")[0].text == "📣 Reklama ma'lumoti olinmoqda…"
            assert env.session.of("DeleteMessage")
            text = env.session.of("SendMessage")[-1].text
            assert "<b>Meta Ads</b> · «TTA kabinet» · faol · USD" in text
            for part in ("<b>Bugun</b>", "<b>Kecha</b>", "<b>7 kun</b>", "<b>30 kun</b>", "<b>Kampaniyalar (7 kun)</b>",
                         "• Massaj — $30.00 · 5 lid ($6.00)", "• Dizayn — $20.00 · 3 lid ($6.67)",
                         "Muammoli reklamalar: 1</b>", "reklama «Massaj video» — rad etildi: Matnda taqiqlangan da'vo"):
                assert part in text, part
            assert text.index("Massaj — $30.00") < text.index("Dizayn — $20.00")  # sarf bo'yicha

            await env.send_text("/reklama o'chir")
            assert (await reports.schedule()).ads_alerts is False
            await env.send_text("/ads yoq")
            assert (await reports.schedule()).ads_alerts is True
            await env.send_text("/reklama", user_id=MEMBER)
            assert env.session.of("SendMessage")[-1].text == "Bu buyruq faqat bot egasi uchun."
        finally:
            await env.close()
    run(go())


def test_meta_problem_alerts_once(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            ads = env.ctx.reports.ads
            assert await ads.check(env.bot, local(WED, "10:00")) == 0  # ulanmagan
            fake = connect(env)
            fake.account_data = AdAccount("act_1", "TTA kabinet", "USD", "Asia/Tashkent", 3, amount_spent=9500,
                                          spend_cap=10000)
            env.session.clear()
            assert await ads.check(env.bot, local(WED, "10:00")) == 3
            text = owner_texts(env)[-1]
            assert "Meta reklama: muammo" in text and "to'lanmagan qarz bor" in text and "95%" in text
            assert "reklama «Massaj video» — rad etildi: Matnda taqiqlangan da'vo" in text
            assert await ads.check(env.bot, local(WED, "10:10")) == 0  # 30 daqiqada bir marta
            assert await ads.check(env.bot, local(WED, "10:30")) == 0  # o'sha muammolar — qayta aytilmaydi
            assert len(owner_texts(env)) == 1

            fake.objects_list = [o for o in fake.objects_list if o.status != "DISAPPROVED"]  # hal bo'ldi
            assert await ads.check(env.bot, local(WED, "11:00")) == 0
            fake.objects_list.append(AdObject("ad", "9", "Massaj video", "DISAPPROVED"))  # yana rad etildi
            assert await ads.check(env.bot, local(WED, "11:30")) == 1

            await env.ctx.reports.save_schedule({"ads_alerts": False})
            fake.objects_list.append(AdObject("campaign", "3", "Dizayn", "WITH_ISSUES"))
            assert await ads.check(env.bot, local(WED, "12:00")) == 0
            await env.ctx.reports.save_schedule({"ads_alerts": True})

            fake.error = MetaAdsError("Meta token yaroqsiz yoki bekor qilingan", auth=True)
            before = len(owner_texts(env))
            assert await ads.check(env.bot, local(WED, "12:30")) == 0
            assert owner_texts(env)[-1] == "⚠️ <b>Meta Ads</b>: Meta token yaroqsiz yoki bekor qilingan"
            assert await ads.check(env.bot, local(WED, "13:00")) == 0
            assert len(owner_texts(env)) == before + 1  # sutkada bir marta
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- kunlik tavsiya va AI suhbati
def test_advice_digest_and_ai_tool(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            await queue_analysis(env)
            reports = env.ctx.reports
            await reports.analyze_pending()
            advisor = reports.advisor
            assert "ads" not in await advisor.digest(WED, local(WED, "20:30"))  # ulanmagan

            fake = connect(env)
            data = (await advisor.digest(WED, local(WED, "20:30")))["ads"]
            assert data["account"] == {"name": "TTA kabinet", "currency": "USD", "status": "faol", "timezone": "Asia/Tashkent"}
            days = data["days"]
            assert len(days) == 14 and days[0]["date"] == "2026-10-08" and days[-1]["date"] == "2026-10-21"
            assert days[-1] == {"date": "2026-10-21", "spend": 31.0, "meta_leads": 21, "our_leads": 1, "main_result": 0}
            assert [d["our_leads"] for d in days if d["date"] in ("2026-10-19", "2026-10-20")] == [1, 1]
            assert [c["name"] for c in data["campaigns_7d"]] == ["Massaj", "Dizayn"]
            assert data["campaigns_7d"][0]["cost_per_meta_lead"] == 6.0 and data["ads_7d"][0]["campaign"] == "Massaj"
            assert data["active_budgets"] == [{"level": "ad set", "name": "Toshkent 25-45", "status": "faol",
                                               "campaign": "Massaj", "daily_budget": 25.0}]
            assert data["problems"][0]["status"] == "rad etildi" and data["last_7_days"]["spend"] == 50.0
            packed = advisor.pack(await advisor.digest(WED, local(WED, "20:30")))
            assert '"ads":' in packed and "998" not in packed

            fake.error = MetaAdsError("Meta serveri vaqtincha javob bermayapti", retryable=True)
            reports.ads._cache.clear()
            assert (await advisor.digest(WED, local(WED, "20:30")))["ads"] == {
                "unavailable": "Meta serveri vaqtincha javob bermayapti"}
            fake.error = None
            reports.ads._cache.clear()

            reports.now = lambda: local(SAT, "21:00")
            tools = owner_tools(env)
            found = await ask(tools, "ads_stats", period="this_week")
            assert found["level"] == "campaign" and found["period"]["basis"].startswith("kalendar")
            assert [r["name"] for r in found["rows"]] == ["Massaj", "Dizayn"] and found["totals"]["our_leads"] == 5
            assert found["account"]["name"] == "TTA kabinet" and found["problems"][0]["name"] == "Massaj video"
            assert (await ask(tools, "ads_stats", level="hammasi"))["error"].startswith("level:")
            settings = (await ask(tools, "bot_status", section="settings"))["settings"]
            assert settings["ads_control"] == {"alerts": True, "meta_ads": "ulangan"}

            reports.ads.client = None
            assert "Meta Ads ulanmagan" in (await ask(tools, "ads_stats"))["error"]
            assert "ads_stats" not in reports.ai_tools(masked=True).names  # rahbarlarga — yo'q
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- Mini App
def test_ads_in_mini_app(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            env = api.env
            await setup_groups(env)
            data = await api.call("GET", "/api/report/settings")
            assert data["ads"] == {"connected": False, "problem": None} and data["schedule"]["ads_alerts"] is True
            assert data["watch"].startswith("🔔 Lid oqimi")
            res = await api.call("PATCH", "/api/report/schedule", {"ads_alerts": False})
            assert res["schedule"]["ads_alerts"] is False
            connect(env)
            data = await api.call("GET", "/api/report/settings")
            assert data["ads"] == {"connected": True, "problem": None, "account": "TTA kabinet", "status": "faol",
                                   "active": True, "currency": "USD"}
            await env.ctx.reports.store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            await api.call("GET", "/api/report/settings", user=(LEADER, "rahbar_1"), status=403)

            env.ctx.reports.ads.client = None
            await api.call("PATCH", "/api/report/schedule", {"ads_alerts": True})
            async with chromium() as open_page:
                page, base = await open_page(api, width=360)
                await page.goto(base + "#r-set")
                await settled(page)
                text = await page.evaluate("document.querySelector('#screen').textContent")
                assert "Reklama nazorati" in text and "Meta Ads" in text and "ulanmagan" in text
                async with page.expect_response(lambda r: r.url.endswith("/api/report/schedule")):
                    await page.evaluate("document.querySelector('[data-rtoggle=r-ads]').click()")
                await page.wait_for_function("document.querySelector('#toast').textContent === 'Ogohlantirishlar o\\'chirildi'")
                assert (await env.ctx.reports.schedule()).ads_alerts is False
                await page.click("[data-action=r-meta]")
                await page.wait_for_selector(".sheet .pre-text")
                sheet = await page.evaluate("document.querySelector('.sheet').textContent")
                assert "META_ACCESS_TOKEN" in sheet and "ads_read" in sheet and "Ulanmagan" in sheet
                await page.click(".sheet", position={"x": 20, "y": 20})

                connect(env)
                await go_hash(page, "#r-tasks")
                await go_hash(page, "#r-set")
                await page.click("[data-action=r-meta]")
                await page.wait_for_selector(".sheet .sheet-sub")
                assert "TTA kabinet · faol · USD" in await page.evaluate("document.querySelector('.sheet').textContent")
        finally:
            await api.close()
    run(go())

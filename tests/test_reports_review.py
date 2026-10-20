"""v1.7: mustaqil tekshiruvda topilgan holatlar — har biri alohida test.

Vaqt o'zgarsa takroriy hisobot, yetkazilmagan hisobot, oddiy xabarga reaksiya, bo'shatilgan username,
istisnolar, a'zo operatorning eslatmalari, uzun hisobot, guruh superguruhga aylanishi va boshqalar."""
import asyncio
from datetime import date, timedelta

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError

from bot.access import hash_password
from bot.handlers import PASSWORD_KEY
from bot.reports.render import split_message
from test_flow import OWNER, run
from test_reports import (
    BOSS, CARD_1, CARD_2, JASUR, LEADERS, LEADS, MALIKA, SARDOR, WED, AdminSession, Group, local, person,
    setup_groups, start, ts,
)
from test_reports_api import Api

THU = date(2026, 10, 22)


class FailingSession(AdminSession):
    """Ba'zi chatlarga xabar yuborilmaydi: chat_id → «forbidden» yoki «network»."""

    def __init__(self) -> None:
        super().__init__()
        self.fail: dict[int, str] = {}

    async def make_request(self, bot, method, timeout=None):
        if type(method).__name__ == "SendMessage" and method.chat_id in self.fail:
            self.calls.append(method)
            if self.fail[method.chat_id] == "network":
                raise TelegramNetworkError(method=method, message="HTTP Client says - ClientConnectorError")
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was kicked from the supergroup chat")
        return await super().make_request(bot, method, timeout)


async def failing_env(tmp_path):
    env = await start(tmp_path)
    env.session = FailingSession()
    env.bot.session = env.session
    return env


def texts(env, chat_id):
    return [m.text for m in env.session.of("SendMessage") if m.chat_id == chat_id]


# ---------------------------------------------------------------- 1. vaqt o'zgarsa — takroriy hisobot yo'q
def test_changing_report_time_does_not_resend(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            service = env.ctx.reports
            assert await service.check_daily(env.bot, local(WED, "19:00"))
            assert len(texts(env, LEADERS)) == 1
            # o'sha kuni 19:30 da vaqt 19:15 ga — chorshanba hisoboti qayta yuborilmaydi
            await service.save_schedule({"time": "19:15"})
            assert not await service.check_daily(env.bot, local(WED, "19:30"))
            # ertasi 12:00 da 18:00 ga — chorshanba (yangi davr bilan) «kechikkan» bo'lib qayta kelmaydi
            await service.save_schedule({"time": "18:00"})
            assert not await service.check_daily(env.bot, local(THU, "12:00"))
            assert len(texts(env, LEADERS)) == 1
            assert await service.check_daily(env.bot, local(THU, "18:00"))  # payshanba — yangi vaqtda
            assert "22.10.2026" in texts(env, LEADERS)[-1]
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- 2. yetkazilmagan hisobot yo'qolmaydi
def test_report_falls_back_to_owner_when_group_is_closed(tmp_path):
    async def go():
        env = await failing_env(tmp_path)
        try:
            await setup_groups(env)
            env.session.fail[LEADERS] = "forbidden"  # botni rahbarlar guruhidan chiqarishgan
            assert await env.ctx.reports.check_daily(env.bot, local(WED, "19:00"))
            owner = texts(env, OWNER)[-1]
            assert "rahbarlar guruhiga yuborilmadi — sizga yuborildi" in owner and "KUNLIK HISOBOT" in owner
            assert "bot was kicked" in owner
            assert await env.ctx.reports.store.report_for_day("2026-10-21")
        finally:
            await env.close()
    run(go())


def test_send_now_reports_where_it_went(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            env = api.env
            session = FailingSession()
            env.session = session
            env.bot.session = session
            g = Group(env)
            await g.join(LEADS, "L")
            await api.call("PATCH", f"/api/report/groups/{LEADS}", {"roles": ["leads"]})
            await g.join(LEADERS, "R")
            await api.call("PATCH", f"/api/report/groups/{LEADERS}", {"roles": ["leaders"]})
            assert (await api.call("POST", "/api/report/send", {}))["target"] == "leaders"
            session.fail[LEADERS] = "forbidden"
            assert (await api.call("POST", "/api/report/send", {}))["target"] == "owner"
            session.fail[OWNER] = "network"
            failed = await api.call("POST", "/api/report/send", {}, status=502)
            assert failed["error"] == "send_failed"
        finally:
            await api.close()
    run(go())


def test_report_retried_after_network_failure(tmp_path):
    async def go():
        env = await failing_env(tmp_path)
        try:
            await setup_groups(env)
            service = env.ctx.reports
            env.session.fail.update({LEADERS: "network", OWNER: "network"})
            assert await service.check_daily(env.bot, local(WED, "19:00"))
            assert not await service.store.report_for_day("2026-10-21")  # hech qayerga yetmadi — belgilanmaydi
            assert not await service.check_daily(env.bot, local(WED, "19:02"))  # 5 daqiqa kutiladi
            env.session.fail.clear()
            assert await service.check_daily(env.bot, local(WED, "19:05"))
            assert "KUNLIK HISOBOT" in texts(env, LEADERS)[-1]
            assert await service.store.report_for_day("2026-10-21")
            assert not await service.check_daily(env.bot, local(WED, "19:10"))
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- 3. oddiy xabarga reaksiya — qo'ng'iroq emas
def test_reaction_on_plain_message_is_not_a_call(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await g.post(CARD_1, ts(WED, "09:00"))  # bot ko'rgan birinchi xabar
            g.mid += 1
            notice = {"message_id": g.mid, "date": ts(WED, "09:05"), "chat": g.chat(LEADS, "L"),
                      "from": person(BOSS, "Rahbar", "boss"), "text": "Bugun 17:00 da yig'ilish bor"}
            await env._feed({"message": notice})
            await g.react(notice["message_id"], person(MALIKA, "Malika"), "👍", ts(WED, "09:10"))
            assert await store.lead_by_message(LEADS, notice["message_id"]) is None
            # bot ko'rmagan xabarlar: qo'shilishidan oldingi post va boshqa bot yozgan kartochka (botlar bir-birini
            # ko'rmaydi) — operator belgisi sanaladi
            await g.react(900, person(SARDOR, "Sardor"), "🆒", ts(WED, "09:20"))
            await g.react(notice["message_id"] + 5, person(SARDOR, "Sardor"), "👍", ts(WED, "09:25"))
            assert (await store.lead_by_message(LEADS, 900)).source == "stub"
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, r.total, dict(r.counts)) for r in ov.rows] == [("Sardor", 2, {"🆒": 1, "👍": 1})]
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- 4. bo'shatilgan username boshqa odamga huquq bermaydi
def test_reused_username_does_not_inherit_roles(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            env = api.env
            store = env.ctx.reports.store
            await store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await api.call("GET", "/api/me", user=(801, "rahbar_1"))  # rahbar kirdi — ID bog'landi
            g = Group(env)
            await g.join(LEADS, "L")
            await store.set_group_roles(LEADS, ["leads"])
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            other = await g.post(CARD_2, ts(WED, "09:01"))
            await g.react(lead["message_id"], person(MALIKA, "Malika", "malika_op"), "👍", ts(WED, "09:30"))
            # ikkalasi username'ini almashtirdi, bo'shagan nomlarni boshqalar oldi
            await api.call("GET", "/api/me", user=(801, "yangi_rahbar"))
            denied = await api.call("GET", "/api/me", user=(999, "rahbar_1"), status=403)
            assert denied["error"] == "no_access"
            assert await store.roles_of(999, "rahbar_1") == set()
            await g.react(other["message_id"], person(990, "Begona", "malika_op"), "👎", ts(WED, "09:40"))
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, r.total) for r in ov.rows] == [("Malika", 1)]
            assert [(r.name, r.total) for r in ov.extras] == [("Begona", 1)]
            assert [p.username for p in await store.people("leader")] == ["yangi_rahbar"]  # ro'yxatda yangi nom
        finally:
            await api.close()
    run(go())


# ---------------------------------------------------------------- 5. istisnolar hech qayerda sanalmaydi
def test_excluded_people_are_ignored_everywhere(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            service = env.ctx.reports
            await store.add_person("excluded", username="boss", user_id=None)
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await env.send_text("/start", user_id=MALIKA, **{"from": person(MALIKA, "Malika", "malika_op")})
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            boss = person(BOSS, "Rahbar", "boss")
            await g.comment(lead, boss, "Uzoq ekan, ertaga yana qiling", ts(WED, "09:10"))
            await g.react(lead["message_id"], boss, "👍", ts(WED, "09:11"))
            assert await store.pending_comments(10) == []  # AI'ga yuborilmaydi
            # (eski versiyada tahlil qilingan bo'lsa ham) sabab va qayta qo'ng'iroq sanalmaydi
            event = (await store.events_between("2026-10-21T00:00:00", "2026-10-22T00:00:00"))[0]
            await store.save_analysis(event.id, is_call=True, status="👎", reason="uzoq", callback="ertaga")
            await store.commit()
            ov = await service.overview(WED, local(WED, "15:00"))
            assert (ov.comments, ov.reasons, ov.callbacks, ov.unmarked) == (0, [], 0, 1)
            assert ov.courses == [] and all(r.total == 0 for r in ov.rows)
            env.session.clear()
            assert await service.check_reminders(env.bot, local(WED, "09:40")) == 1  # rahbar belgisi eslatmani yopmaydi
            assert "Aziza Karimova" in texts(env, MALIKA)[-1]
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- 6. parol bilan kirgan operator ham eslatma oladi
def test_member_operator_gets_reminders_and_staff_can_use_password(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            store = env.ctx.reports.store
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await store.add_person("operator", username="sardor_op", user_id=None, name="Sardor")
            await env.db.add_member(MALIKA, "Malika", "malika_op")
            await env.send_text("/start", user_id=MALIKA, **{"from": person(MALIKA, "Malika", "malika_op")})
            people = {p.username: p for p in await store.people("operator")}
            assert people["malika_op"].dm_ok and people["malika_op"].user_id == MALIKA
            assert not any("operatori sifatida" in (m.text or "") for m in env.session.of("SendMessage"))  # a'zo — odatdagi bot

            # parol o'rnatilgan: operator «salom» yozsa — yo'riqnoma, parolni yozsa — a'zo bo'ladi
            await env.db.set_config(PASSWORD_KEY, await asyncio.to_thread(hash_password, "maxfiy123"))
            sardor = {"from": person(SARDOR, "Sardor", "sardor_op")}
            await env.send_text("salom", user_id=SARDOR, **sardor)
            assert "operatori sifatida" in env.session.of("SendMessage")[-1].text
            await env.send_text("maxfiy123", user_id=SARDOR, **sardor)
            member = await env.db.member(SARDOR)
            assert member is not None and member.active
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- uzun hisobot — bo'laklarga
def test_split_long_report_keeps_table_tags():
    table = "<pre>" + "\n".join(f"Operator{i:03d}   12   5   7" for i in range(300)) + "</pre>"
    text = "📊 <b>KUNLIK HISOBOT</b>\n<i>davr</i>\n\n" + table + "\n📥 Yangi lidlar: 5"
    parts = split_message(text, limit=1000)
    assert len(parts) > 3 and "".join(parts).count("Operator") == 300
    for part in parts:
        assert len(part.encode("utf-16-le")) // 2 <= 1000
        assert part.count("<pre>") == part.count("</pre>")
    assert parts[0].startswith("📊") and parts[-1].endswith("📥 Yangi lidlar: 5")
    assert split_message("qisqa") == ["qisqa"]


def test_long_report_is_sent_in_parts(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            for i in range(140):
                await store.add_person("operator", username=None, user_id=10_000 + i, name=f"Operator {i}")
            await g.post(CARD_1, ts(WED, "09:00"))
            assert await env.ctx.reports.check_daily(env.bot, local(WED, "19:00"))
            parts = texts(env, LEADERS)
            assert len(parts) >= 2 and all(len(t) < 4096 for t in parts)
            assert parts[0].startswith("📊") and "JAMI" in parts[-1]
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- guruh superguruhga aylandi
def test_group_migration_keeps_roles_without_new_prompt(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = Group(env)
            await g.join(-4001, "Rahbarlar (oddiy)")
            store = env.ctx.reports.store
            await store.set_group_roles(-4001, ["leaders"])
            env.session.clear()
            new_id = -1004001
            # superguruhdagi birinchi xizmat xabari eski guruhdagidan oldin keldi
            await env._feed({"message": {"message_id": 1, "date": ts(WED, "10:00"), "chat": g.chat(new_id, "Rahbarlar"),
                                         "from": person(OWNER, "Shohrux"), "migrate_from_chat_id": -4001}})
            await env._feed({"message": {"message_id": 77, "date": ts(WED, "10:00"),
                                         "chat": {"id": -4001, "type": "group", "title": "Rahbarlar (oddiy)"},
                                         "from": person(OWNER, "Shohrux"), "migrate_to_chat_id": new_id}})
            assert (await store.group(new_id)).roles == ("leaders",) and await store.group(-4001) is None
            assert not env.session.of("SendMessage")  # egasidan qayta so'ralmadi
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- mayda holatlar
def test_reminders_are_batched_and_not_duplicated(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            await store.add_person("operator", username=None, user_id=MALIKA, name="Malika (ID)")  # ikkinchi yozuv
            await env.send_text("/start", user_id=MALIKA, **{"from": person(MALIKA, "Malika", "malika_op")})
            assert len(await store.people("operator")) == 1  # bir odam — bitta yozuv
            await g.post(CARD_1, ts(WED, "09:00"))
            await g.post(CARD_2, ts(WED, "09:05"))
            env.session.clear()
            service = env.ctx.reports
            assert await service.check_reminders(env.bot, local(WED, "09:31")) == 1
            assert "1 ta lid" in texts(env, MALIKA)[-1]
            assert await service.check_reminders(env.bot, local(WED, "09:36")) == 0  # 15 daqiqa o'tmagan
            assert await service.check_reminders(env.bot, local(WED, "09:46")) == 1
            assert "Bobur" in texts(env, MALIKA)[-1] and len(texts(env, MALIKA)) == 2
        finally:
            await env.close()
    run(go())


def test_removed_reaction_unmarks_lead(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.react(lead["message_id"], person(JASUR, "Jasur"), "👍", ts(WED, "09:10"))
            await g.react(lead["message_id"], person(JASUR, "Jasur"), None, ts(WED, "09:11"))  # olib tashladi
            store = env.ctx.reports.store
            assert [x.name for x in await store.unmarked_leads("2026-10-21T00:00:00", "2026-10-21T05:00:00")] == ["Aziza Karimova"]
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert ov.unmarked == 1 and ov.totals.total == 0
        finally:
            await env.close()
    run(go())


def test_leader_sees_only_operators_and_work_hours_validated(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            store = api.env.ctx.reports.store
            await store.add_person("leader", username="rahbar_1", user_id=None, name="Rahbar")
            await store.add_person("excluded", username="boss", user_id=None)
            await store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            people = await api.call("GET", "/api/report/people", user=(801, "rahbar_1"))
            assert [p["label"] for p in people["operators"]] == ["Malika"]
            assert people["excluded"] == [] and people["leaders"] == []
            full = await api.call("GET", "/api/report/people")
            assert len(full["excluded"]) == 1 and len(full["leaders"]) == 1
            bad = await api.call("PATCH", "/api/report/schedule", {"work_start": "19:00", "work_end": "09:00"}, status=400)
            assert "Ish vaqti" in bad["message"]
            ok = await api.call("PATCH", "/api/report/schedule", {"work_start": "08:30", "work_end": "18:00"})
            assert (ok["schedule"]["work_start"], ok["schedule"]["work_end"]) == ("08:30", "18:00")
        finally:
            await api.close()
    run(go())


def test_one_failing_step_does_not_block_daily_report(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            service = env.ctx.reports

            async def broken(*args, **kwargs):
                raise RuntimeError("eslatma buzildi")

            service.check_reminders = broken
            await service.tick(env.bot, local(WED, "19:00"))
            assert "KUNLIK HISOBOT" in texts(env, LEADERS)[-1]
        finally:
            await env.close()
    run(go())


def test_concurrent_reactions_on_old_post_create_one_lead(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            await asyncio.gather(*(
                g.react(500, person(uid, name), "👍", ts(WED, "09:00") + i)
                for i, (uid, name) in enumerate([(MALIKA, "Malika"), (SARDOR, "Sardor"), (JASUR, "Jasur")])
            ))
            store = env.ctx.reports.store
            events = await store.events_between("2026-10-21T00:00:00", "2026-10-22T00:00:00")
            assert len(events) == 3 and len({e.lead_id for e in events}) == 1
        finally:
            await env.close()
    run(go())


def test_report_time_change_period_gap_is_one_off(tmp_path):
    """Vaqt kechroqqa surilsa, keyingi davr yangi vaqtdan boshlanadi (bir martalik farq) — takror yuborilmaydi."""
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            service = env.ctx.reports
            assert await service.check_daily(env.bot, local(WED, "19:00"))
            await service.save_schedule({"time": "20:00"})
            assert not await service.check_daily(env.bot, local(WED, "20:00"))
            assert await service.check_daily(env.bot, local(THU, "20:00"))
            assert len(texts(env, LEADERS)) == 2
            start_, end = (local(WED, "20:00"), local(THU, "20:00"))
            assert f"{start_:%d.%m %H:%M} → {end:%d.%m %H:%M}" in texts(env, LEADERS)[-1]
            assert end - start_ == timedelta(days=1)
        finally:
            await env.close()
    run(go())


# ---------------------------------------------------------------- ikkinchi tekshiruv
def test_staff_password_guesses_are_limited(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await env.ctx.reports.store.add_person("operator", username="sardor_op", user_id=None, name="Sardor")
            await env.db.set_config(PASSWORD_KEY, await asyncio.to_thread(hash_password, "maxfiy123"))
            sardor = {"from": person(SARDOR, "Sardor", "sardor_op")}
            for guess in ("parol111", "parol222", "parol333", "parol444", "parol555"):
                await env.send_text(guess, user_id=SARDOR, **sardor)
                assert "operatori sifatida" in env.session.of("SendMessage")[-1].text  # yo'riqnoma chiqaveradi
            assert env.ctx.limiter.locked_for(SARDOR) > 0
            await env.send_text("maxfiy123", user_id=SARDOR, **sardor)  # bloklangan — kira olmaydi
            assert await env.db.member(SARDOR) is None
        finally:
            await env.close()
    run(go())


def test_instruction_comment_does_not_mark_lead(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            lead = await g.post(CARD_1, ts(WED, "09:00"))
            await g.comment(lead, person(BOSS, "Rahbar", "boss"), "Malika, bunga ertaga qiling", ts(WED, "09:05"))
            # tahlil kutilayotgan izoh belgilangan hisoblanadi; AI «qo'ng'iroq emas» desa — belgilanmagan
            assert (await env.ctx.reports.overview(WED, local(WED, "15:00"))).unmarked == 0
            event = (await store.pending_comments(10))[0]
            await store.save_analysis(event.id, is_call=False, status=None, reason=None, callback=None)
            await store.commit()
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert (ov.unmarked, ov.totals.total, ov.comments) == (1, 0, 0)
            assert [x.name for x in await store.unmarked_leads("2026-10-21T00:00:00", "2026-10-21T05:00:00")] == ["Aziza Karimova"]
        finally:
            await env.close()
    run(go())


def test_unreadable_card_reaction_still_counts(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            g = await setup_groups(env)
            store = env.ctx.reports.store
            g.mid += 1
            card = {"message_id": g.mid, "date": ts(WED, "09:00"), "chat": g.chat(LEADS, "L"),
                    "from": person(BOSS, "Rahbar", "boss"), "text": "Yangi odam 90 123 45 67 qo'ng'iroq qiling"}
            await env._feed({"message": card})
            assert await store.lead_by_message(LEADS, card["message_id"]) is None  # kartochka sifatida o'qilmadi
            await g.react(card["message_id"], person(MALIKA, "Malika"), "👍", ts(WED, "09:10"))
            ov = await env.ctx.reports.overview(WED, local(WED, "15:00"))
            assert [(r.name, dict(r.counts)) for r in ov.rows] == [("Malika", {"👍": 1})]
        finally:
            await env.close()
    run(go())

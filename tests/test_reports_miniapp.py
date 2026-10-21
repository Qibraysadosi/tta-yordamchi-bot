"""v1.7: Mini App bo'limlari brauzerda — tanlov ekrani, Hisobotlar bo'limi (egasi / rahbar / a'zo), start_param.

Haqiqiy aiohttp server + Chromium (Playwright); bo'lmasa — o'tkazib yuboriladi (test_fix_miniapp.chromium)."""
from test_fix_miniapp import chromium, go_hash, navigate, settled
from test_flow import OWNER, run
from test_reports import CARD_1, CARD_2, LEADERS, LEADS, MALIKA, SARDOR, WED, Group, person, ts
from test_reports_api import LEADER, MEMBER, Api, init_data

TABS = "() => Array.from(document.querySelectorAll('#tabs [data-tab]')).map((b) => b.dataset.tab)"
TEXT = "() => document.querySelector('#screen').textContent.replace(/\\u00a0/g, ' ')"  # innerText CSS uppercase'ni ham qaytaradi
AI_TABS = ["home", "history", "files", "stats", "admin"]


async def prepare(api: Api) -> None:
    """Lidlar va rahbarlar guruhi, operator, rahbar, ikkita lid: biri ro'yxatdagi, biri ro'yxatda yo'q odam belgilagan."""
    g = Group(api.env)
    await g.join(LEADS, "Lidlar guruhi")
    await api.call("PATCH", f"/api/report/groups/{LEADS}", {"roles": ["leads"]})
    await g.join(LEADERS, "Rahbarlar guruhi")
    await api.call("PATCH", f"/api/report/groups/{LEADERS}", {"roles": ["leaders"]})
    await api.call("POST", "/api/report/people", {"kind": "operator", "who": "@malika_op", "name": "Malika"})
    await api.call("POST", "/api/report/people", {"kind": "leader", "who": "@rahbar_1", "name": "Rahbar"})
    one = await g.post(CARD_1, ts(WED, "09:00"))
    two = await g.post(CARD_2, ts(WED, "09:10"))
    await g.react(one["message_id"], person(MALIKA, "Malika", "malika_op"), "👍", ts(WED, "09:30"))
    await g.react(two["message_id"], person(SARDOR, "Sardor", "sardor_op"), "👎", ts(WED, "09:40"))


def test_owner_chooser_and_report_section(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            await prepare(api)
            async with chromium() as open_page:
                page, base = await open_page(api)
                await page.goto(base)  # Telegram'dan ochilish: hash'da marshrut yo'q
                await settled(page)
                assert await page.evaluate("location.hash") == "#choose"
                assert await page.evaluate(TABS) == []  # tanlov ekranida pastki menyu yo'q
                choices = await page.locator(".choice").all_inner_texts()
                assert len(choices) == 2 and "AI yordamchi" in choices[0] and "Hisobotlar" in choices[1]
                assert "2 ta qo'ng'iroq" in choices[1] and "👍 1" in choices[1] and "2 yangi lid" in choices[1]

                await navigate(page, lambda: page.click(".choice.report"))
                assert await page.evaluate("location.hash") == "#r-today"
                assert await page.evaluate(TABS) == ["r-today", "r-tasks", "r-ops", "r-set", "choose"]
                text = await page.evaluate(TEXT)
                assert "2 ta lidga qo'ng'iroq" in text and "jonli" in text and "Malika" in text
                assert "Ro'yxatda yo'q" in text and "Sardor" in text
                assert "Hozirgi holatni rahbarlarga yuborish" in text

                # oldingi kun: yakunlangan davr, keyingi kun tugmasi bor
                await navigate(page, lambda: page.click("[data-action=r-day]"))
                assert await page.evaluate("location.hash") == "#r-today/2026-10-20"
                text = await page.evaluate(TEXT)
                assert "20-oktabr" in text and "yakunlangan" in text and "rahbarlarga yuborish" not in text
                await go_hash(page, "#r-today")

                # ro'yxatda yo'q odamni operator qilish
                await page.click("[data-action=r-extra]")
                async with page.expect_response(lambda r: r.url.endswith("/api/report/people") and r.request.method == "POST"):
                    await page.click(".sheet [data-action=r-promote][data-kind=operator]")
                await page.wait_for_function("!document.querySelector('.sheet') && !document.querySelector('.skeleton')"
                                             " && !document.querySelector('#screen').textContent.includes(\"Ro'yxatda yo'q\")")
                people = await api.call("GET", "/api/report/people")
                assert [p["username"] for p in people["operators"]] == ["malika_op", "sardor_op"]

                await navigate(page, lambda: page.click("[data-tab=r-ops]"))
                text = await page.evaluate(TEXT)
                assert "Call center · 2 ta" in text and "+ Qo'shish" in text

                await navigate(page, lambda: page.click("[data-tab=choose]"))
                assert await page.evaluate("location.hash") == "#choose"
                await navigate(page, lambda: page.click(".choice.ai"))
                assert await page.evaluate("location.hash") == "#home"
                assert await page.evaluate(TABS) == AI_TABS + ["choose"]
                assert await page.locator(".hero").count() == 1
        finally:
            await api.close()
    run(go())


def test_owner_settings_screen(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            await prepare(api)
            async with chromium() as open_page:
                page, base = await open_page(api)
                await page.goto(base + "#r-set")
                await settled(page)
                text = await page.evaluate(TEXT)
                assert "Lidlar guruhi" in text and "Rahbarlar guruhi" in text and "Statuslar" in text
                assert "19:00" in text and "Du–Sh" in text and "Rahbar" in text

                # rahbarlar guruhini lidlar guruhi ham qilish: panel yopilgach ro'yxat yangilanadi
                await page.click(f"[data-action=r-group][data-chat='{LEADERS}']")
                async with page.expect_response(lambda r: f"/api/report/groups/{LEADERS}" in r.url):
                    await page.evaluate("document.querySelector('.sheet input[data-role=leads]').click()")
                await page.wait_for_function("document.querySelector('#toast').textContent === 'Saqlandi'")
                await page.click(".sheet", position={"x": 20, "y": 20})
                await page.wait_for_function("!document.querySelector('.sheet') && !document.querySelector('.skeleton')"
                                             " && document.querySelector('#screen').textContent.includes('Lidlar, Rahbarlar')")
                group = await api.env.ctx.reports.store.group(LEADERS)
                assert group.has("leads") and group.has("leaders")

                # yuborish vaqtini o'zgartirish
                await page.click("[data-action=r-time]")
                await page.fill(".sheet input[name=time]", "18:30")
                async with page.expect_response(lambda r: r.url.endswith("/api/report/schedule")):
                    await page.click(".sheet button[type=submit]")
                await page.wait_for_function("!document.querySelector('.sheet') && !document.querySelector('.skeleton')"
                                             " && document.querySelector('#screen').textContent.includes('18:30')")
                assert (await api.env.ctx.reports.schedule()).time == "18:30"

                # kunlik hisobotni o'chirish tumbleri
                async with page.expect_response(lambda r: r.url.endswith("/api/report/schedule")):
                    await page.evaluate("document.querySelector('[data-rtoggle=r-enabled]').click()")
                await page.wait_for_function("!document.querySelector('[data-rtoggle=r-enabled]').checked")
                assert (await api.env.ctx.reports.schedule()).enabled is False
        finally:
            await api.close()
    run(go())


def test_leader_sees_only_reports(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            await prepare(api)
            async with chromium() as open_page:
                page, base = await open_page(api, init=init_data(LEADER, "rahbar_1"))
                await page.goto(base)
                await settled(page)
                assert await page.evaluate("location.hash") == "#r-today"
                assert await page.evaluate(TABS) == ["r-today", "r-tasks", "r-ops"]  # sozlamalar va bo'limlar yo'q
                text = await page.evaluate(TEXT)
                assert "2 ta lidga qo'ng'iroq" in text and "Malika" in text
                assert "rahbarlarga yuborish" not in text and "operator qilish" not in text
                assert await page.locator("[data-action=r-extra]").count() == 0

                # yopiq bo'limlar manzili — ilova qulflanmaydi, hisobotga qaytadi
                for closed in ("#home", "#r-set", "#admin", "#choose"):
                    await go_hash(page, closed)
                    assert await page.evaluate("location.hash") == "#r-today", closed
                    assert await page.locator(".lock").count() == 0

                await navigate(page, lambda: page.click("[data-tab=r-ops]"))
                text = await page.evaluate(TEXT)
                assert "Call center · 1 ta" in text and "+ Qo'shish" not in text
        finally:
            await api.close()
    run(go())


def test_start_param_opens_report_day(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            await prepare(api)
            await api.env.db.add_member(MEMBER, "Ali", "ali")
            async with chromium() as open_page:
                # hisobot ostidagi «📊 Batafsil» (t.me/<bot>?startapp=hisobot_20261020) — tanlovsiz shu kun
                page, base = await open_page(api, start_param="hisobot_20261020")
                await page.goto(base)
                await settled(page)
                assert await page.evaluate("location.hash") == "#r-today/2026-10-20"
                assert "20-oktabr" in await page.evaluate(TEXT)
                await navigate(page, lambda: page.click("[data-tab=choose]"))
                assert await page.locator(".choice").count() == 2

                # a'zo (faqat AI): tanlov yo'q, start_param ham, hisobot manzili ham bosh sahifaga olib boradi
                page, base = await open_page(api, init=init_data(MEMBER, "ali"), start_param="hisobot")
                await page.goto(base)
                await settled(page)
                assert await page.evaluate("location.hash") == "#home"
                assert await page.evaluate(TABS) == AI_TABS[:4]
                await go_hash(page, "#r-today")
                assert await page.evaluate("location.hash") == "#home"
                assert await page.locator(".lock").count() == 0
        finally:
            await api.close()
    run(go())


def test_stranger_is_locked(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            async with chromium() as open_page:
                page, base = await open_page(api, user_id=OWNER + 999)
                await page.goto(base)
                await page.wait_for_selector(".lock")
                assert "parol" in await page.evaluate(TEXT)
                assert await page.evaluate(TABS) == []
        finally:
            await api.close()
    run(go())


def test_tasks_tab(tmp_path):
    async def go():
        api = await Api().start(tmp_path)
        try:
            await prepare(api)
            env = api.env
            store = env.ctx.reports.store
            g = Group(env)
            await g.join(-1005555555555, "Ofis")
            await store.set_group_roles(-1005555555555, ["tasks"])
            made = await api.call("POST", "/api/report/tasks", {"who": "@malika_op", "text": "Oylik hisobotni topshirish",
                                                              "due": "2026-10-22T18:00"})
            async with chromium() as open_page:
                page, base = await open_page(api, start_param="vazifalar")  # «📋 Mini App'da ochish» tugmasi
                await page.goto(base)
                await settled(page)
                assert await page.evaluate("location.hash") == "#r-tasks"
                text = await page.evaluate(TEXT)
                assert "1 ta ochiq" in text and "Oylik hisobotni topshirish" in text and "Malika" in text
                assert "kutilmoqda" in text and "22.10 (pa) 18:00" in text

                # yangi vazifa — panel orqali
                await page.click("[data-action=r-task-add]")
                await page.fill(".sheet input[name=who]", "@sardor_op")
                await page.fill(".sheet textarea[name=text]", "Zalni tayyorlash")
                await page.select_option(".sheet select[name=repeat]", "weekly")
                async with page.expect_response(lambda r: r.url.endswith("/api/report/tasks") and r.request.method == "POST"):
                    await page.click(".sheet button[type=submit]")
                await page.wait_for_function("!document.querySelector('.sheet') && !document.querySelector('.skeleton')"
                                             " && document.querySelector('#screen').textContent.includes('Zalni tayyorlash')")
                added = [t for t in await env.ctx.reports.tasks.store.tasks() if t.text == "Zalni tayyorlash"][0]
                assert added.assignee_username == "sardor_op" and added.repeat == "weekly:3"  # 22.10 — payshanba

                # tafsilot → bajarildi
                await page.click(f"[data-action=r-task][data-id='{made['task']['id']}']")
                await page.wait_for_selector(".sheet .hist")
                assert "qayd etildi" in await page.evaluate("document.querySelector('.sheet').textContent")
                async with page.expect_response(lambda r: f"/api/report/tasks/{made['task']['id']}" in r.url):
                    await page.click(".sheet [data-action=r-task-set][data-status=done]")
                await page.wait_for_function("!document.querySelector('.sheet') && !document.querySelector('.skeleton')"
                                             " && !document.querySelector('#screen').textContent.includes('Oylik hisobot')")
                await page.click("[data-action=r-tfilter][data-f=done]")
                await page.wait_for_function("document.querySelector('#screen').textContent.includes('Oylik hisobotni topshirish')"
                                             " && document.querySelector('.seg .on').dataset.f === 'done'")
                assert "bajarildi" in await page.evaluate(TEXT)

                # rahbar: ko'radi, qo'sha olmaydi
                page, base = await open_page(api, init=init_data(LEADER, "rahbar_1"))
                await page.goto(base + "#r-tasks")
                await settled(page)
                assert "Zalni tayyorlash" in await page.evaluate(TEXT)
                assert await page.locator("[data-action=r-task-add]").count() == 0
                await page.click("[data-action=r-task]")
                await page.wait_for_selector(".sheet .hist")
                assert await page.locator(".sheet [data-action=r-task-set]").count() == 0
        finally:
            await api.close()
    run(go())


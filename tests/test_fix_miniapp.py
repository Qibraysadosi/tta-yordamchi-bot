"""Mini App review tuzatishlari: render poygasi, bo'sh joriy suhbat, uzun ism, joriy suhbat belgisi, tejamkor rejim.

Brauzer testlari haqiqiy aiohttp server + Chromium (Playwright) da ishlaydi; Playwright yoki Chromium
bo'lmasa — o'tkazib yuboriladi. Tashqi tarmoqqa so'rov yo'q: 127.0.0.1 dan boshqa hamma so'rov to'xtatiladi."""
import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest

import fakes
from test_flow import OWNER, run
from test_webapp import MEMBER, App, init_data


def mock_telegram(user_id: int = OWNER) -> str:
    return """
    (() => {
      const noop = () => {};
      window.__nav = 0;  // har hashchange — ilova render() dan oldin sanaladi
      window.addEventListener('hashchange', () => { window.__nav++; });
      window.Telegram = { WebApp: {
        initData: %s, colorScheme: 'light', themeParams: {},
        ready: noop, expand: noop, close: noop, onEvent: noop, offEvent: noop, setHeaderColor: noop,
        setBackgroundColor: noop, disableVerticalSwipes: noop, openLink: noop, downloadFile: noop,
        BackButton: { show: noop, hide: noop, onClick: noop },
        HapticFeedback: { impactOccurred: noop, notificationOccurred: noop },
      } };
    })();
    """ % json.dumps(init_data(user_id))


def local_chromium() -> str | None:
    """Playwright versiyasiga mos brauzer yo'q bo'lsa — o'rnatilgan boshqa Chromium build'i."""
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    for pattern in ("chromium_headless_shell-*/chrome-*/headless_shell", "chromium_headless_shell-*/chrome-*/chrome-headless-shell",
                    "chromium-*/chrome-linux*/chrome"):
        found = sorted(root.glob(pattern))
        if found:
            return str(found[-1])
    return None


@contextlib.asynccontextmanager
async def chromium():
    """`open(app, width)` → (page, base): har ilova uchun alohida kontekst; sahifa xatolari yig'iladi."""
    pw_api = pytest.importorskip("playwright.async_api")
    async with pw_api.async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as exc:  # mos build yo'q — o'rnatilganini sinaymiz
            path = local_chromium()
            if not path:
                pytest.skip(f"Chromium ishga tushmadi: {exc}")
            try:
                browser = await pw.chromium.launch(executable_path=path)
            except Exception as exc2:
                pytest.skip(f"Chromium ishga tushmadi: {exc2}")
        errors: list[str] = []

        async def open_page(app: App, width: int = 390):
            base = str(app.client.make_url("/"))

            async def only_local(route):
                if route.request.url.startswith(base):
                    await route.continue_()
                else:
                    await route.abort()

            context = await browser.new_context(viewport={"width": width, "height": 800}, bypass_csp=True)
            await context.route("**/*", only_local)
            await context.add_init_script(mock_telegram())
            page = await context.new_page()
            page.set_default_timeout(8000)
            page.on("pageerror", lambda e: errors.append(str(e)))
            return page, base

        try:
            yield open_page
            assert not errors, errors
        finally:
            await browser.close()


async def settled(page) -> None:
    await page.wait_for_function("!document.querySelector('.skeleton') && document.querySelector('#screen').children.length")
    await page.wait_for_timeout(50)


async def navigate(page, action) -> None:
    """Amal (bosish, hash) → hashchange → ekran tayyor bo'lguncha kutadi."""
    count = await page.evaluate("window.__nav")
    await action()
    await page.wait_for_function(f"window.__nav > {count}")
    await settled(page)


async def go_hash(page, value: str) -> None:
    await navigate(page, lambda: page.evaluate("(h) => { location.hash = h; }", value))


def slow(seconds: float):
    async def handler(route):
        await asyncio.sleep(seconds)
        await route.fallback()
    return handler


SCREEN = """() => ({
  hash: location.hash,
  active: (document.querySelector('.tab.active') || {dataset: {}}).dataset.tab,
  title: (document.querySelector('#screen > .title') || {firstChild: null}).firstChild?.textContent || null,
  hero: !!document.querySelector('.hero'),
  msgs: !!document.querySelector('.msgs'),
  list: !!document.querySelector('#history-list .group'),
})"""


# ---------------------------------------------------------------- 1. kechikkan javob boshqa ekranni bosmasin
def test_late_response_does_not_draw_over_new_screen(tmp_path):
    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Javob.")])
        try:
            await app.env.send_text("Birinchi savol")
            async with chromium() as open_page:
                page, base = await open_page(app)
                await page.goto(base + "#home")
                await settled(page)

                # Fayllar sekin javob beradi, foydalanuvchi esa darrov Xarajatga o'tadi
                await page.route("**/api/files*", slow(0.8))
                async with page.expect_response(lambda r: "/api/files" in r.url):
                    await page.click("[data-tab=files]")
                    await page.click("[data-tab=stats]")
                await page.wait_for_timeout(300)
                state = await page.evaluate(SCREEN)
                assert (state["hash"], state["active"], state["title"]) == ("#stats", "stats", "Xarajat")

                # suhbat yuklanayotganda orqaga: ro'yxat qoladi, suhbat ustidan chizilmaydi
                await go_hash(page, "#history")
                detail = lambda r: r.url.endswith("/api/conversations/1")  # noqa: E731
                await page.route("**/api/conversations/1", slow(0.8))
                async with page.expect_response(detail):
                    async with page.expect_request(detail):
                        await page.click("[data-action=open-conv][data-conv='1']")
                    await page.evaluate("history.back()")
                    await page.wait_for_function("location.hash === '#history'")
                await page.wait_for_timeout(300)
                state = await page.evaluate(SCREEN)
                assert state["hash"] == "#history" and state["list"] and not state["msgs"], state
        finally:
            await app.close()
    run(go())


# ---------------------------------------------------------------- 2. bo'sh joriy suhbat — xato emas
def test_empty_current_conversation_api(tmp_path):
    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Javob.")])
        try:
            me = await app.get("/api/me")
            conv_id = me["conversation"]["conv_id"]
            assert me["conversation"]["turns"] == 0
            data = await app.get(f"/api/conversations/{conv_id}")
            assert data["messages"] == [] and data["current"] == conv_id
            assert data["conversation"] == {"conv_id": conv_id, "title": "Yangi suhbat", "turns": 0, "updated_at": ""}
            await app.get(f"/api/conversations/{conv_id + 5}", status=404)  # joriy bo'lmagan — baribir topilmaydi

            # savoldan keyin «Yangi suhbat» — yangi raqam ham bo'sh, lekin ochiladi
            await app.env.send_text("Savol")
            new_id = (await app.post("/api/conversations/new"))["settings"]["conv_id"]
            assert new_id != conv_id
            data = await app.get(f"/api/conversations/{new_id}")
            assert data["messages"] == [] and data["current"] == new_id and data["conversation"]["turns"] == 0
            assert len((await app.get(f"/api/conversations/{conv_id}"))["messages"]) == 2
            await app.get("/api/conversations/99", status=404)
        finally:
            await app.close()
    run(go())


def test_empty_current_conversation_opens(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        try:
            async with chromium() as open_page:
                page, base = await open_page(app)
                await page.goto(base + "#home")
                await settled(page)
                await navigate(page, lambda: page.click("[data-action=open-conv]"))
                text = await page.inner_text("#screen")
                assert "Xato" not in text and "topilmadi" not in text
                assert "Bo'sh suhbat" in text
                small = await page.inner_text("#screen > .title small")
                assert small.startswith("0 ta savol-javob") and "joriy" in small
                assert await page.get_attribute(".sticky-bottom .btn", "data-action") == "close"
        finally:
            await app.close()
    run(go())


# ---------------------------------------------------------------- 3. uzun ism ustunga chiqmasin
def test_long_user_name_stays_in_its_column(tmp_path):
    async def go():
        app = await App().start(tmp_path)
        try:
            await app.env.db.add_member(MEMBER, "Abdurahmonbek", "abdurahmonbek_karimov_2001")
            await app.env.db.log_usage(MEMBER, "claude-haiku-5-5", {"input": 100, "output": 50}, 0.4)
            async with chromium() as open_page:
                page, base = await open_page(app, width=320)
                await page.goto(base + "#stats")
                await settled(page)
                rows = await page.evaluate("""() => [...document.querySelectorAll('.hbar .name')].map((el) => ({
                    text: el.textContent, title: el.getAttribute('title'), sw: el.scrollWidth, cw: el.clientWidth,
                    right: el.getBoundingClientRect().right,
                    track: el.parentElement.querySelector('.track').getBoundingClientRect().left }))""")
                long = [r for r in rows if "abdurahmonbek_karimov_2001" in r["text"]]
                assert long and rows
                for r in rows:  # matn 86px ustundan chiqib, chiziq ustiga tushmaydi
                    assert r["sw"] <= r["cw"] + 1 and r["right"] <= r["track"], r
                assert long[0]["title"] == "Abdurahmonbek @abdurahmonbek_karimov_2001"  # to'liq ism — title'da
        finally:
            await app.close()
    run(go())


# ---------------------------------------------------------------- 4. joriy suhbat — serverdan, keshdan emas
def test_conversation_detail_reports_current(tmp_path):
    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Birinchi javob.")])
        env = app.env
        try:
            await env.send_text("Birinchi savol")
            assert (await app.get("/api/conversations/1"))["current"] == 1
            await env.db.new_conversation(OWNER)  # chatda /new
            await env.db.add_turn(OWNER, 2, [{"type": "text", "text": "Ikkinchi savol"}], "Ikkinchi javob")
            assert (await app.get("/api/conversations/1"))["current"] == 2
            assert (await app.get("/api/conversations/2"))["current"] == 2
            await env.db.switch_conversation(OWNER, 1)  # chatda /tarix → qaytish
            assert (await app.get("/api/conversations/2"))["current"] == 1
        finally:
            await app.close()
    run(go())


def test_conversation_detail_uses_fresh_current(tmp_path):
    async def go():
        app = await App().start(tmp_path, [fakes.simple_answer("Birinchi javob.")])
        env = app.env
        try:
            await env.send_text("Birinchi savol")
            async with chromium() as open_page:
                page, base = await open_page(app)
                await page.goto(base + "#home")
                await settled(page)  # ilova conv 1 ni joriy deb eslab qoldi

                # chatda /new va yangi savol — ilova ochiq turibdi
                await env.db.new_conversation(OWNER)
                await env.db.add_turn(OWNER, 2, [{"type": "text", "text": "Ikkinchi savol"}], "Ikkinchi javob")

                await go_hash(page, "#history/1")
                small = await page.inner_text("#screen > .title small")
                assert "joriy" not in small
                button = await page.query_selector(".sticky-bottom .btn")
                assert await button.get_attribute("data-action") == "resume"
                assert await button.get_attribute("data-conv") == "1"
                await go_hash(page, "#history/2")
                assert "joriy" in await page.inner_text("#screen > .title small")
                assert await page.get_attribute(".sticky-bottom .btn", "data-action") == "close"
        finally:
            await app.close()
    run(go())


# ---------------------------------------------------------------- 5. tejamkor rejim o'chiq bo'lsa — yolg'on yozuv yo'q
def test_economy_note_and_meter_follow_settings(tmp_path):
    cases = [
        ({"budget_economy_percent": 0}, "", "Bugun hammasi: $1.00."),
        ({"monthly_budget": 0.0}, None, "Bugun hammasi: $1.00."),
        ({}, "", "Bugun hammasi: $1.00. Kredit umumiy — 90% dan keyin hamma Haiku 5.5 bilan ishlaydi."),
    ]

    async def go():
        async with chromium() as open_page:
            for i, (over, bar_class, note) in enumerate(cases):
                folder = tmp_path / f"case{i}"
                folder.mkdir()
                app = await App().start(folder, **over)
                try:
                    await app.env.db.log_usage(OWNER, "claude-opus-5-5", {"input": 100, "output": 50}, 1.0)
                    page, base = await open_page(app)
                    await page.goto(base + "#home")
                    await settled(page)
                    bar = await page.query_selector(".hero .bar i")
                    if bar_class is None:
                        assert bar is None, over
                    else:
                        assert await bar.get_attribute("class") == bar_class, over
                    await go_hash(page, "#stats")
                    notes = await page.eval_on_selector_all(".note", "els => els.map((e) => e.textContent)")
                    assert note in notes, (over, notes)
                finally:
                    await app.close()
    run(go())

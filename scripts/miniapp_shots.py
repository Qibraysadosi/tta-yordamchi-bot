"""Mini App ekranlarining skrinshotlari: haqiqiy server + soxta Telegram WebApp obyekti (Playwright).

Ishlatish: pip install playwright && playwright install chromium; python scripts/miniapp_shots.py [chiqish papkasi]
Natija: docs/screens/{light,dark}-<ekran>.png va overview-{light,dark}.png"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from aiohttp import web  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from bot import files as filebuild  # noqa: E402
from bot.filestore import FileStore  # noqa: E402
from bot.webapp import WebApp  # noqa: E402
from test_flow import OWNER, Env  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "docs" / "screens"))
TOKEN = "123456:TESTTOKEN"

LIGHT = {
    "bg_color": "#ffffff", "secondary_bg_color": "#efeff4", "section_bg_color": "#ffffff", "text_color": "#000000",
    "hint_color": "#8e8e93", "link_color": "#3390ec", "button_color": "#3390ec", "button_text_color": "#ffffff",
    "section_header_text_color": "#6d6d72", "subtitle_text_color": "#8e8e93", "destructive_text_color": "#ff3b30",
    "section_separator_color": "#c8c7cc", "header_bg_color": "#efeff4",
}
DARK = {
    "bg_color": "#1c1c1d", "secondary_bg_color": "#000000", "section_bg_color": "#1c1c1d", "text_color": "#ffffff",
    "hint_color": "#98989e", "link_color": "#3e88f7", "button_color": "#3e88f7", "button_text_color": "#ffffff",
    "section_header_text_color": "#8d8e93", "subtitle_text_color": "#98989e", "destructive_text_color": "#ef5b5b",
    "section_separator_color": "#3d3d3f", "header_bg_color": "#1c1c1d",
}


def init_data(user_id: int) -> str:
    fields = {
        "auth_date": str(int(time.time())), "query_id": "AAEqq",
        "user": json.dumps({"id": user_id, "first_name": "Shohrux", "username": "shohrux"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def mock_script(scheme: str) -> str:
    params = DARK if scheme == "dark" else LIGHT
    return f"""
    (() => {{
      const params = {json.dumps(params)};
      const apply = () => {{ const root = document.documentElement; for (const [k, v] of Object.entries(params)) root.style.setProperty('--tg-theme-' + k.replace(/_/g, '-'), v); }};
      if (document.documentElement) apply(); else document.addEventListener('DOMContentLoaded', apply);
      const noop = () => {{}};
      window.Telegram = {{ WebApp: {{
        initData: {json.dumps(init_data(OWNER))}, colorScheme: {json.dumps(scheme)}, themeParams: params,
        ready: noop, expand: noop, close: noop, onEvent: noop, offEvent: noop, setHeaderColor: noop, setBackgroundColor: noop,
        disableVerticalSwipes: noop, openLink: noop, downloadFile: noop,
        BackButton: {{ show: noop, hide: noop, onClick: noop }},
        HapticFeedback: {{ impactOccurred: noop, notificationOccurred: noop }},
      }} }};
    }})();
    """


async def seed(env: Env, store: FileStore) -> None:
    db = env.db
    questions = [
        ("Hokimiyatga xat: o'quv markazi uchun bino ajratish so'rovi", "Hurmatli Abdullayev! ..."),
        ("Instagram post: Excel kursi 15-oktabrdan boshlanadi", "📊 Excel kursiga yozilish boshlandi! ..."),
        ("Shartnoma bo'yicha tomonlar majburiyatlarini tushuntir", "Shartnomaga ko'ra ..."),
        ("Sentabr oyi hisobotini Word qilib ber", "Hisobot tayyor — faylda 4 bo'lim bor."),
        ("Bandlik bo'limi uchun oylik hisobot jadvali", "Jadval Excel ko'rinishida yuborildi."),
        ("Meta reklama matni: bepul kompyuter savodxonligi kursi", "Variant 1: ..."),
    ]
    conv = 1
    for i, (q, a) in enumerate(questions, 1):
        await db.add_turn(OWNER, conv, [{"type": "text", "text": q}], a)
        if i in (1, 3):
            await db.add_turn(OWNER, conv, [{"type": "text", "text": "Rahmat, qisqaroq variant ham ber"}], "Qisqa variant: ...")
        conv += 1
    await env.ctx.user_settings(OWNER)
    await db.update_settings(OWNER, conv_id=4, mode="rasmiy_xat", effort="high")
    # xarajat: oxirgi 30 kun
    rnd = random.Random(7)
    now = datetime.now(timezone.utc)
    for day in range(30):
        stamp = now - timedelta(days=29 - day)
        for _ in range(rnd.randint(0, 6) if day % 7 else rnd.randint(0, 2)):
            model = rnd.choice(["claude-opus-5-5", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"])
            cost = {"claude-opus-5-5": 0.32, "claude-sonnet-5-5": 0.09, "claude-haiku-5-5": 0.01, "claude-fable-5-1": 0.9}[model] * rnd.uniform(0.3, 2.2)
            await db._conn.execute(
                "INSERT INTO usage_log (user_id, model, input, output, cost, created_at) VALUES (?, ?, 3000, 800, ?, ?)",
                (OWNER, model, round(cost, 5), stamp.strftime("%Y-%m-%dT%H:%M:%S")),
            )
    await db._conn.commit()
    await db.add_member(555, "Dilnoza", "dilnoza_a")
    await db.add_member(556, "Jasur", None)
    await db.log_usage(555, "claude-sonnet-5-5", {"input": 2000, "output": 500}, 0.41)
    await db.log_usage(556, "claude-haiku-5-5", {"input": 2000, "output": 500}, 0.05)
    await db.add_template(OWNER, "Instagram post", "Shu mavzuda Instagram post yoz: sarlavha, matn, 5 ta heshteg")
    await db.add_template(OWNER, "Rasmiy xat", "TKHTA nomidan rasmiy xat yoz. Mavzu: ")
    await db.set_config("password_hash", "x")
    docx = filebuild.build_docx("# Hisobot\n\nSentabr", "Sentabr hisoboti")
    xlsx = filebuild.build_xlsx([{"name": "Hisobot", "columns": ["Kurs", "Soni"], "rows": [["Excel", 24], ["Kompyuter", 31]]}])
    pptx = filebuild.build_pptx([{"title": "Kirish", "bullets": ["bir", "ikki"]}], "Taqdimot")
    pdf = filebuild.build_pdf("Matn", "Bildirishnoma")
    png = filebuild.build_chart({"type": "bar", "title": "Sotuv", "labels": ["Yan", "Fev"], "series": [{"values": [1, 2]}]})
    for name, data, cid in [("Sentabr_hisoboti.docx", docx, 4), ("Bandlik_hisobot_jadvali.xlsx", xlsx, 5), ("Excel_kursi_taqdimot.pptx", pptx, 2),
                            ("Bildirishnoma.pdf", pdf, 1), ("Kurslar_dinamikasi.png", png, 5)]:
        await store.save(OWNER, cid, name, data, None)
    # ikkinchi kundagi fayl
    await db._conn.execute("UPDATE files SET created_at = ? WHERE id <= 2", ((now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),))
    await db._conn.commit()
    backups = env.ctx.updater.backups_dir
    backups.mkdir(parents=True, exist_ok=True)
    import shutil
    target = backups / "20261011-031500-v1.4.0"
    shutil.copytree(env.app_dir / "bot", target / "bot")
    shutil.copytree(env.app_dir / "prompts", target / "prompts")
    shutil.copy2(env.app_dir / "requirements.txt", target / "requirements.txt")
    (target / "VERSION").write_text("1.4.0\n")
    (env.app_dir / "VERSION").write_text("1.5.0\n")


async def main() -> None:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="miniapp-shots-"))
    env = await Env().start(tmp, webapp_url="https://bepulkurs.uz/yordamchi/")
    store = FileStore(env.db, tmp / "data" / "files", 50 * 1024 * 1024)
    env.ctx.files = store
    await seed(env, store)
    webapp = WebApp(env.ctx, env.bot)
    runner = web.AppRunner(webapp.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}/"
    OUT.mkdir(parents=True, exist_ok=True)

    screens = [("home", "#home"), ("history", "#history"), ("conversation", "#history/1"), ("files", "#files"), ("stats", "#stats"), ("admin", "#admin")]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for scheme in ("light", "dark"):
            context = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, bypass_csp=True)
            await context.route("**/telegram.org/**", lambda route: asyncio.ensure_future(route.abort()))
            await context.add_init_script(mock_script(scheme))
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            for name, route_hash in screens:
                print(scheme, name, flush=True)
                await page.goto(base + route_hash, timeout=15000)
                await page.wait_for_timeout(600)
                await page.wait_for_function("!document.querySelector('.skeleton')", timeout=5000)
                await page.wait_for_timeout(250)
                await page.screenshot(path=str(OUT / f"{scheme}-{name}.png"))
            # sheet: model tanlash
            await page.goto(base + "#home")
            await page.wait_for_function("!document.querySelector('.skeleton')", timeout=5000)
            await page.click("[data-action='pick-model']")
            await page.wait_for_timeout(350)
            await page.screenshot(path=str(OUT / f"{scheme}-sheet-model.png"))
            print(scheme, "errors:", errors)
            await context.close()
        await browser.close()
    await runner.cleanup()
    await env.close()
    compose_overview()
    print("saved to", OUT)


def compose_overview() -> None:
    """Barcha ekranlarni bitta rasmga yig'adi (ko'rib chiqish uchun)."""
    from PIL import Image, ImageDraw, ImageFont

    names = [("home", "Bosh sahifa"), ("sheet-model", "Model tanlash"), ("history", "Tarix"), ("conversation", "Suhbat"),
             ("files", "Fayllar"), ("stats", "Xarajat"), ("admin", "Admin panel")]
    cell_w, cell_h, pad, label_h = 390, 844, 18, 34
    font_path = ROOT / "bot" / "assets" / "fonts" / "DejaVuSans-Bold.ttf"
    font = ImageFont.truetype(str(font_path), 15) if font_path.exists() else ImageFont.load_default()
    for scheme, bg, fg in (("light", (238, 240, 245), (30, 30, 40)), ("dark", (12, 12, 16), (235, 235, 240))):
        canvas = Image.new("RGB", (pad + len(names) * (cell_w + pad), pad + label_h + cell_h + pad), bg)
        draw = ImageDraw.Draw(canvas)
        for i, (name, label) in enumerate(names):
            path = OUT / f"{scheme}-{name}.png"
            if not path.exists():
                continue
            img = Image.open(path).convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
            x = pad + i * (cell_w + pad)
            draw.text((x + 4, pad + 6), label, fill=fg, font=font)
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius=28, fill=255)
            canvas.paste(img, (x, pad + label_h), mask)
        canvas.save(OUT / f"overview-{scheme}.png", optimize=True)


if __name__ == "__main__":
    asyncio.run(main())

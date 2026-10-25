"""v1.10: AI suhbat yadrosi tuzatishlari — javob yo'qolmaydi, «/» bilan boshlangan matn, fayl xatolari, rasm hajmi,
tejamkor rejim, uzun xabarlar, qidiruv chegarasi, chiqarilgan a'zo, /new, ichki tarmoq havolalari."""
import asyncio
import base64
import io
import socket
import zipfile

import httpx2
import openpyxl
import pytest
from PIL import Image

import fakes
from bot import links, media, tgformat
from test_flow import OWNER, Env, png, run

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def wait_active(env, uid=OWNER, steps=300):
    for _ in range(steps):
        await asyncio.sleep(0.01)
        if env.ctx.active.get(uid) is not None:
            return True
    return False


def broken_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    wb.active["A1"] = "salom"
    good = io.BytesIO()
    wb.save(good)
    source = zipfile.ZipFile(io.BytesIO(good.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data[: len(data) // 2]  # kesilgan XML — openpyxl ParseError beradi
            archive.writestr(item, data)
    return out.getvalue()


# ----------------------------------------------------------------------------- javob yo'qolmaydi
def test_broken_telegram_reply_does_not_lose_answer(tmp_path):
    async def go():
        from aiogram.exceptions import ClientDecodeError

        from bot.live import LiveMessage
        LiveMessage.INTERVAL = 0.05
        env = await Env().start(tmp_path, [fakes.simple_answer("Tayyor javob")], delay=0.4)
        try:
            original = env.session.make_request
            state = {"failed": False}

            async def flaky(bot, method, timeout=None):
                if type(method).__name__ in ("EditMessageText", "SendChatAction") and not state["failed"]:
                    state["failed"] = True
                    env.session.calls.append(method)
                    raise ClientDecodeError("Failed to decode object", ValueError("Expecting value"),
                                            "<html><title>502 Bad Gateway</title></html>")
                return await original(bot, method, timeout)
            env.session.make_request = flaky
            await env.send_text("Salom")
            assert state["failed"]
            finals = [e for e in env.session.of("EditMessageText") if e.parse_mode == "HTML"]
            assert finals and "Tayyor javob" in finals[-1].text  # javob ko'rsatildi
        finally:
            LiveMessage.INTERVAL = 1.6
            await env.close()
    run(go())


def test_answer_shown_even_if_saving_fails(tmp_path, monkeypatch):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Muhim javob")])
        try:
            async def broken(*args, **kwargs):
                raise RuntimeError("disk to'la")
            monkeypatch.setattr(env.db, "add_turn", broken)
            await env.send_text("Savol")
            final = env.last_edit()
            assert "Muhim javob" in final.text and "saqlanmadi" in final.text and "Kutilmagan xato" not in final.text
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- «/» bilan boshlangan matn
def test_slash_text_reaches_claude(tmp_path):
    async def go():
        from bot.handlers import Debouncer
        env = await Env().start(tmp_path, [fakes.simple_answer("ok"), fakes.simple_answer("ok2")])
        try:
            await env.send_text("/etc/nginx/nginx.conf faylida 502 xato chiqyapti, nima qilay?")
            assert env.api.requests and "nginx.conf" in env.api.requests[0]["messages"][-1]["content"][-1]["text"]
            env.ctx.texts = Debouncer(0.2)
            part1 = "function a() {\n  return 1;\n}\n" * 3
            part2 = "// ikkinchi qism: bu yerda xato bor\nfunction b() { return a() + ; }"
            await asyncio.gather(env.send_text(part1), env.send_text(part2))
            sent = env.api.requests[1]["messages"][-1]["content"][-1]["text"]
            assert "ikkinchi qism" in sent and "function a()" in sent  # bo'lingan matn birga keldi
            await env.send_text("/yoqbuyruq")
            assert "Bunday buyruq yo'q" in env.session.of("SendMessage")[-1].text
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- fayl xatolari
def test_broken_file_gets_reply_and_album_keeps_good_files(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("Rasm haqida")])
        try:
            await env.send_document("x1", broken_xlsx(), "hisobot.xlsx", XLSX_MIME, caption="Tahlil qil")
            assert "«hisobot.xlsx» ni o'qib bo'lmadi" in env.session.of("SendMessage")[-1].text
            assert env.api.requests == [] and env.ctx.busy == {}

            env.session.files["x2"] = broken_xlsx()
            document = {"document": {"file_id": "x2", "file_unique_id": "dx2", "file_name": "jadval.xlsx",
                                     "mime_type": XLSX_MIME, "file_size": 100}, "media_group_id": "g9"}
            await asyncio.gather(
                env.send_photo("p1", png(), caption="Ikkalasini ko'r", media_group_id="g9"),
                env._feed({"message": env._msg(OWNER, **document)}),
            )
            assert any("«jadval.xlsx» ni o'qib bo'lmadi" in m.text for m in env.session.of("SendMessage"))
            content = env.api.requests[0]["messages"][-1]["content"]
            assert [b["type"] for b in content] == ["image", "text"]  # yaxshi rasm yo'qolmadi
        finally:
            await env.close()
    run(go())


def test_download_timeout_gets_reply(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("javob")])
        try:
            async def stalled(url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
                raise asyncio.TimeoutError()
                yield b""  # pragma: no cover
            env.session.stream_content = stalled
            await env.send_document("big", b"%PDF-1.7 ...", "katta.pdf", "application/pdf", caption="Xulosa qil")
            assert "«katta.pdf» ni Telegram'dan yuklab bo'lmadi" in env.session.of("SendMessage")[-1].text
            assert env.api.requests == []
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- rasm hajmi
def _photo_like(size, mode="RGB"):
    noise = [Image.effect_noise(size, 60) for _ in range(3)]
    image = Image.merge("RGB", noise)
    if mode == "RGBA":
        image = image.convert("RGBA")
    return image


def _encoded(image, fmt="PNG"):
    buf = io.BytesIO()
    image.save(buf, fmt)
    return buf.getvalue()


def test_opaque_alpha_image_becomes_jpeg():
    opaque = _photo_like((1600, 1600), "RGBA")  # alfa kanal 255 — eksport qilingan PNG'larda odatiy
    block = media.image_block(_encoded(opaque), 1568)
    assert block["source"]["media_type"] == "image/jpeg"

    logo = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    logo.paste((200, 30, 30, 255), (50, 50, 250, 250))
    small = media.image_block(_encoded(logo), 1568)
    assert small["source"]["media_type"] == "image/png"  # haqiqiy shaffof kichik rasm — PNG qoladi

    photo = _photo_like((1600, 1600), "RGBA")
    photo.putalpha(Image.linear_gradient("L").resize((1600, 1600)).point(lambda v: max(v, 1)))
    heavy = media.image_block(_encoded(photo), 1568)
    data = base64.b64decode(heavy["source"]["data"])
    assert heavy["source"]["media_type"] == "image/jpeg" and len(data) < media.PNG_LIMIT  # og'ir PNG o'rniga JPEG


def test_huge_non_jpeg_image_is_refused(monkeypatch):
    monkeypatch.setattr(media, "MAX_PIXELS", 1_000_000)
    with pytest.raises(media.MediaError, match="juda katta"):
        media.image_block(_encoded(Image.new("RGB", (1200, 1000))), 1568)
    big_jpeg = _encoded(Image.new("RGB", (3000, 2000), (10, 120, 200)), "JPEG")  # JPEG — kichraytirib o'qiladi
    assert media.image_block(big_jpeg, 1568)["source"]["media_type"] == "image/jpeg"


# ----------------------------------------------------------------------------- tejamkor rejim
def test_economy_mode_applies_after_refusal_fallback(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("ok")])
        try:
            await env.ctx.user_settings(OWNER)
            await env.db.log_usage(OWNER, "claude-opus-5-5", {}, 95.0)  # $100 dan 95%
            await env.db.update_settings(OWNER, model_override="claude-opus-4-8")
            await env.send_text("Savol")
            assert env.api.requests[0]["model"] == env.ctx.settings.economy_model
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- uzun xabarlar
def test_long_link_errors_do_not_drop_question(tmp_path, monkeypatch):
    async def go():
        async def failing_fetch(url, max_bytes, client=None):
            raise links.LinkError("sahifadan matn olib bo'lmadi")
        monkeypatch.setattr(links, "fetch", failing_fetch)
        env = await Env().start(tmp_path, [fakes.simple_answer("javob")])
        try:
            urls = [f"https://docs.google.com/document/d/{i}" + "x" * 1260 for i in range(3)]
            await env.send_text("Shu hujjatlarni solishtir:\n" + "\n".join(urls))
            notice = next(m.text for m in env.session.of("SendMessage") if "havolasiz javob" in m.text)
            assert len(notice) < 600 and "…" in notice  # havolalar qisqartirildi
            assert env.api.requests  # savol baribir Claude'ga bordi
        finally:
            await env.close()
    run(go())


def test_plain_split_and_preview_count_emoji():
    text = "📌 Band matni qisqa\n" * 600
    parts = tgformat.split_plain(text)
    assert all(len(p.encode("utf-16-le")) // 2 <= 4000 for p in parts) and "".join(parts).count("📌") == 600
    shown = tgformat.preview("😀" * 3000)
    assert len(shown.encode("utf-16-le")) // 2 <= tgformat.PREVIEW_LIMIT + 1 and shown.startswith("…")


# ----------------------------------------------------------------------------- qidiruv chegarasi
def test_pause_turn_is_continued_at_most_four_times(tmp_path):
    async def go():
        def paused(i):
            return (200, fakes.sse([fakes.msg_start(input_tokens=2000), *fakes.search(0, f"q{i}"),
                                    fakes.delta("pause_turn", 100, searches=1), fakes.STOP]))
        env = await Env().start(tmp_path, [paused(i) for i in range(12)])
        try:
            await env.send_text("Chuqur tadqiqot qil")
            from bot.claude import MAX_CONTINUATIONS
            assert len(env.api.requests) == MAX_CONTINUATIONS + 1
            assert "Qidiruv juda uzoq davom etib to'xtatildi" in env.last_edit().text
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- chiqarilgan a'zo
def test_removed_member_queued_message_is_dropped(tmp_path):
    async def go():
        member = 777
        env = await Env().start(tmp_path, [fakes.simple_answer("A1"), fakes.simple_answer("A2")], delay=0.4)
        try:
            await env.db.add_member(member, "Ali", None)
            first = asyncio.create_task(env.send_text("Q1", user_id=member))
            assert await wait_active(env, member)
            second = asyncio.create_task(env.send_text("Q2", user_id=member))
            await asyncio.sleep(0.1)  # Q2 navbatda (qulfni kutmoqda)
            await env.press(f"rm:{member}")
            await asyncio.gather(first, second)
            assert len(env.api.requests) == 1  # navbatdagi savol Claude'ga bormadi
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- /new birinchi javob paytida
def test_new_during_first_answer_starts_clean_conversation(tmp_path):
    async def go():
        env = await Env().start(tmp_path, [fakes.simple_answer("A1"), fakes.simple_answer("A2")], delay=0.5)
        try:
            task = asyncio.create_task(env.send_text("Q1"))
            assert await wait_active(env)
            await env.send_text("/new")
            await task
            await env.send_text("Q2")
            messages = env.api.requests[1]["messages"]
            assert [m["content"][0]["text"] for m in messages] == ["Q2"]
            assert [m.role for m in await env.db.history(OWNER, 1, 0)] == ["user", "assistant"]  # Q1/A1 eskisida
        finally:
            await env.close()
    run(go())


# ----------------------------------------------------------------------------- ichki tarmoq havolalari
@pytest.fixture
def dns(monkeypatch):
    table = {"public.example": "93.184.216.34", "intranet.example": "10.0.0.7", "localhost": "127.0.0.1",
             "127.0.0.1": "127.0.0.1", "169.254.169.254": "169.254.169.254"}

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        if host not in table:
            raise socket.gaierror("topilmadi")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


def test_links_do_not_reach_internal_addresses(dns):
    async def go():
        seen = []

        def handler(request):
            seen.append(str(request.url))
            if request.url.path == "/jump":
                return httpx2.Response(302, headers={"location": "http://intranet.example/secret"})
            return httpx2.Response(200, headers={"content-type": "text/html"},
                                   text="<title>Sahifa</title><p>" + "Matn " * 100 + "</p>")
        transport = httpx2.MockTransport(handler)
        for url in ("http://127.0.0.1:8080/admin", "http://localhost/", "http://intranet.example/",
                    "http://169.254.169.254/latest/meta-data"):
            with pytest.raises(links.LinkError, match="ichki tarmoq"):
                await links.fetch(url, 1_000_000, transport=transport)
        with pytest.raises(links.LinkError, match="ichki tarmoq"):
            await links.fetch("http://public.example/jump", 1_000_000, transport=transport)
        assert seen == ["http://public.example/jump"]  # ichki manzilga so'rov ketmadi
        page = await links.fetch("http://public.example/page", 1_000_000, transport=transport)
        assert page.title == "Sahifa"
        with pytest.raises(links.LinkError, match="topilmadi"):
            await links.fetch("http://yoq.example/", 1_000_000, transport=transport)
    run(go())

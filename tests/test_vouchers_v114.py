"""v1.14: «Vaucherlar» guruhi — xabar va rasmlarni AI o'qiydi, kunlik hisobotda soni, voucher_list vositasi,
Telegram Desktop eksportidan eski xabarlar; hisobotda qo'ng'iroq qilmagan (0) xodimlar ko'rinmaydi."""
import io
import json
import zipfile
from datetime import date
from pathlib import Path

from PIL import Image

import fakes
from bot.reports.archive import WEEK
from bot.reports.handlers import is_voucher_export
from bot.reports.vouchers import (
    export_chat_ids,
    group_albums,
    passport_code,
    person_key,
    phone_digits,
    pinfl_digits,
    read_export,
    same_person,
)
from test_archive import MON, NEXT_MON, seed_week
from test_assistant import LEADER
from test_flow import OWNER, run
from test_reports import LEADERS, MALIKA, SARDOR, TZ, WED, Group, local, person, setup_groups, start, ts

VOUCHERS = -1001444444444
DILNOZA = 606
THU = date(2026, 10, 22)


def jpeg(color="white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 40), color).save(buf, format="JPEG")
    return buf.getvalue()


def answer(kind, *people):
    return fakes.json_answer(json.dumps({"kind": kind, "people": list(people)}, ensure_ascii=False),
                             input_tokens=1500, output_tokens=120)


class Vouchers:
    """Vaucher guruhiga xabarlar (soxta Telegram yangilanishlari)."""

    def __init__(self, env):
        self.env = env
        self.mid = 7000

    def chat(self):
        return {"id": VOUCHERS, "type": "supergroup", "title": "Vaucher guruhi"}

    async def join(self):
        g = Group(self.env)
        await g.join(VOUCHERS, "Vaucher guruhi")
        await self.env.press(f"rg:{VOUCHERS}:vouchers")
        await self.env.press(f"rg:{VOUCHERS}:done")
        return self.env.session.of("EditMessageText")[-1].text

    async def say(self, user, when, text=None, *, photo=None, album=None, reply=None, caption=None):
        self.mid += 1
        message = {"message_id": self.mid, "date": when, "chat": self.chat(), "from": user}
        if text is not None:
            message["text"] = text
        if photo is not None:
            self.env.session.files[photo] = jpeg()
            message["photo"] = [{"file_id": photo + "s", "file_unique_id": "s" + photo, "width": 90, "height": 60},
                                {"file_id": photo, "file_unique_id": "u" + photo, "width": 800, "height": 600}]
            if caption:
                message["caption"] = caption
        if album:
            message["media_group_id"] = album
        if reply is not None:
            message["reply_to_message"] = reply
        await self.env._feed({"message": message})
        return message


def export_zip(messages, *, chat_id=1444444444, title="Vaucher guruhi", photos=None, folder="ChatExport_2026-11-11"):
    doc = {"name": title, "type": "private_supergroup", "id": chat_id, "messages": messages}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"{folder}/result.json", json.dumps(doc, ensure_ascii=False))
        for name, data in (photos or {}).items():
            archive.writestr(f"{folder}/{name}", data)
    return buf.getvalue()


def message(mid, when, text="", *, sender=(MALIKA, "Malika"), photo=None, reply=None):
    item = {"id": mid, "type": "message", "date": "2026-10-19T09:00:00", "date_unixtime": str(when),
            "from": sender[1], "from_id": f"user{sender[0]}", "text": text}
    if photo:
        item["photo"] = photo
    if reply:
        item["reply_to_message_id"] = reply
    return item


# --------------------------------------------------------------------------- odamni tanish
def test_person_matching():
    assert person_key("Karimova Aziza Bahodirovna") == person_key("Aziza  KARIMOVA bahodirovna")
    assert person_key("Обидов Бобур") == person_key("Bobur Obidov")
    assert person_key("Xasanov Ali Botir o'g'li") == person_key("Khasanov Ali Botir ogli")
    assert same_person(person_key("Aziza Karimova"), person_key("Karimova Aziza Bahodirovna"))
    assert not same_person(person_key("Aziza Karimova"), person_key("Aziza Saidova"))
    assert not same_person(person_key("Aziza"), person_key("Aziza"))  # bitta so'z — telefon ham kerak
    assert same_person(person_key("Aziza"), person_key("Aziza"), "998901234567", "998901234567")
    assert phone_digits("90 123-45-67") == "998901234567" and phone_digits("+998 (93) 765 43 21") == "998937654321"
    assert phone_digits("12345") == "" and pinfl_digits("1234 5678 9012 34") == "12345678901234"
    assert pinfl_digits("123") == "" and passport_code("ab 1234567") == "AB1234567" and passport_code("A1") == ""


def test_export_parsing():
    data = export_zip([
        message(50, ts(MON, "09:00"), "Ali Valiyev — Elektrik", photo="photos/a.jpg"),
        message(51, ts(MON, "09:00") + 1, photo="photos/b.jpg"),
        {"id": 52, "type": "service", "action": "invite_members", "date_unixtime": str(ts(MON, "09:05"))},
        message(53, ts(MON, "10:00"), ["Ali Valiyev ", {"type": "bold", "text": "tasdiqlandi"}], reply=50),
        message(55, ts(MON, "11:00"), photo="(File not included. Change data exporting settings to download.)"),
    ], photos={"photos/a.jpg": jpeg(), "photos/b.jpg": jpeg()})
    export, archive = read_export(data, "ChatExport.zip", TZ)
    assert [m.id for m in export.messages] == [50, 51, 53, 55] and archive is not None
    assert export.messages[2].text == "Ali Valiyev tasdiqlandi" and export.messages[2].reply_to == 50
    assert export.messages[3].photo is None and export.messages[3].photo_missing
    assert export.base == "ChatExport_2026-11-11/" and VOUCHERS in export_chat_ids(export)
    assert group_albums(export.messages) == {50: "imp:50", 51: "imp:50"}
    plain, none = read_export(json.dumps({"name": "x", "id": 5, "messages": []}).encode(), "result.json", TZ)
    assert plain.messages == [] and none is None


# --------------------------------------------------------------------------- jonli guruh
def test_voucher_group_flow_and_daily_report(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await setup_groups(env)
            reports = env.ctx.reports
            await reports.store.add_person("operator", username="malika_op", user_id=None, name="Malika")
            v = Vouchers(env)
            summary = await v.join()
            assert "Vaucher xabarlari" in summary and "Export chat history" in summary
            group = await reports.store.group(VOUCHERS)
            assert group.roles == ("vouchers",)

            malika, dilnoza, sardor = person(MALIKA, "Malika", "malika_op"), person(DILNOZA, "Dilnoza"), person(SARDOR, "Sardor")
            first = await v.say(malika, ts(WED, "10:00"), photo="p1",
                                caption="Aziza Karimova 90 123 45 67 Kompyuter savodxonligi, Chilonzor")
            await v.say(dilnoza, ts(WED, "11:00"), photo="p2", album="alb1", caption="2 kishi yuborildi")
            await v.say(dilnoza, ts(WED, "11:00"), photo="p3", album="alb1")
            await v.say(sardor, ts(WED, "12:00"), "Tasdiqlandi ✅", reply=first)
            await v.say(sardor, ts(WED, "12:05"), "Rahmat")
            await v.say(sardor, ts(WED, "12:06"), "+")  # ma'nosiz — AI'ga ketmaydi
            store = reports.vouchers.store
            assert await store.pending_count() == 5  # albomning 2 xabari — bitta post bo'lib tahlil qilinadi

            env.api.responses += [
                answer("voucher", {"name": "Karimova Aziza", "phone": "90 123 45 67", "course": "Kompyuter savodxonligi",
                                   "district": "Chilonzor", "pinfl": "12345678901234", "passport": "AB 1234567",
                                   "status": "sent"}),
                answer("voucher", {"name": "Обидов Бобур", "course": "Massaj", "district": "Yunusobod",
                                   "mahalla": "Navro'z MFY", "status": "sent"},
                       {"name": "Nodira Saidova", "course": "Tikuvchilik", "status": "sent"}),
                answer("update", {"name": "", "status": "approved"}),
                answer("other"),
            ]
            before = len(env.api.requests)
            assert await reports.check_daily(env.bot, local(WED, "19:02"))  # hisobotdan oldin vaucherlar tahlil qilinadi
            calls = env.api.requests[before:]
            assert len(calls) == 4 and "Vaucher" in calls[0]["system"]
            blocks = calls[0]["messages"][0]["content"]
            assert blocks[0]["type"] == "text" and blocks[1]["type"] == "image"
            assert "Aziza Karimova" in json.loads(blocks[0]["text"])["text"]
            assert [b["type"] for b in calls[1]["messages"][0]["content"]] == ["text", "image", "image"]  # albom birga
            reply = json.loads(calls[2]["messages"][0]["content"][0]["text"])["reply_to"]
            assert reply["people"][0]["name"] == "Karimova Aziza"

            rows = await store._all("SELECT * FROM rp_vouchers ORDER BY id")
            assert [(r["name"], r["status"]) for r in rows] == [
                ("Karimova Aziza", "approved"), ("Обидов Бобур", "sent"), ("Nodira Saidova", "sent")]
            assert (rows[0]["phone"], rows[0]["pinfl"], rows[0]["passport"]) == ("998901234567", "12345678901234", "AB1234567")
            assert rows[1]["sender_name"] == "Dilnoza" and rows[1]["mahalla"] == "Navro'z MFY"
            states = {r["message_id"]: r["state"] for r in await store._all("SELECT * FROM rp_voucher_posts")}
            assert sorted(states.values()) == ["done"] * 5 + ["skipped"]
            spent, count = await env.db.spend(0)
            assert spent > 0 and count == 4  # xarajat «📊 Guruh tahlili» qatorida

            report = [m.text for m in env.session.of("SendMessage") if m.chat_id == LEADERS][-1]
            assert "📄 Vaucher: <b>3</b> ta yuborildi — Dilnoza 2, Malika 1 · ✅ tasdiqlandi 1" in report

            # keyingi kun: o'sha odam qayta yozildi (otasining ismi bilan) va kirill ismli odam rad etildi
            await v.say(malika, ts(THU, "10:00"), "Aziza Karimova Bahodirovna — qayta yuborildi")
            await v.say(sardor, ts(THU, "11:00"), "Bobur Obidov rad etildi, hujjat yetishmaydi")
            env.api.responses += [
                answer("voucher", {"name": "Aziza Karimova Bahodirovna", "status": "sent"}),
                answer("update", {"name": "Bobur Obidov", "status": "rejected", "note": "hujjat yetishmaydi"}),
            ]
            assert await reports.vouchers.drain(env.bot) == 2
            rows = await store._all("SELECT * FROM rp_vouchers ORDER BY id")
            assert len(rows) == 3 and rows[1]["status"] == "rejected" and rows[1]["note"] == "hujjat yetishmaydi"
            assert rows[0]["status"] == "approved"  # qayta yozilgani holatni orqaga qaytarmaydi
            events = await store._all("SELECT status FROM rp_voucher_events ORDER BY id")
            assert [e["status"] for e in events] == ["sent", "sent", "sent", "approved", "rejected"]
            lines = await reports.vouchers.report_lines(local(WED, "19:00"), local(THU, "19:00"))
            assert lines == ["📄 Vaucher: <b>0</b> ta yuborildi · ❌ rad etildi 1"]
        finally:
            await env.close()
    run(go())


def test_voucher_list_tool(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            reports = env.ctx.reports
            v = Vouchers(env)
            await v.join()
            malika = person(MALIKA, "Malika", "malika_op")
            first = await v.say(malika, ts(WED, "10:00"), "Aziza Karimova, 90 123 45 67, Kompyuter")
            await v.say(person(DILNOZA, "Dilnoza"), ts(WED, "11:00"), "Обидов Бобур — Massaj")
            await v.say(person(SARDOR, "Sardor"), ts(WED, "12:00"), "tasdiqlandi", reply=first)
            env.api.responses += [
                answer("voucher", {"name": "Aziza Karimova", "phone": "901234567", "course": "Kompyuter",
                                   "pinfl": "12345678901234", "passport": "AB1234567"}),
                answer("voucher", {"name": "Обидов Бобур", "course": "Massaj"}),
                answer("update", {"status": "approved"}),
            ]
            await reports.vouchers.drain(env.bot)
            reports.now = lambda: local(WED, "20:00")
            full = reports.ai_tools(masked=False)
            data = json.loads(await full.run("voucher_list", {"period": "today"}))
            assert data["total"] == 2 and data["totals"] == {"yuborildi": 2, "tasdiqlandi": 1}
            aziza = next(p for p in data["people"] if p["name"] == "Aziza Karimova")
            assert aziza["phone"] == "+998 90 123-45-67" and aziza["pinfl"] == "12345678901234"
            assert aziza["status"] == "✅ tasdiqlandi" and aziza["sent_by"] == "Malika" and aziza["link"].startswith("https://t.me/c/")
            assert {s["name"]: s["sent"] for s in data["by_sender"]} == {"Malika": 1, "Dilnoza": 1}
            pick = lambda **kw: full.run("voucher_list", {"period": "today", **kw})  # noqa: E731
            assert [p["name"] for p in json.loads(await pick(status="approved"))["people"]] == ["Aziza Karimova"]
            assert [p["name"] for p in json.loads(await pick(sender="@malika_op"))["people"]] == ["Aziza Karimova"]
            assert [p["name"] for p in json.loads(await pick(search="bobur obidov"))["people"]] == ["Обидов Бобур"]
            assert [p["name"] for p in json.loads(await pick(search="12345678901234"))["people"]] == ["Aziza Karimova"]
            assert json.loads(await pick(course="massaj"))["total"] == 1

            hidden = reports.ai_tools(masked=True)
            masked = json.loads(await hidden.run("voucher_list", {"period": "today"}))
            text = json.dumps(masked, ensure_ascii=False)
            assert "12345678901234" not in text and "AB1234567" not in text and "123-45-67" not in text
            assert next(p for p in masked["people"] if p["name"] == "Aziza Karimova")["phone"] == "+998 90 ***-**-67"
            assert json.loads(await hidden.run("voucher_list", {"period": "today", "search": "12345678"}))["total"] == 0
            assert "voucher_list" in [s["name"] for s in hidden.specs()]
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- Telegram Desktop eksporti
def test_import_from_desktop_export(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            reports = env.ctx.reports
            v = Vouchers(env)
            await v.join()
            vouchers = reports.vouchers
            data = export_zip([
                message(50, ts(MON, "09:00"), "Ali Valiyev — vaucherga yuborildi, Elektrik", photo="photos/a.jpg"),
                message(51, ts(MON, "09:00") + 1, photo="photos/b.jpg"),
                message(53, ts(MON, "10:00"), "tasdiqlandi", sender=(SARDOR, "Sardor"), reply=50),
                message(54, ts(MON, "10:05"), "ok", sender=(SARDOR, "Sardor")),
                message(55, ts(MON, "11:00"), photo="(File not included. Change data exporting settings to download.)"),
            ], photos={"photos/a.jpg": jpeg(), "photos/b.jpg": jpeg("gray")})
            env.api.responses += [
                answer("voucher", {"name": "Valiyev Ali", "course": "Elektrik", "status": "sent"}),
                answer("update", {"status": "approved"}),
                answer("other"),
            ]
            before = len(env.api.requests)
            await env.send_document("exp1", data, "ChatExport_2026-11-11.zip", "application/zip")
            if vouchers._task is not None:
                await vouchers._task
            notes = [m.text for m in env.session.of("EditMessageText") if m.chat_id == OWNER]
            assert any("eksportda 5 ta xabar — 5 tasi yangi" in t and "Rasmlar: 2 ta" in t and "1 tasi eksportga kirmagan" in t
                       and "AI 3 ta postni o'qiydi" in t for t in notes), notes
            calls = env.api.requests[before:]
            assert len(calls) == 3 and [b["type"] for b in calls[0]["messages"][0]["content"]] == ["text", "image", "image"]
            rows = await vouchers.store._all("SELECT * FROM rp_vouchers")
            assert [(r["name"], r["status"], r["sender_name"], r["message_id"]) for r in rows] == [
                ("Valiyev Ali", "approved", "Malika", 50)]
            done = [m.text for m in env.session.of("SendMessage") if m.chat_id == OWNER][-1]
            assert "eski xabarlari tahlil qilindi: <b>1</b> ta yangi odam, 1 ta yozuv" in done
            assert not any((vouchers.photo_dir / str(abs(VOUCHERS))).glob("*")), "rasmlar tahlildan keyin o'chiriladi"
            logged = await reports.store.logged_messages("2026-01-01T00:00:00", "2027-01-01T00:00:00", [VOUCHERS])
            assert [m["message_id"] for m in logged] == [50, 51, 53, 54, 55]  # «guruhda nima deyishdi?» uchun ham
            lines = await vouchers.report_lines(local(MON, "00:00"), local(MON, "23:59"))
            assert lines == ["📄 Vaucher: <b>1</b> ta yuborildi — Malika 1 · ✅ tasdiqlandi 1"]  # ism — eksportdan

            # qayta yuborilsa — takrorlanmaydi
            await env.send_document("exp2", data, "ChatExport_2026-11-11.zip", "application/zip")
            last = [m.text for m in env.session.of("EditMessageText") if m.chat_id == OWNER][-1]
            assert "0 tasi yangi, 5 tasi avval bor edi" in last and "yangi xabar yo'q" in last
            assert len(env.api.requests) == before + 3
        finally:
            await env.close()
    run(go())


def test_import_errors_and_routing(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            reports = env.ctx.reports
            await env.send_document("e0", export_zip([]), "ChatExport_x.zip", "application/zip")
            assert "Avval botni vaucher guruhiga qo'shing" in env.session.of("EditMessageText")[-1].text
            v = Vouchers(env)
            await v.join()
            no_json = io.BytesIO()
            with zipfile.ZipFile(no_json, "w") as archive:
                archive.writestr("ChatExport/messages.html", "<html></html>")
            await env.send_document("e1", no_json.getvalue(), "ChatExport_y.zip", "application/zip")
            assert "result.json yo'q" in env.session.of("EditMessageText")[-1].text
            await env.send_document("e2", b'{"foo": 1}', "result.json", "application/json")
            assert "chat eksporti emas" in env.session.of("EditMessageText")[-1].text
            g = Group(env)
            await g.join(-1005555555555, "Ikkinchi vaucher")
            await env.press("rg:-1005555555555:vouchers")
            await env.send_document("e3", export_zip([], chat_id=999, title="Boshqa guruh"), "ChatExport_z.zip",
                                    "application/zip")
            assert "mos kelmadi" in env.session.of("EditMessageText")[-1].text
            await env.send_document("e5", export_zip([message(70, ts(MON, "09:00"))], chat_id=999, title="Ikkinchi vaucher"),
                                    "ChatExport_w.zip", "application/zip")  # id boshqa, nomi mos — qabul qilinadi
            assert "eksportda 1 ta xabar" in env.session.of("EditMessageText")[-1].text
            assert (await reports.vouchers.store.post(-1005555555555, 70)).state == "skipped"

            # /vaucher: holat, yo'riqnoma; keyingi ZIP (boshqa nomli ham) — eksport sifatida
            env.session.clear()
            await env.send_text("/vaucher")
            text = env.session.of("SendMessage")[-1].text
            assert "Vaucherlar" in text and "Bugun: <b>0</b> ta yuborildi" in text and "Export chat history" in text
            assert OWNER in reports.vouchers.waiting
            env.api.responses.append(answer("other"))
            await env.send_document("e4", export_zip([message(60, ts(MON, "09:00"), "salom")]), "arxiv.zip",
                                    "application/zip")
            assert "eksportda 1 ta xabar" in env.session.of("EditMessageText")[-1].text
            if reports.vouchers._task is not None:
                await reports.vouchers._task
            await env.send_text("/vaucher", user_id=LEADER)
            assert "Vaucherlar" not in env.session.of("SendMessage")[-1].text  # faqat egasiga

            # yangilanish arxivi va oddiy fayl — eksport emas
            from aiogram.types import Message

            def doc(name, uid=OWNER, caption=None):
                payload = {"message_id": 1, "date": 1760240000, "chat": {"id": uid, "type": "private"},
                           "from": {"id": uid, "is_bot": False, "first_name": "Shohrux"},
                           "document": {"file_id": "x", "file_unique_id": "ux", "file_name": name}}
                if caption:
                    payload["caption"] = caption
                return Message.model_validate(payload)

            reports.vouchers.waiting.clear()
            assert await is_voucher_export(doc("ChatExport_2026.zip"), env.ctx)
            assert not await is_voucher_export(doc("hisobot.zip"), env.ctx)
            assert not await is_voucher_export(doc("ChatExport.pdf"), env.ctx)
            assert not await is_voucher_export(doc("result.json", uid=LEADER), env.ctx)
            assert not await is_voucher_export(doc("ChatExport.zip", caption="/yangilash"), env.ctx)
            env.ctx.awaiting_update[OWNER] = 0.0
            assert not await is_voucher_export(doc("result.json"), env.ctx)
        finally:
            await env.close()
    run(go())


# --------------------------------------------------------------------------- 0 qo'ng'iroq — hisobotda yo'q
def test_zero_call_staff_hidden_in_report_and_excel(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await seed_week(env)
            reports = env.ctx.reports
            await reports.store.add_person("operator", username="dilnoza_op", user_id=None, name="Dilnoza")
            text = (await reports.compose(WED, local(WED, "20:00"), interim=False))[1]
            assert "Malika" in text and "Dilnoza" not in text
            export = await reports.archive.collect(WEEK, MON, date(2026, 10, 25), local(NEXT_MON, "09:05"))
            assert [o.name for o in export.report.operators] == ["Malika", "Sardor", "Jasur"]
            overview = await reports.overview(WED, local(WED, "20:00"))  # Mini App va AI uchun — hamma qoladi
            assert "Dilnoza" in [r.name for r in overview.rows]
        finally:
            await env.close()
    run(go())


def test_photo_dir_is_next_to_database(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            folder = env.ctx.reports.vouchers.photo_dir
            assert folder.name == "vouchers" and folder.parent == Path(env.ctx.settings.db_path).parent
        finally:
            await env.close()
    run(go())


def test_live_album_waits_and_tick_starts_worker(tmp_path):
    async def go():
        from datetime import datetime, timedelta, timezone

        from bot.reports.store import iso
        env = await start(tmp_path)
        try:
            vouchers = env.ctx.reports.vouchers
            store = vouchers.store
            now = datetime.now(timezone.utc)
            for mid, source in ((1, "live"), (2, "import")):
                await store.add_post(VOUCHERS, mid, album=None, user_id=MALIKA, name="Malika", created_at=iso(now),
                                     text="Ali Valiyev", photos=[], reply_to=None, source=source, state="pending")
            ready = await store.pending_posts(iso(now - timedelta(seconds=20)), 10)
            assert [p.message_id for p in ready] == [2]  # jonli xabar albom to'liq kelishini kutadi
            env.api.responses.append(answer("other"))
            await vouchers.tick(env.bot, local(WED, "10:00"))
            assert vouchers._task is not None
            await vouchers._task
            assert (await store.post(VOUCHERS, 2)).state == "done" and (await store.post(VOUCHERS, 1)).state == "pending"
        finally:
            await env.close()
    run(go())


def test_caption_and_text_carry_voucher_lines():
    from bot.reports.render import render_caption, render_report
    from bot.reports.stats import Overview
    ov = Overview(day=WED, start=local(WED, "09:00"), end=local(WED, "19:00"), live=False, statuses=[], rows=[], extras=[],
                  new_leads=0, unmarked=0, courses=[], conflicts=0, comments=0, pending=0, reasons=[], callbacks=0,
                  has_operator_list=False, notes=["📄 Vaucher: <b>2</b> ta yuborildi — Malika 2"])
    assert render_caption(ov).endswith("📄 Vaucher: <b>2</b> ta yuborildi — Malika 2")
    assert render_report(ov).endswith("📄 Vaucher: <b>2</b> ta yuborildi — Malika 2")


def test_broken_post_does_not_block_queue(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            vouchers = env.ctx.reports.vouchers
            store = vouchers.store
            for mid in (1, 2):
                await store.add_post(VOUCHERS, mid, album=None, user_id=MALIKA, name="Malika", created_at="2026-10-21T05:00:00",
                                     text=f"Odam {mid}", photos=[], reply_to=None, source="import", state="pending")
            calls = []

            async def broken(bot, post):
                calls.append(post.message_id)
                if post.message_id == 1:
                    raise RuntimeError("buzuq")
                await store.finish_posts([post], "done")
                return True

            vouchers._analyze = broken
            await vouchers.drain(env.bot)
            assert calls == [1, 1, 1, 2] and (await store.post(VOUCHERS, 1)).state == "failed"
            # bot ko'rmagan eski xabar tahrirlansa — yangi post bo'ladi
            v = Vouchers(env)
            await v.join()
            await env._feed({"edited_message": {"message_id": 99, "date": ts(WED, "09:00"), "edit_date": ts(WED, "09:30"),
                                                "chat": v.chat(), "from": person(MALIKA, "Malika"), "text": "Ali Valiyev"}})
            assert (await store.post(VOUCHERS, 99)).state == "pending"
        finally:
            await env.close()
    run(go())

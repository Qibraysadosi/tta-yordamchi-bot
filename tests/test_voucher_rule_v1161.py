"""v1.16.1: vaucher deb faqat ismdan tashqari telefon, JSHSHIR, pasport yoki vaucher raqami bor yozuv sanaladi.
Faqat ism (egasi «Qaysilari biziki?» deb tashlagan ro'yxat rasmi) — bazada qoladi, hech qayerda sanalmaydi:
/vaucher, voucher_list (xodimlar bo'yicha), kunlik hisobot, kunlik tavsiya, reklamadagi vaucher narxi.
Eski yozuvlarda izohdagi «Vaucher № 261-190757» alohida maydonga (voucher_no) o'tkaziladi."""
import json
from datetime import date

from bot.reports.vouchers import (
    SYSTEM,
    VoucherService,
    Voucher,
    blank,
    counts_as_voucher,
    number_in_note,
    voucher_number,
)
from bot.reports.store import iso
from test_ads_v116 import connect
from test_ai_tools_v113 import ask, owner_tools
from test_flow import OWNER, run
from test_reports import MALIKA, local, person, start, ts
from test_vouchers_v114 import DILNOZA, answer, Vouchers

MARJONA = 707
NOV2, NOV11, NOV12, NOV16 = date(2026, 11, 2), date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 16)
LIST_NAMES = ["Aliyev Vali", "Karimova Nilufar", "To'xtayev Jasur", "Raximova Sevara"]


# --------------------------------------------------------------------------- yagona tekshiruv
def test_counts_as_voucher_rule():
    name = {"name": "Aliyev Vali"}
    assert not counts_as_voucher(name)  # faqat ism — sanalmaydi
    assert counts_as_voucher({**name, "phone": "998901234567"})  # faqat telefon
    assert counts_as_voucher({**name, "phone": "90 123 45 67"})  # 9 raqam yetadi
    assert counts_as_voucher({**name, "pinfl": "12345678901234"})  # faqat JSHSHIR
    assert counts_as_voucher({**name, "voucher_no": "261-190757"})  # faqat vaucher raqami
    assert counts_as_voucher({**name, "passport": "AD1234567"})  # faqat pasport
    for empty in ("", "-", " - ", "—", "yo'q", "yoʻq", "YO'Q", "null", "None", None):
        assert blank(empty) or empty is None, empty
        assert not counts_as_voucher({**name, "phone": empty, "pinfl": empty, "passport": empty, "voucher_no": empty})
    assert not counts_as_voucher({**name, "phone": "-"})  # telefon «-» — bo'sh
    assert not counts_as_voucher({**name, "phone": "90 123 45"})  # 8 raqam — yetmaydi
    assert not counts_as_voucher({**name, "pinfl": "1234567890123", "passport": "AD123", "voucher_no": "12"})
    assert not counts_as_voucher({"name": "", "course": "Massaj", "district": "Chilonzor", "note": "biziki?"})

    # Voucher obyekti va sqlite qatori ham
    row = dict(id=1, chat_id=1, message_id=1, created_at="", sender_id=None, sender_name="", name="Ali Valiyev",
               name_key="ali valiyev", phone="", course="", district="", mahalla="", pinfl="", passport="",
               status="sent", status_at="", note="Vaucher № 261-190757")
    assert not counts_as_voucher(Voucher(**row))  # raqam faqat izohda — avval to'ldiriladi (fill_numbers)
    assert counts_as_voucher(Voucher(**row, voucher_no="261-190757"))

    assert voucher_number("№ 261-190757") == voucher_number("261 - 190757") == "261-190757"
    assert voucher_number("261190757") == "261190757"
    assert [voucher_number(v) for v in ("yo'q", "-", "12", "AD1234567", None)] == ["", "", "", "", ""]
    assert number_in_note("Vaucher № 261-190757") == "261-190757"
    assert number_in_note("vaucher raqami: 261190757, tasdiqlandi") == "261190757"
    assert number_in_note("Ваучер №261-190757") == "261-190757"
    for note in ("JSHSHIR № 12345678901234", "Tel 90 123 45 67", "kurs 2-oktabrdan", "hujjat yetishmaydi", "", None):
        assert number_in_note(note) == "", note
    assert '"voucher_no"' in SYSTEM  # AI sxemasida alohida maydon


def test_ai_fields_take_number_from_note():
    service = VoucherService.__new__(VoucherService)  # _fields xizmat holatiga bog'liq emas
    fields = service._fields({"name": "Karimov Bekzod", "note": "Vaucher № 261-190799"})
    assert fields["voucher_no"] == "261-190799" and fields["phone"] == ""
    assert service._fields({"name": "X", "voucher_no": "261-190800"})["voucher_no"] == "261-190800"
    assert service._fields({"name": "X", "voucher_no": "yo'q", "phone": "-"}) == {
        "name": "X", "phone": "", "course": "", "district": "", "mahalla": "", "pinfl": "", "passport": "", "note": "",
        "voucher_no": ""}


# --------------------------------------------------------------------------- noyabr holati
async def november(env):
    """Noyabr: 2 ta oddiy yozuv (telefon / JSHSHIR), Marjonaning 12 ta eski yozuvi (raqam faqat izohda, 8 tasi
    tasdiqlangan), 16.11 da 3 ta haqiqiy yozuv va egasining ro'yxat rasmi (2419) — 4 ta faqat ism."""
    reports = env.ctx.reports
    v = Vouchers(env)
    await v.join()
    store = reports.vouchers.store
    chat = v.chat()["id"]

    def fields(name, **extra):
        data = {"name": name, "phone": "", "course": "Kurs", "district": "", "mahalla": "", "pinfl": "",
                "passport": "", "note": ""}
        data.update(extra)
        return data

    async def old(mid, name, when, sender, **extra):  # v1.16.1 dan oldin yozilgan (voucher_no ustuni bo'sh)
        voucher_id = await store.add_voucher(chat, mid, created_at=iso(when), sender_id=sender[0],
                                             sender_name=sender[1], fields=fields(name, **extra), status="sent",
                                             status_msg=None)
        await store.add_event(voucher_id, "sent", iso(when), chat, mid)
        return voucher_id

    await old(100, "Sobirov Akmal", local(NOV2, "10:00"), (DILNOZA, "Dilnoza"), phone="998901112233")
    await old(101, "Qodirova Madina", local(NOV2, "11:00"), (DILNOZA, "Dilnoza"), pinfl="31234567890123")
    marjona = []
    for n in range(12):  # 11.11 15:04–15:40: telefon ham, JSHSHIR ham yo'q — vaucher raqami izohda
        when = local(NOV11, f"15:{4 + n * 3:02d}")
        marjona.append(await old(200 + n, f"Marjona yozgan Odam{n}", when, (MARJONA, "Marjona"),
                                 note=f"Vaucher № 261-1907{50 + n}"))
    for n, voucher_id in enumerate(marjona[:8]):  # 8 tasi tasdiqlangan
        await store.update_voucher(voucher_id, {"status": "approved", "status_at": iso(local(NOV12, "10:00"))})
        await store.add_event(voucher_id, "approved", iso(local(NOV12, "10:00")), chat, 300 + n)

    dilnoza, malika, owner = person(DILNOZA, "Dilnoza"), person(MALIKA, "Malika", "malika_op"), person(OWNER, "Shohrux")
    await v.say(dilnoza, ts(NOV16, "10:00"), "Aliyev Sardor, 90 111 22 33, Elektrik")
    await v.say(malika, ts(NOV16, "11:00"), "Usmonova Dilfuza JSHSHIR 41234567890123")
    v.mid = 2418
    listing = await v.say(owner, ts(NOV16, "13:07"), photo="list", caption="Qaysilari biziki?")
    assert listing["message_id"] == 2419
    await v.say(dilnoza, ts(NOV16, "14:00"), "Karimov Bekzod — Vaucher № 261-190799")
    env.api.responses += [
        answer("voucher", {"name": "Aliyev Sardor", "phone": "90 111 22 33", "course": "Elektrik"}),
        answer("voucher", {"name": "Usmonova Dilfuza", "pinfl": "41234567890123"}),
        answer("voucher", *({"name": n, "phone": "-", "pinfl": "", "passport": "yo'q", "status": "sent"}
                            for n in LIST_NAMES)),
        answer("voucher", {"name": "Karimov Bekzod", "note": "Vaucher № 261-190799"}),
    ]
    assert await reports.vouchers.drain(env.bot) == 4
    return v, listing


def test_november_list_photo_is_not_counted(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            reports = env.ctx.reports
            v, listing = await november(env)
            store = reports.vouchers.store
            reports.now = lambda: local(NOV16, "21:00")
            tools = owner_tools(env)
            month = {"period": "this_month", "calendar": True}

            # bot yangilangunga qadar: Marjonaning raqami faqat izohda — u ham sanalmaydi
            before_rows = [dict(r) for r in await store._all("SELECT * FROM rp_vouchers ORDER BY id")]
            assert len(before_rows) == 21
            found = await ask(tools, "voucher_list", **month)
            assert (found["total"], found["not_counted"]["count"]) == (5, 16)

            # ishga tushganda — izohdagi raqam alohida maydonga (bir marta; boshqa hech narsa o'zgarmaydi)
            reports._ready = False
            await reports.ensure()
            assert await store.fill_numbers() == 0
            after_rows = [dict(r) for r in await store._all("SELECT * FROM rp_vouchers ORDER BY id")]
            assert [r["voucher_no"] for r in after_rows if r["sender_name"] == "Marjona"] == [
                f"261-1907{50 + n}" for n in range(12)]
            assert [{k: x for k, x in r.items() if k != "voucher_no"} for r in after_rows] == \
                [{k: x for k, x in r.items() if k != "voucher_no"} for r in before_rows]
            assert next(r for r in after_rows if r["name"] == "Karimov Bekzod")["voucher_no"] == "261-190799"

            # noyabr: 21 ta yozuvdan faqat ro'yxat rasmidagi 4 tasi chiqib ketadi
            found = await ask(tools, "voucher_list", **month, limit=200)
            listed = {p["name"] for p in found["people"]}
            assert found["total"] == 17 and listed == {r["name"] for r in after_rows} - set(LIST_NAMES)
            weak = found["not_counted"]
            assert (weak["count"], weak["label"]) == (4, "⚠️ ma'lumot yetarli emas: 4 ta")
            assert [p["name"] for p in weak["people"]] == LIST_NAMES
            assert {p["link"] for p in weak["people"]} == {f"https://t.me/c/1444444444/{listing['message_id']}"}
            assert {s["name"]: s["sent"] for s in found["by_sender"]} == {"Marjona": 12, "Dilnoza": 4, "Malika": 1}
            assert found["totals"] == {"yuborildi": 17, "tasdiqlandi": 8}
            assert next(p for p in found["people"] if p["name"] == "Marjona yozgan Odam0")["voucher_no"] == "261-190750"
            assert (await ask(tools, "voucher_list", **month, search="261-190757"))["people"][0]["name"] == \
                "Marjona yozgan Odam7"
            masked = await reports.ai_tools(masked=True).run("voucher_list", month)
            assert json.loads(masked)["not_counted"]["count"] == 4 and "261-1907" not in masked

            # kunlik hisobot va /vaucher
            lines = await reports.vouchers.report_lines(local(NOV16, "00:00"), local(NOV16, "21:00"))
            assert lines == ["📄 Vaucher: <b>3</b> ta yuborildi — Dilnoza 2, Malika 1"]
            await env.send_text("/vaucher")
            text = env.session.of("SendMessage")[-1].text
            assert "Bugun: <b>3</b> ta yuborildi · ⚠️ ma'lumot yetarli emas: 4 ta" in text
            assert "Shu oy: <b>17</b> ta yuborildi · ✅ tasdiqlandi 8 · ⚠️ ma'lumot yetarli emas: 4 ta" in text
            assert "🏆 Shu oy: Marjona 12 · Dilnoza 4 · Malika 1" in text and "Shohrux" not in text
            assert "Jami ro'yxatda: 17 ta odam · ⚠️ ma'lumot yetarli emas: 4 ta" in text and "faqat ism yozilgan" in text

            # kunlik tavsiya va reklamadagi vaucher narxi
            data = await reports.advisor.digest(NOV16, local(NOV16, "20:30"))
            assert data["vouchers"]["total"] == 3 and data["vouchers_month"]["total"] == 17
            packed = json.dumps(data["vouchers"], ensure_ascii=False)
            assert not any(name in packed for name in LIST_NAMES) and "not_counted" not in packed
            connect(env)
            summary = await reports.ads.summary(NOV11, NOV16, local(NOV16, "21:00"))
            assert (summary["vouchers"], summary["cost_per_voucher"]) == (15, round(50 / 15, 2))

            # keyin telefon qo'shilsa — o'zi sanala boshlaydi; bazadan hech narsa o'chmaydi
            await v.say(person(MALIKA, "Malika", "malika_op"), ts(NOV16, "15:00"), "Aliyev Vali — 90 123 45 67",
                        reply=listing)
            env.api.responses.append(answer("update", {"name": "Aliyev Vali", "phone": "90 123 45 67", "status": "sent"}))
            assert await reports.vouchers.drain(env.bot) == 1
            found = await ask(tools, "voucher_list", **month, limit=200)
            assert (found["total"], found["not_counted"]["count"]) == (18, 3)
            assert "Aliyev Vali" in {p["name"] for p in found["people"]}
            assert len(await store._all("SELECT id FROM rp_vouchers")) == 21
            assert len(await store.events_between(iso(local(NOV2, "00:00")), iso(local(NOV16, "21:00")), counted=None)) \
                == 21 + 8
        finally:
            await env.close()
    run(go())

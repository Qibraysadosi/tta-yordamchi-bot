"""v1.18: sertifikat.mehnat.uz bilan solishtirish (/solishtir) — tizim ro'yxati (Excel / CSV / HTML yoki rasm) har
qatori vaucher guruhi yozuvlari va lidlar bilan JSHSHIR / F.I.Sh. bo'yicha solishtiriladi, holatlar bazada yangilanadi,
natija — xulosa va Excel («Solishtirish», «Ro'yxatda yo'q»)."""
import asyncio
import io
import json
from datetime import date, datetime

import pytest
from openpyxl import Workbook, load_workbook

import fakes
from bot.reports.certs import CertError, close, header_type, map_status, name_score, name_words, read_table
from bot.reports.store import iso
from test_archive import FRI, MON, SAT, THU, queue_analysis, seed_week
from test_assistant import LEADER
from test_flow import OWNER, run
from test_reports import MALIKA, TZ, local, start
from test_vouchers_v114 import DILNOZA, VOUCHERS, Vouchers, jpeg

MARJONA = 707
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = ["№", "Фуқаронинг Ф.И.Ш.", "ЖШШИР", "Телефон рақами", "Туман", "Касб йўналиши", "Ваучер рақами",
           "Ариза санаси", "Ҳолати", "Туғилган санаси"]


# --------------------------------------------------------------------------- ustunlar, holatlar, ismlar
def test_headers_statuses_and_names():
    kinds = {h: header_type(h) for h in HEADERS + ["Фамилия, исми, отасининг исми", "Фамилия", "Исми",
                                                  "Отасининг исми", "Паспорт серияси ва рақами", "ПИНФЛ", "ФИО",
                                                  "Ваучер берилган сана", "Таълим муассасаси", "Статус", "Т/р"]}
    assert kinds == {
        "№": None, "Фуқаронинг Ф.И.Ш.": "name", "ЖШШИР": "pinfl", "Телефон рақами": "phone", "Туман": "district",
        "Касб йўналиши": "course", "Ваучер рақами": "voucher_no", "Ариза санаси": "date", "Ҳолати": "status",
        "Туғилган санаси": "birth", "Фамилия, исми, отасининг исми": "name", "Фамилия": "last", "Исми": "first",
        "Отасининг исми": "middle", "Паспорт серияси ва рақами": "passport", "ПИНФЛ": "pinfl", "ФИО": "name",
        "Ваучер берилган сана": "date", "Таълим муассасаси": "institution", "Статус": "status", "Т/р": "skip"}
    statuses = {s: map_status(s) for s in ("Ўқишни тугатган", "Ўқишни тугатмаган", "Ўқиш жараёнида", "Тасдиқланган",
                                           "Рад этилган", "Бекор қилинган", "Ариза берилган", "Четлаштирилган",
                                           "confirmed", "started", "Noma'lum", "")}
    assert statuses == {
        "Ўқишни тугатган": "finished", "Ўқишни тугатмаган": "other", "Ўқиш жараёнида": "studying",
        "Тасдиқланган": "approved", "Рад этилган": "rejected", "Бекор қилинган": "rejected", "Ариза берилган": "sent",
        "Четлаштирилган": "other", "confirmed": "approved", "started": "studying", "Noma'lum": None, "": None}

    def score(a, b):
        found = name_score(name_words(a), name_words(b))
        return found[0] if found else None

    assert score("КАРИМОВА АЗИЗА РУСТАМ ҚИЗИ", "Aziza Karimova") == 3  # kirill / lotin, tartib, «qizi» farqsiz
    assert score("Рахимова Севара", "Raximova Sevara") == 3 and score("Қодиров Жасур", "Kodirov Jasur") == 3  # h/x, q/k
    assert score("Юсуфов Акмал", "Yusupov Akmal") == 2  # bitta so'z biroz boshqacha
    assert score("Каримов Азиз", "Karimova Aziza") is None  # ayol / erkak shakli — boshqa odam
    assert score("Юсуфов Ахмад", "Yusupov Akmal") is None  # ikki so'z farq qiladi
    assert score("Aziza", "Aziza Karimova") is None  # bitta so'z — F.I.Sh. emas
    assert close("abdurahmon", "abdurakhmon") and not close("aziz", "aziza") and not close("ali", "oli")


# --------------------------------------------------------------------------- fayllar
def official_xlsx(rows, *, headers=HEADERS, title=True):
    wb = Workbook()
    ws = wb.active
    if title:
        ws.append(["Касб-ҳунарга ўқитиш ваучерлари (Фуқаролар аризалари)"])
        ws.append([])
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_read_table_formats():
    data = official_xlsx([
        [1, "КАРИМОВА АЗИЗА РУСТАМ ҚИЗИ", 12345678901234, "+998 90 123 45 67", "Чилонзор", "Компьютер", None,
         datetime(2026, 10, 22, 9, 30), "Ўқиш жараёнида", datetime(1999, 5, 1)],
        [None] * 10,
        HEADERS,  # sahifa boshida takrorlangan sarlavha
        [2, "Бобуров Бобур", 3.1234567890123e13, "93 765 43 21; 90 000 00 01", "", "", "261-190757", "23.10.2026",
         "Ўқишни тугатган", None],
        ["", "Жами: 2", None, None, None, None, None, None, None, None],
    ])
    table = read_table(data, "ro'yxat.xlsx", TZ)
    assert [p.name for p in table.people] == ["КАРИМОВА АЗИЗА РУСТАМ ҚИЗИ", "Бобуров Бобур"]
    first, second = table.people
    assert (first.pinfl, first.phones, first.status) == ("12345678901234", frozenset({"998901234567"}), "studying")
    assert first.date == datetime(2026, 10, 22, 9, 30, tzinfo=TZ) and first.district == "Чилонзор"
    assert second.pinfl == "31234567890123" and second.phones == {"998937654321", "998900000001"}
    assert (second.voucher_no, second.date, second.status) == ("261-190757", datetime(2026, 10, 23, tzinfo=TZ), "finished")

    split = official_xlsx([["Каримова", "Азиза", "Рустам қизи", "12.10.2026"]],
                          headers=["Фамилия", "Исми", "Отасининг исми", "Сана"], title=False)
    assert [p.name for p in read_table(split, "x.xlsx", TZ).people] == ["Каримова Азиза Рустам қизи"]
    csv_people = read_table("Ф.И.Ш.;ЖШШИР;Ҳолати\nTOSHMATOV SHERZOD;12345678901234;Tasdiqlangan\n".encode(), "a.csv", TZ).people
    assert [(p.name, p.pinfl, p.status) for p in csv_people] == [("TOSHMATOV SHERZOD", "12345678901234", "approved")]
    russian = "ФИО,ПИНФЛ,Статус\nТошматов Шерзод,12345678901234,Отказ\n".encode("cp1251")  # eski Excel CSV'si
    assert [(p.name, p.status) for p in read_table(russian, "b.csv", TZ).people] == [("Тошматов Шерзод", "rejected")]
    page = ("<html><body><table><tr><th>T/r</th><th>F.I.Sh.</th><th>Holati</th></tr>"
            "<tr><td>1</td><td>Aliyev<br>Vali</td><td>Rad etilgan</td></tr></table></body></html>").encode()
    assert [(p.name, p.status) for p in read_table(page, "eksport.xls", TZ).people] == [("Aliyev Vali", "rejected")]
    with pytest.raises(CertError, match="eski .xls"):
        read_table(b"\xd0\xcf\x11\xe0" + b"\0" * 100, "eski.xls", TZ)
    with pytest.raises(CertError, match="F.I.Sh. ustuni topilmadi"):
        read_table(official_xlsx([["a", "b"]], headers=["Kurs", "Narx"]), "x.xlsx", TZ)


# --------------------------------------------------------------------------- to'liq oqim
async def add_voucher(env, mid, name, when, sender, **extra):
    store = env.ctx.reports.vouchers.store
    fields = {"name": name, "phone": "", "course": "Kurs", "district": "", "mahalla": "", "pinfl": "", "passport": "",
              "note": "", **extra}
    voucher_id = await store.add_voucher(VOUCHERS, mid, created_at=iso(when), sender_id=sender[0],
                                         sender_name=sender[1], fields=fields, status="sent", status_msg=None)
    await store.add_event(voucher_id, "sent", iso(when), VOUCHERS, mid)
    return voucher_id


async def prepare(env):
    await seed_week(env)  # lidlar: Aziza Karimova (dush, Malika 👍), Bobur (sesh, Sardor), Dilshod, Nodira (pay)
    await queue_analysis(env)
    reports = env.ctx.reports
    await reports.analyze_pending()
    await Vouchers(env).join()
    dilnoza, marjona, malika = (DILNOZA, "Dilnoza"), (MARJONA, "Marjona"), (MALIKA, "Malika")
    ids = {
        "aziza": await add_voucher(env, 9001, "Karimova Aziza Rustam qizi", local(THU, "12:00"), dilnoza,
                                   pinfl="12345678901234"),
        "sherzod": await add_voucher(env, 9002, "Toshmatov Sherzod", local(THU, "13:00"), marjona,
                                     voucher_no="261-190757"),
        "bobur": await add_voucher(env, 9003, "Boburov Bobur", local(FRI, "10:00"), dilnoza, phone="998937654321"),
        "akmal": await add_voucher(env, 9004, "Yusupov Akmal", local(FRI, "11:00"), malika, phone="998901112233"),
        "sevara": await add_voucher(env, 9005, "Raximova Sevara", local(FRI, "12:00"), (OWNER, "Shohrux")),  # faqat ism
        "jasur": await add_voucher(env, 9006, "Ergashev Jasur", local(FRI, "14:00"), dilnoza, phone="998977777777"),
    }
    return ids


OFFICIAL = [
    [1, "КАРИМОВА АЗИЗА РУСТАМ ҚИЗИ", 12345678901234, "+998 90 123 45 67", "Чилонзор", "Компьютер", None,
     datetime(2026, 10, 22), "Ўқиш жараёнида", None],
    [2, "Тошматов Шерзод Алиевич", None, None, "", "Электрик", "№ 261-190757", "22.10.2026", "Тасдиқланган", None],
    [3, "БОБУРОВ БОБУР", 3.1234567890123e13, "93 765 43 21", "", "", None, "23.10.2026", "Ўқишни тугатган", None],
    [4, "Юсуфов Акмал", None, None, "", "", None, "23.10.2026", "Рад этилган", None],
    [5, "Саидова Нодира", None, None, "", "", None, "23.10.2026", "Ариза берилган", None],
    [6, "Қодиров Жасур Бахтиёрович", None, None, "", "", None, "23.10.2026", "Тасдиқланган", None],
    [7, "Рахимова Севара", None, None, "", "", None, "23.10.2026", "Тасдиқланган", None],
]


def sheet_rows(data, title):
    ws = load_workbook(io.BytesIO(data))[title]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def test_compare_flow_sync_and_excel(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            ids = await prepare(env)
            reports = env.ctx.reports
            store = reports.vouchers.store
            reports.now = lambda: local(SAT, "18:00")

            await env.send_text("/solishtir")
            assert "sertifikat.mehnat.uz bilan solishtirish" in env.session.of("SendMessage")[-1].text
            await env.send_document("c1", official_xlsx(OFFICIAL), "vaucherlar.xlsx", XLSX)
            text = env.last_edit().text
            assert "Ro'yxat: <b>7</b> kishi · 22.10–23.10.2026 (vaucherlar.xlsx)" in text
            assert "✅ Biziki: <b>5</b> — vaucher guruhida 4, faqat lid 1" in text
            assert "Yuborganlar: Dilnoza 2, Marjona 1, Malika 1" in text and "Shohrux" not in text
            assert "Lidga qo'ng'iroq qilganlar: Malika 1, Sardor 1" in text and "❌ Topilmadi: <b>2</b>" in text
            assert ("📊 Biznikilarning tizimdagi holati: 📤 yuborildi 1 · ✅ tasdiqlandi 1 · ❌ rad etildi 1 · "
                    "📚 o'qiyapti 1 · 🎓 tugatdi 1") in text
            assert "🔄 Bazada yangilandi: 3 ta holat, 2 ta yozuv to'ldirildi" in text
            assert "📭 Guruhda yozilgan, ro'yxatda yo'q: 1 ta" in text
            assert "⚠️ Guruhda faqat ismi bor (sanalmaydi): 1 ta" in text
            name, data, _ = env.session.documents[-1]
            assert name == "solishtirish_24.10.2026_1800.xlsx"

            rows = sheet_rows(data, "Solishtirish")
            assert rows[0][:2] == ["№", "F.I.Sh. (tizim)"] and len(rows) == 8
            table = {r[1]: dict(zip(rows[0], r)) for r in rows[1:]}
            aziza = table["КАРИМОВА АЗИЗА РУСТАМ ҚИЗИ"]
            assert (aziza["Bizniki?"], aziza["Qayerdan"], aziza["Yuborgan"]) == ("Ha", "vaucher guruhi + lid", "Dilnoza")
            assert aziza["Lid"] == "Aziza Karimova · 19.10.2026" and aziza["Lidga qo'ng'iroq qilgan"] == "Malika"
            assert aziza["Izoh"].startswith("JSHSHIR mos; F.I.Sh. mos va telefon mos (lid); to'ldirildi: telefon") and "holat: 📤 yuborildi → 📚 o'qiyapti" in aziza["Izoh"]
            assert "vaucher raqami mos" in table["Тошматов Шерзод Алиевич"]["Izoh"]
            bobur = table["БОБУРОВ БОБУР"]
            assert bobur["Izoh"].startswith("F.I.Sh. mos va telefon mos; ism va telefon mos (lid)")
            assert "to'ldirildi: JSHSHIR" in bobur["Izoh"] and bobur["Lidga qo'ng'iroq qilgan"] == "Sardor"
            akmal = table["Юсуфов Акмал"]
            assert akmal["Bizniki?"] == "Ha" and "Yusufov ↔ Yusupov" in akmal["Izoh"] and "holat yangilanmadi" in akmal["Izoh"]
            nodira = table["Саидова Нодира"]
            assert (nodira["Bizniki?"], nodira["Qayerdan"]) == ("Ha", "lid") and "ro'yxatda shu ismli bitta" in nodira["Izoh"]
            assert table["Қодиров Жасур Бахтиёрович"]["Bizniki?"] == "Yo'q"
            sevara = table["Рахимова Севара"]
            assert sevara["Bizniki?"] == "Yo'q" and "guruhda faqat ismi bor" in sevara["Izoh"] and "Shohrux" in sevara["Izoh"]
            missing = sheet_rows(data, "Ro'yxatda yo'q")
            assert [r[1] for r in missing[1:2]] == ["Ergashev Jasur"] and missing[1][6] == "Dilnoza"

            # bazada: holatlar (tarixga «tizimdan» — message_id 0) va bo'sh maydonlar; boshqalari o'zgarmagan
            rows = {r["id"]: dict(r) for r in await store._all("SELECT * FROM rp_vouchers")}
            assert {k: rows[v]["status"] for k, v in ids.items()} == {
                "aziza": "studying", "sherzod": "approved", "bobur": "finished", "akmal": "sent", "sevara": "sent",
                "jasur": "sent"}
            assert (rows[ids["bobur"]]["pinfl"], rows[ids["aziza"]]["phone"]) == ("31234567890123", "998901234567")
            events = await store._all("SELECT voucher_id, status FROM rp_voucher_events WHERE message_id = 0 ORDER BY id")
            assert [(e["voucher_id"], e["status"]) for e in events] == [
                (ids["aziza"], "studying"), (ids["sherzod"], "approved"), (ids["bobur"], "finished")]
            lines = await reports.vouchers.report_lines(local(SAT, "00:00"), local(SAT, "19:00"))
            assert lines == ["📄 Vaucher: <b>0</b> ta yuborildi · 🔄 tizimdan: ✅ tasdiqlandi 1, 📚 o'qiyapti 1, "
                             "🎓 tugatdi 1"]

            # qayta yuborilsa — o'zgarish yo'q
            await env.send_text("/solishtir")
            await env.send_document("c2", official_xlsx(OFFICIAL), "vaucherlar.xlsx", XLSX)
            assert "🔄 Bazada yangilandi" not in env.last_edit().text and "✅ Biziki: <b>5</b>" in env.last_edit().text
            assert len(await store._all("SELECT id FROM rp_voucher_events WHERE message_id = 0")) == 3

            # faqat egasi; /solishtir'siz Excel — oddiy AI suhbatiga
            await reports.store.add_person("leader", username=None, user_id=LEADER, name="Dilshod aka")
            await env.send_text("/solishtir", user_id=LEADER)  # rahbarga — o'z menyusi, solishtirish emas
            assert "solishtirish" not in env.session.of("SendMessage")[-1].text and not reports.certs.waits(LEADER)
            assert not reports.certs.waits(OWNER)
        finally:
            await env.close()
    run(go())


def test_caption_photo_album_and_errors(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await prepare(env)
            reports = env.ctx.reports
            reports.now = lambda: local(SAT, "18:00")
            env.api.responses.append(fakes.json_answer(json.dumps({"rows": [
                {"name": "Karimova Aziza Rustam qizi", "pinfl": "12345678901234", "status": "Ўқишни тугатган",
                 "date": "22.10.2026"},
                {"name": "Noma'lum Odam", "status": "Тасдиқланган"},
            ]}, ensure_ascii=False), input_tokens=3000, output_tokens=200))
            before = len(env.api.requests)
            await env.send_text("/solishtir")
            await asyncio.gather(env.send_photo("r1", jpeg(), media_group_id="al1"),
                                 env.send_photo("r2", jpeg("gray"), media_group_id="al1"))
            calls = env.api.requests[before:]
            assert len(calls) == 1 and [b["type"] for b in calls[0]["messages"][0]["content"]] == ["text", "image", "image"]
            text = env.last_edit().text
            assert "Ro'yxat: <b>2</b> kishi · 22.10–22.10.2026 (rasm (2 ta))" in text and "✅ Biziki: <b>1</b>" in text
            assert "🔄 Bazada yangilandi: 1 ta holat" in text and "Rasmni AI o'qidi: $" in text
            spent, count = await env.db.spend(0)
            assert spent > 0 and count >= 1  # xarajat «📊 Guruh tahlili» qatorida

            # fayl izohida /solishtir — darrov; ro'yxat bo'lmagan fayl va tushunarsiz jadval
            data = official_xlsx([[1, "Ergashev Jasur", None, "97 777 77 77", "", "", None, "23.10.2026", "", None]])
            await env.send_document("c3", data, "r.xlsx", XLSX, caption="/solishtir")
            assert "✅ Biziki: <b>1</b> — vaucher guruhida 1" in env.last_edit().text
            await env.send_document("c4", b"%PDF-1.4", "hujjat.pdf", "application/pdf", caption="/solishtir")
            assert "Bu fayl ro'yxat emas" in env.session.of("SendMessage")[-1].text
            await env.send_text("/solishtir")
            await env.send_document("c5", official_xlsx([["a"]], headers=["Narx"]), "x.xlsx", XLSX)
            assert env.last_edit().text.startswith("⚠️ Jadvalda F.I.Sh. ustuni topilmadi")
        finally:
            await env.close()
    run(go())


def test_list_dates_and_no_dates(tmp_path):
    async def go():
        env = await start(tmp_path)
        try:
            await prepare(env)
            reports = env.ctx.reports
            table = read_table(official_xlsx([[1, "Ergashev Jasur", None, "97 777 77 77"]],
                                             headers=["№", "Ф.И.Ш.", "ЖШШИР", "Телефон"], title=False), "x.xlsx", TZ)
            result = await reports.certs.compare(table, local(SAT, "18:00"), source="x.xlsx")
            assert result.period is None and result.missing == [] and len(result.found) == 1
            assert "Ro'yxatda sana ustuni yo'q" in result.notes[0]
            text = reports.certs.summary_text(result)
            assert "📭" not in text and "ℹ️ Ro'yxatda sana ustuni yo'q" in text
            # lid ro'yxatdagi sanadan keyin tushgan bo'lsa — bog'lanmaydi
            late = read_table(official_xlsx([[1, "Karimova Aziza", None, None, "", "", None, "01.10.2026", "", None]]),
                              "x.xlsx", TZ)
            result = await reports.certs.compare(late, local(SAT, "18:00"))
            assert result.matches[0].lead is None and result.matches[0].voucher is not None
            assert date(2026, 10, 1) == result.period[0].date() and MON > result.period[0].date()
        finally:
            await env.close()
    run(go())

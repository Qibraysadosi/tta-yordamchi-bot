"""Claude vositalari (client tools): fayl yaratish, grafik, PDF amallari, hujjat tarjimasi, solishtirish.

Claude javob yozayotganda vositani chaqiradi (stop_reason="tool_use"); bot vositani bajarib, natijani
tool_result sifatida qaytaradi va Claude javobni davom ettiradi (bot/claude.py → run_turn).
Yaratilgan fayllar foydalanuvchiga to'g'ridan-to'g'ri chatga yuboriladi."""
from __future__ import annotations

import asyncio
import difflib
import io
import logging
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile, ReplyParameters

from . import doctrans, files, media, pdfs

log = logging.getLogger(__name__)

RAW_TTL = 2 * 3600  # yuborilgan fayllarning asl nusxasi shuncha vaqt vositalar uchun saqlanadi
RAW_MAX_FILES = 12
RAW_MAX_BYTES = 60 * 1024 * 1024
DIFF_LIMIT = 40_000

FORMATS = {
    "docx": "Word", "pdf": "PDF", "pptx": "PowerPoint", "xlsx": "Excel", "csv": "CSV", "md": "Markdown", "txt": "Matn",
}
TEXT_KINDS = ("pdf", "docx", "xlsx", "text")

# Tizim ko'rsatmasiga qo'shiladi (o'zgarmas matn — kesh ishlayveradi)
TOOLS_GUIDE = """Vositalar (fayl bilan ishlash):
- Foydalanuvchi fayl so'rasa («Word qilib ber», «Excel jadval», «taqdimot», «PDF») yoki natija uzun rasmiy hujjat (xat, shartnoma, hisobot, reja, jadval) bo'lsa — create_document bilan fayl yarat. Fayl yuborilgach chatda 1–3 jumlalik izoh yoz (nima tayyorlandi, nimaga e'tibor berish kerak); hujjat matnini chatda takrorlama.
- Raqamli ma'lumotni vizual ko'rsatish foydali bo'lsa (Excel tahlili, taqqoslash, dinamika) — create_chart.
- Skanerlangan rasm yoki PDF'dan Word so'ralsa: undagi matnni to'liq va aniq ko'chir (so'zlarni o'zgartirma; sarlavha, abzats va jadval tuzilmasini saqla), so'ng create_document(docx).
- PDF'larni birlashtirish, sahifalarini olish/bo'lish/burish, rasmlarni PDF qilish — pdf_tool.
- Word faylni formatini saqlab boshqa tilga o'girish so'ralsa — translate_document (matnni o'zing qayta terma).
- Ikki faylni solishtirish so'ralsa — compare_documents, so'ng farqlarni tushunarli tushuntir.
- Fayl nomini mazmunli qo'y (masalan «Hisobot_2026_sentabr»). Bir so'rovda kerakli fayllarni yaratib bo'lgach javobni yakunla; bir xil faylni qayta yaratma. Vosita xato qaytarsa, sababini foydalanuvchiga qisqa tushuntir."""


def specs() -> list[dict]:
    """Claude'ga yuboriladigan vositalar ro'yxati (JSON schema)."""
    return [
        {
            "name": "create_document",
            "description": (
                "Fayl yaratib foydalanuvchiga Telegram orqali yuboradi: Word (docx), PDF, PowerPoint (pptx), "
                "Excel (xlsx), CSV, Markdown (md) yoki matn (txt). docx/pdf/md/txt uchun content (Markdown) ber; "
                "xlsx/csv uchun sheets; pptx uchun slides. Jadval yoki slaydlar berilmasa, content ichidagi "
                "Markdown jadvallar / '# sarlavha + - bandlar' ishlatiladi."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": list(FORMATS)},
                    "filename": {"type": "string", "description": "Fayl nomi (kengaytmasiz bo'lsa ham bo'ladi)"},
                    "title": {"type": "string", "description": "Hujjat sarlavhasi (docx/pdf/pptx; ixtiyoriy)"},
                    "content": {
                        "type": "string",
                        "description": (
                            "Hujjat matni Markdown'da: # sarlavhalar, ro'yxatlar, **qalin**, jadvallar "
                            "(| a | b |), kod bloklari. docx/pdf/md/txt uchun majburiy."
                        ),
                    },
                    "sheets": {
                        "type": "array",
                        "description": (
                            "xlsx varaqlari (csv — faqat bitta varaq). Sonlarni son sifatida ber (ID, hisob raqami — "
                            "matn); Excel formulalarini '=SUM(B2:B9)' "
                            "ko'rinishida satr qilib berish mumkin."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "columns": {"type": "array", "items": {"type": "string"}},
                                "rows": {
                                    "type": "array",
                                    "items": {"type": "array", "items": {"type": ["string", "number", "null"]}},
                                },
                            },
                            "required": ["rows"],
                        },
                    },
                    "slides": {
                        "type": "array",
                        "description": (
                            "pptx slaydlari: har birida sarlavha va 3–6 ta qisqa band. Birinchi slaydning subtitle "
                            "maydoni titul slaydda chiqadi; notes — ma'ruzachi izohi."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "subtitle": {"type": "string"},
                                "notes": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["format", "filename"],
            },
        },
        {
            "name": "create_chart",
            "description": (
                "Grafik (diagramma) chizib rasm sifatida yuboradi: bar — ustunli, barh — gorizontal ustunli, "
                "line — chiziqli, pie — doira. Ma'lumotni foydalanuvchi faylidan yoki javobdan ol."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["bar", "barh", "line", "pie"]},
                    "title": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "Kategoriya (X o'qi) nomlari"},
                    "series": {
                        "type": "array",
                        "description": "Qatorlar; har birida labels soniga teng qiymatlar. pie uchun bitta qator.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "values": {"type": "array", "items": {"type": "number"}},
                            },
                            "required": ["values"],
                        },
                    },
                    "x_label": {"type": "string"},
                    "y_label": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["type", "labels", "series"],
            },
        },
        {
            "name": "pdf_tool",
            "description": (
                "Foydalanuvchi shu suhbatda yuborgan PDF va rasm fayllari ustida amallar: merge — bir nechta PDF'ni "
                "birlashtirish; extract — ko'rsatilgan sahifalarni yangi PDF qilish; split — har sahifani alohida "
                "PDF qilish (zip); rotate — sahifalarni burish; images_to_pdf — rasmlarni bitta PDF'ga yig'ish; "
                "info — sahifalar soni va ma'lumot. Natija foydalanuvchiga yuboriladi. Fayl nomi — yuborilgan "
                "hujjatning nomi (document title)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["merge", "extract", "split", "rotate", "images_to_pdf", "info"]},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fayl nomlari (tartib muhim). Berilmasa — oxirgi marta yuborilgan mos fayllar.",
                    },
                    "pages": {"type": "string", "description": "Sahifalar, masalan '1-3,5' (extract, rotate)"},
                    "angle": {"type": "integer", "description": "rotate uchun: 90, 180 yoki 270"},
                    "filename": {"type": "string", "description": "Natija faylining nomi (ixtiyoriy)"},
                },
                "required": ["action"],
            },
        },
        {
            "name": "translate_document",
            "description": (
                "Yuborilgan Word (.docx) faylni formatini (uslublar, jadvallar, rasmlar, sarlavha-izohlar) saqlagan "
                "holda boshqa tilga tarjima qilib, yangi .docx yuboradi. Faqat foydalanuvchi faylni tarjima "
                "qilishni so'raganda ishlat; oddiy matn tarjimasiga kerak emas."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_language": {"type": "string", "description": "Masalan: o'zbek (lotin), rus, ingliz"},
                    "file": {"type": "string", "description": "Fayl nomi; berilmasa oxirgi yuborilgan .docx"},
                    "glossary": {"type": "string", "description": "Atamalar lug'ati yoki uslub ko'rsatmalari (ixtiyoriy)"},
                },
                "required": ["target_language"],
            },
        },
        {
            "name": "compare_documents",
            "description": (
                "Ikki yuborilgan hujjat (docx, pdf, xlsx, txt) matnini qatorma-qator solishtirib farqlarni qaytaradi "
                "('-' — birinchi fayldan olib tashlangan, '+' — ikkinchisiga qo'shilgan). Natijani foydalanuvchiga "
                "tushunarli xulosa qilib ber."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_a": {"type": "string", "description": "Birinchi (eski) fayl nomi; berilmasa — oldinroq yuborilgani"},
                    "file_b": {"type": "string", "description": "Ikkinchi (yangi) fayl nomi"},
                },
            },
        },
    ]


# ------------------------------------------------------------------ yuborilgan fayllarning asl nusxasi
@dataclass
class RawFile:
    name: str
    kind: str  # image | pdf | docx | xlsx | text
    data: bytes
    stamp: float = field(default_factory=time.monotonic)
    batch: int = 0  # qaysi suhbat navbatigacha yuborilgan (RawStore belgilaydi) — nom berilmasa oxirgi to'plam olinadi


class RawStore:
    """Foydalanuvchi yaqinda yuborgan fayllarning asl baytlari — PDF amallari, tarjima va solishtirish uchun."""

    def __init__(self, ttl: float = RAW_TTL, max_files: int = RAW_MAX_FILES, max_bytes: int = RAW_MAX_BYTES) -> None:
        self.ttl, self.max_files, self.max_bytes = ttl, max_files, max_bytes
        self._items: dict[int, list[RawFile]] = {}
        self._turns: dict[int, int] = {}

    def add(self, user_id: int, items: list[RawFile]) -> None:
        for item in items:  # keyingi navbatgacha yuborilganlar bitta to'plam (albom, kutilayotgan fayllar)
            item.batch = self._turns.get(user_id, 0)
        kept = self._fresh(user_id) + list(items)
        while len(kept) > self.max_files or sum(len(f.data) for f in kept) > self.max_bytes:
            if len(kept) <= 1:
                break
            kept.pop(0)
        self._items[user_id] = kept

    def recent(self, user_id: int) -> list[RawFile]:
        """Suhbat navbati (Claude so'rovi) uchun yaqindagi fayllar (to'plamni o'zgartirmaydi)."""
        return self._fresh(user_id)

    def close_batch(self, user_id: int) -> None:
        """Fayl ishlatgan navbat saqlandi — bundan keyin yuborilganlar yangi to'plam bo'ladi.

        Vaqtinchalik xatoda (fayllar navbatga qaytganda) yoki faylsiz navbatda (🔄, ilovadan savol) chaqirilmaydi:
        kutayotgan va keyin yuborilgan fayllar bitta to'plamda qoladi."""
        self._turns[user_id] = self._turns.get(user_id, 0) + 1

    def _fresh(self, user_id: int) -> list[RawFile]:
        now = time.monotonic()
        fresh = [f for f in self._items.get(user_id, []) if now - f.stamp <= self.ttl]
        if fresh:
            self._items[user_id] = fresh
        else:
            self._items.pop(user_id, None)
        return list(fresh)


class ToolError(Exception):
    """Claude'ga tool_result (is_error) sifatida qaytadi — u foydalanuvchiga tushuntiradi."""


@dataclass
class ToolContext:
    bot: Bot
    chat_id: int
    reply_to: int
    uid: int
    files: list[RawFile]
    model: str
    claude: Any = None  # ClaudeService — hujjat tarjimasi uchun
    live: Any = None  # LiveMessage — holat qatori uchun
    log_usage: Any = None  # async (model, numbers, cost) — tarjima xarajatini yozish
    on_file: Any = None  # async (name, data, telegram_file_id) — yaratilgan faylni galereyaga yozish
    sent: list[str] = field(default_factory=list)
    cost: float = 0.0

    async def status(self, text: str) -> None:
        if self.live is not None:
            await self.live.on_tool(text)

    def _reply(self) -> ReplyParameters:
        return ReplyParameters(message_id=self.reply_to, allow_sending_without_reply=True)

    async def _remember(self, name: str, data: bytes, file_id: str | None) -> None:
        if self.on_file is None:
            return
        try:
            await self.on_file(name, data, file_id)
        except Exception:  # noqa: BLE001 — galereya xatosi javobga xalaqit bermasin
            log.exception("Faylni galereyaga yozib bo'lmadi: %s", name)

    async def send_document(self, data: bytes, name: str, caption: str | None = None) -> None:
        message = await self.bot.send_document(
            chat_id=self.chat_id, document=BufferedInputFile(data, filename=name),
            caption=caption or f"📎 {name}", reply_parameters=self._reply(),
        )
        self.sent.append(name)
        await self.status(f"📎 {name} yuborildi ✅")
        document = getattr(message, "document", None)
        await self._remember(name, data, document.file_id if document is not None else None)

    async def send_photo(self, data: bytes, name: str, caption: str | None = None) -> None:
        message = await self.bot.send_photo(
            chat_id=self.chat_id, photo=BufferedInputFile(data, filename=name), caption=caption,
            reply_parameters=self._reply(),
        )
        self.sent.append(name)
        await self.status(f"📊 {name} yuborildi ✅")
        photos = getattr(message, "photo", None) or []
        await self._remember(name, data, photos[-1].file_id if photos else None)


# ------------------------------------------------------------------ yordamchilar
def _size(data: bytes) -> str:
    return f"{len(data) / 1024:.0f} KB" if len(data) < 1024 * 1024 else f"{len(data) / 1024 / 1024:.1f} MB"


def _match(files_: list[RawFile], name: str, exclude: list[RawFile] | tuple = ()) -> RawFile | None:
    """Avval to'liq nom (shartnoma.docx ≠ shartnoma.pdf), keyin kengaytmasiz; allaqachon tanlangani oxirida."""
    wanted = name.strip().lower()
    stem = wanted.rsplit(".", 1)[0]
    ordered = list(reversed(files_))  # eng yangisi birinchi
    fresh = [f for f in ordered if not any(f is e for e in exclude)]
    for same in (lambda low: low == wanted, lambda low: low.rsplit(".", 1)[0] == stem):
        for pool in (fresh, ordered):
            for item in pool:
                if same(item.name.lower()):
                    return item
    return None


def pick(files_: list[RawFile], names: list[str] | None, kinds: tuple[str, ...], *, at_least: int = 1) -> list[RawFile]:
    """Nomi bo'yicha (tartib saqlanadi) yoki, nom berilmasa, oxirgi to'plamdagi (yetmasa — barcha) mos fayllar."""
    available = [f for f in files_ if f.kind in kinds]
    if not names and available:
        # Oldingi savolga yuborilgan eski rasm/PDF yangi so'rovga aralashib ketmasin
        latest = [f for f in available if f.batch == available[-1].batch]
        if len(latest) >= at_least:
            available = latest
    if names:
        chosen: list[RawFile] = []
        for name in names:
            found = _match(files_, str(name), chosen)
            if found is None:
                have = ", ".join(f.name for f in files_) or "hech narsa"
                raise ToolError(f"«{name}» fayli topilmadi. Shu suhbatda mavjud fayllar: {have}.")
            if found.kind not in kinds:
                raise ToolError(f"«{found.name}» bu amal uchun mos emas (kerak: {', '.join(kinds)}).")
            chosen.append(found)
        available = chosen
    if len(available) < at_least:
        raise ToolError(
            "Kerakli fayl topilmadi — foydalanuvchi faylni shu suhbatda qaytadan yuborsin "
            f"(kerak: {', '.join(kinds)}, kamida {at_least} ta)."
        )
    return available


def _left_out(files_: list[RawFile], names: Any, used: list[RawFile], kind: str) -> str:
    """Nom berilmaganda olinmagan eski fayllar — Claude kerak bo'lsa nomi bilan qayta chaqiradi."""
    rest = [] if names else [f.name for f in files_ if f.kind == kind and not any(f is u for u in used)]
    if not rest:
        return ""
    return f" Oldinroq yuborilgan {len(rest)} ta fayl qo'shilmadi (kerak bo'lsa files'da bering): {', '.join(rest[:10])}."


def text_of(item: RawFile) -> str:
    if item.kind == "pdf":
        return pdfs.text(item.data)
    if item.kind == "docx":
        return media.docx_text(item.data)
    if item.kind == "xlsx":
        return media.xlsx_text(item.data)
    if item.kind == "text":
        return media.decode_text(item.data)
    raise ToolError(f"«{item.name}» matnli hujjat emas.")


# ------------------------------------------------------------------ vositalar
async def create_document(tctx: ToolContext, args: dict) -> str:
    fmt = str(args.get("format") or "").lower().strip(".")
    if fmt not in FORMATS:
        raise ToolError(f"format quyidagilardan biri bo'lsin: {', '.join(FORMATS)}")
    name = files.safe_name(str(args.get("filename") or args.get("title") or "hujjat"), fmt)
    title = (args.get("title") or None) and str(args["title"])
    content = args.get("content")
    content = str(content) if content is not None else ""
    await tctx.status(f"📎 {FORMATS[fmt]} fayl tayyorlanmoqda…")
    if fmt in ("docx", "pdf", "md", "txt"):
        if not content.strip():
            raise ToolError("content (hujjat matni) bo'sh — Markdown matn ber.")
        if fmt == "docx":
            data = await asyncio.to_thread(files.build_docx, content, title)
        elif fmt == "pdf":
            data = await asyncio.to_thread(files.build_pdf, content, title)
        else:
            data = files.build_text((f"# {title}\n\n" if title and fmt == "md" else "") + content)
    elif fmt in ("xlsx", "csv"):
        sheets = args.get("sheets") or files.tables_from_markdown(content)
        if not sheets:
            raise ToolError("sheets berilmadi (yoki content'da Markdown jadval yo'q).")
        data = await asyncio.to_thread(files.build_xlsx if fmt == "xlsx" else files.build_csv, sheets)
    else:
        slides = args.get("slides") or files.slides_from_markdown(content)
        if not slides:
            raise ToolError("slides berilmadi (yoki content'da '# sarlavha' + '- band' tuzilmasi yo'q).")
        data = await asyncio.to_thread(files.build_pptx, slides, title)
    await tctx.send_document(data, name)
    return f"Fayl yuborildi: {name} ({_size(data)}). Endi foydalanuvchiga qisqa izoh yoz."


async def create_chart(tctx: ToolContext, args: dict) -> str:
    await tctx.status("📊 Grafik chizilmoqda…")
    name = files.safe_name(str(args.get("filename") or args.get("title") or "grafik"), "png")
    data = await asyncio.to_thread(files.build_chart, dict(args))
    await tctx.send_photo(data, name, str(args.get("title") or "📊 Grafik")[:1000])
    return f"Grafik yuborildi: {name}."


async def pdf_tool(tctx: ToolContext, args: dict) -> str:
    action = str(args.get("action") or "").lower()
    names = args.get("files") or None
    pages = args.get("pages")
    custom = args.get("filename")
    await tctx.status("📎 PDF ustida ishlanmoqda…")
    if action == "images_to_pdf":
        images = pick(tctx.files, names, ("image",))
        data = await asyncio.to_thread(pdfs.images_to_pdf, [f.data for f in images])
        name = files.safe_name(str(custom or "skaner"), "pdf")
        await tctx.send_document(data, name)
        return f"{len(images)} ta rasmdan PDF yuborildi: {name} ({_size(data)}).{_left_out(tctx.files, names, images, 'image')}"
    if action == "merge":
        items = pick(tctx.files, names, ("pdf",), at_least=2)
        data = await asyncio.to_thread(pdfs.merge, [f.data for f in items])
        name = files.safe_name(str(custom or "birlashtirilgan"), "pdf")
        await tctx.send_document(data, name)
        return (
            f"{len(items)} ta PDF birlashtirildi: {name} ({_size(data)}, {pdfs.page_count(data)} sahifa)."
            f"{_left_out(tctx.files, names, items, 'pdf')}"
        )
    items = pick(tctx.files, names, ("pdf",))
    source = items[-1]
    stem = source.name.rsplit(".", 1)[0]
    if action == "info":
        return await asyncio.to_thread(pdfs.info, source.data, source.name)
    if action == "extract":
        data, chosen = await asyncio.to_thread(pdfs.extract, source.data, pages)
        name = files.safe_name(str(custom or f"{stem}_sahifalar"), "pdf")
        await tctx.send_document(data, name)
        return f"{len(chosen)} ta sahifa olindi ({', '.join(str(p + 1) for p in chosen[:20])}): {name}."
    if action == "rotate":
        angle = int(args.get("angle") or 90)
        data = await asyncio.to_thread(pdfs.rotate, source.data, pages, angle)
        name = files.safe_name(str(custom or f"{stem}_burilgan"), "pdf")
        await tctx.send_document(data, name)
        return f"Sahifalar {angle}° burildi: {name}."
    if action == "split":
        parts = await asyncio.to_thread(pdfs.split, source.data)
        if len(parts) <= 3:
            for number, part in enumerate(parts, 1):
                await tctx.send_document(part, files.safe_name(f"{stem}_{number}", "pdf"))
            return f"{len(parts)} ta sahifa alohida PDF qilib yuborildi."
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for number, part in enumerate(parts, 1):
                archive.writestr(f"{stem}_{number:03d}.pdf", part)
        name = files.safe_name(str(custom or f"{stem}_sahifalar"), "zip")
        await tctx.send_document(buffer.getvalue(), name)
        return f"{len(parts)} ta sahifa alohida PDF qilib zip'da yuborildi: {name}."
    raise ToolError("action quyidagilardan biri bo'lsin: merge, extract, split, rotate, images_to_pdf, info")


async def translate_document(tctx: ToolContext, args: dict) -> str:
    if tctx.claude is None:
        raise ToolError("Tarjima xizmati ulanmagan.")
    target = str(args.get("target_language") or "").strip()
    if not target:
        raise ToolError("target_language ko'rsatilmadi.")
    glossary = args.get("glossary")
    source = pick(tctx.files, [args["file"]] if args.get("file") else None, ("docx",))[-1]
    await tctx.status("🌐 Hujjat tarjima qilinmoqda…")

    async def translate(batch: list[str]) -> list[str]:
        result, numbers, cost = await tctx.claude.translate_items(tctx.model, target, glossary, batch)
        tctx.cost += cost
        if tctx.log_usage is not None:
            await tctx.log_usage(tctx.model, numbers, cost)
        return result

    async def progress(done: int, total: int) -> None:
        await tctx.status(f"🌐 Hujjat tarjima qilinmoqda… {done}/{total}")

    try:
        data, count = await doctrans.translate_docx(source.data, translate, progress)
    except doctrans.TranslateError as exc:
        raise ToolError(str(exc)) from exc
    stem = source.name.rsplit(".", 1)[0]
    name = files.safe_name(f"{stem}_tarjima", "docx")
    await tctx.send_document(data, name)
    return f"{count} ta abzats {target} tiliga tarjima qilindi, fayl yuborildi: {name} ({_size(data)})."


async def compare_documents(tctx: ToolContext, args: dict) -> str:
    name_a, name_b = (str(args.get(k) or "").strip() for k in ("file_a", "file_b"))
    if bool(name_a) != bool(name_b):
        # Bitta nom berildi — ikkinchisi oxirgi yuborilgan boshqa matnli hujjat
        named = pick(tctx.files, [name_a or name_b], TEXT_KINDS)[0]
        others = [f for f in tctx.files if f.kind in TEXT_KINDS and f is not named]
        if not others:
            raise ToolError(f"«{named.name}» bilan solishtirish uchun ikkinchi hujjat yo'q — foydalanuvchi uni yuborsin.")
        first, second = (named, others[-1]) if name_a else (others[-1], named)
    else:
        items = pick(tctx.files, [name_a, name_b] if name_a else None, TEXT_KINDS, at_least=2)
        first, second = items[-2], items[-1]
        order = [id(f) for f in tctx.files]
        if name_a and name_a.lower() == name_b.lower() and order.index(id(first)) > order.index(id(second)):
            first, second = second, first  # bir xil nomli ikki nusxa: A — eskisi, B — yangisi
    await tctx.status("🔍 Hujjatlar solishtirilmoqda…")
    text_a, text_b = await asyncio.to_thread(text_of, first), await asyncio.to_thread(text_of, second)
    lines_a = [line.strip() for line in text_a.splitlines() if line.strip()]
    lines_b = [line.strip() for line in text_b.splitlines() if line.strip()]
    diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=first.name, tofile=second.name, lineterm="", n=1))
    if not diff:
        return f"«{first.name}» va «{second.name}» matni bir xil (farq topilmadi)."
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    ratio = difflib.SequenceMatcher(None, lines_a, lines_b).ratio()
    body = "\n".join(diff)
    if len(body) > DIFF_LIMIT:
        body = body[:DIFF_LIMIT] + "\n[… farqlar juda ko'p, qolgani qisqartirildi]"
    return (
        f"Solishtirish: A = «{first.name}» ({len(lines_a)} qator), B = «{second.name}» ({len(lines_b)} qator). "
        f"O'xshashlik: {ratio:.0%}. Olib tashlangan qatorlar: {removed}, qo'shilgan: {added}.\n\n{body}"
    )


HANDLERS = {
    "create_document": create_document,
    "create_chart": create_chart,
    "pdf_tool": pdf_tool,
    "translate_document": translate_document,
    "compare_documents": compare_documents,
}


async def run(tctx: ToolContext, name: str, args: dict) -> str:
    handler = HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Noma'lum vosita: {name}")
    try:
        return await handler(tctx, args)
    except (ToolError, files.FileError, pdfs.PdfError, media.MediaError) as exc:
        raise ToolError(str(exc)) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise ToolError(f"Parametrlar noto'g'ri: {exc}") from exc

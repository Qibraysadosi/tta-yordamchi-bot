"""PDF amallari (pypdf + Pillow): birlashtirish, sahifa olish, bo'lish, burish, rasm → PDF, matn olish."""
from __future__ import annotations

import io
import re

MAX_SPLIT_PAGES = 100
TEXT_LIMIT = 200_000


class PdfError(Exception):
    """Foydalanuvchiga (Claude orqali) tushuntiriladigan xato."""


def _reader(data: bytes):
    from pypdf import PdfReader

    if b"%PDF" not in data[:1024]:
        raise PdfError("Fayl PDF emas.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise PdfError("PDF parol bilan himoyalangan — avval parolini oching.") from exc
        len(reader.pages)
    except PdfError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"PDF o'qilmadi: {exc}") from exc
    return reader


def _write(writer) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def parse_pages(spec: str | None, count: int) -> list[int]:
    """'1-3,5,8-10' → 0 dan boshlanadigan sahifa indekslari (tartib saqlanadi, takrorlar olib tashlanadi)."""
    if not spec or not str(spec).strip():
        return list(range(count))
    pages: list[int] = []
    for part in re.split(r"[,\s;]+", str(spec).strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
        elif part.isdigit():
            start = end = int(part)
        else:
            raise PdfError(f"Sahifa ko'rsatkichi tushunarsiz: «{part}». Masalan: 1-3,5")
        if start < 1 or end > count or start > end:
            raise PdfError(f"Sahifa oralig'i {start}-{end} noto'g'ri: hujjatda {count} ta sahifa bor.")
        for n in range(start, end + 1):
            if n - 1 not in pages:
                pages.append(n - 1)
    if not pages:
        raise PdfError("Sahifalar ko'rsatilmagan.")
    return pages


def page_count(data: bytes) -> int:
    return len(_reader(data).pages)


def merge(datas: list[bytes]) -> bytes:
    from pypdf import PdfWriter

    if len(datas) < 2:
        raise PdfError("Birlashtirish uchun kamida 2 ta PDF kerak.")
    writer = PdfWriter()
    for data in datas:
        writer.append(_reader(data))
    return _write(writer)


def extract(data: bytes, spec: str | None) -> tuple[bytes, list[int]]:
    from pypdf import PdfWriter

    reader = _reader(data)
    pages = parse_pages(spec, len(reader.pages))
    writer = PdfWriter()
    for index in pages:
        writer.add_page(reader.pages[index])
    return _write(writer), pages


def split(data: bytes) -> list[bytes]:
    from pypdf import PdfWriter

    reader = _reader(data)
    if len(reader.pages) > MAX_SPLIT_PAGES:
        raise PdfError(f"Hujjatda {len(reader.pages)} sahifa — bo'lish {MAX_SPLIT_PAGES} sahifagacha ishlaydi.")
    parts = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        parts.append(_write(writer))
    return parts


def rotate(data: bytes, spec: str | None, angle: int) -> bytes:
    from pypdf import PdfWriter

    if angle % 90 != 0:
        raise PdfError("Burish burchagi 90, 180 yoki 270 bo'lsin.")
    reader = _reader(data)
    pages = set(parse_pages(spec, len(reader.pages)))
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index in pages:
            page.rotate(angle % 360)
        writer.add_page(page)
    return _write(writer)


def images_to_pdf(images: list[bytes]) -> bytes:
    from PIL import Image, ImageOps

    if not images:
        raise PdfError("PDF qilish uchun rasm yo'q.")
    frames = []
    for data in images:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:  # noqa: BLE001
            raise PdfError("Rasmlardan biri o'qilmadi.") from exc
        img = ImageOps.exif_transpose(img) or img
        if img.mode in ("I", "I;16", "I;16B", "I;16L"):  # 16 bitli kulrang skaner — 8 bitga o'tkaziladi (oq bo'lmasin)
            img = img.convert("I").point(lambda v: v * (1 / 256)).convert("L")
        if img.has_transparency_data:  # shaffof fon qora emas, oq bo'lsin
            rgba = img.convert("RGBA")
            img = Image.new("RGB", rgba.size, "white")
            img.paste(rgba, mask=rgba.getchannel("A"))
        frames.append(img.convert("RGB"))
    buffer = io.BytesIO()
    frames[0].save(buffer, format="PDF", save_all=True, append_images=frames[1:], resolution=150.0)
    return buffer.getvalue()


def info(data: bytes, name: str) -> str:
    reader = _reader(data)
    count = len(reader.pages)
    lines = [f"{name}: {count} ta sahifa, {len(data) / 1024:.0f} KB"]
    try:
        box = reader.pages[0].mediabox
        lines.append(f"Sahifa o'lchami: {float(box.width) / 72 * 25.4:.0f} × {float(box.height) / 72 * 25.4:.0f} mm")
    except Exception:  # noqa: BLE001
        pass
    meta = reader.metadata
    if meta:
        for key, label in (("/Title", "Sarlavha"), ("/Author", "Muallif"), ("/Creator", "Dastur"), ("/CreationDate", "Yaratilgan")):
            value = meta.get(key)
            if value:
                lines.append(f"{label}: {str(value)[:120]}")
    sample = ""
    try:
        sample = (reader.pages[0].extract_text() or "").strip()
    except Exception:  # noqa: BLE001
        pass
    lines.append("Matn qatlami: " + ("bor" if sample else "yo'q (skaner bo'lishi mumkin)"))
    return "\n".join(lines)


def text(data: bytes, limit: int = TEXT_LIMIT) -> str:
    reader = _reader(data)
    parts: list[str] = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        try:
            chunk = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            chunk = ""
        if chunk:
            parts.append(f"[{number}-sahifa]\n{chunk}")
            total += len(chunk)
        if total > limit:
            parts.append("[… matn juda uzun, qolgani olinmadi]")
            break
    return "\n\n".join(parts)

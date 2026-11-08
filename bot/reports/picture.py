"""Kunlik hisobot rasmi (v1.12): operatorlar jadvali PNG — to'liq ismlar, rangli status ustunlari, natija
chizig'i va JAMI; rahbarlar va ro'yxatda yo'qlar alohida kartochkada.

AI emas — Pillow bilan chiziladi, raqamlar aniq. Emoji — bot/assets/emoji (Twemoji, CC-BY 4.0), shrift — DejaVu
Sans. Ikki baravar katta chizilib, oxirida kichraytiriladi (chiziq va harflar silliq chiqadi)."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..prompt import WEEKDAYS
from .stats import OTHER, Overview, Row

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_REGULAR = ASSETS / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = ASSETS / "fonts" / "DejaVuSans-Bold.ttf"
EMOJI_DIR = ASSETS / "emoji"

S = 2  # chizish masshtabi (supersampling)
WIDTH = 1080
PAD = 32  # rasm chetidan
INNER = 30  # kartochka ichki chekkasi
COL_W, TOTAL_W, NAME_MIN = 66, 96, 300
AVATAR = 46
ROW_H = 92
MAX_PEOPLE = 60  # bundan ko'p qator — rasm juda uzun bo'ladi, matn yuboriladi
UNLISTED_SHOWN = 6

BG = (237, 241, 246)
CARD = (255, 255, 255)
TEXT = (15, 23, 42)
MUTED = (100, 116, 139)
FAINT = (203, 213, 225)
SEP = (232, 236, 242)
TOTAL_BG = (246, 248, 251)
GREEN = (5, 150, 105)
TRACK = (229, 234, 240)
HERO_LEFT, HERO_RIGHT = (4, 120, 87), (13, 148, 136)
MINT = (209, 250, 229)
WHITE = (255, 255, 255)
ADMIN_BG, ADMIN_FG = (224, 231, 255), (67, 56, 202)
LEAD_BG, LEAD_FG = (237, 233, 254), (109, 40, 217)
AVATARS = ((79, 70, 229), (8, 145, 178), (5, 150, 105), (217, 119, 6), (225, 29, 72), (124, 58, 237),
           (2, 132, 199), (192, 38, 211))  # Mini App'dagi avatar ranglari bilan bir xil


def p(value: float) -> int:
    return int(round(value * S))


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), p(size))


def emoji_key(emoji: str) -> str:
    return "-".join(f"{ord(ch):x}" for ch in emoji if ch != "️")


@lru_cache(maxsize=128)
def emoji_image(emoji: str, size: int) -> Image.Image | None:
    path = EMOJI_DIR / f"{emoji_key(emoji)}.png"
    if not path.is_file():
        return None
    with Image.open(path) as source:
        return source.convert("RGBA").resize((p(size), p(size)), Image.Resampling.LANCZOS)


def has_emoji(emoji: str) -> bool:
    return (EMOJI_DIR / f"{emoji_key(emoji)}.png").is_file()


def clean(text: str) -> str:
    """Shriftda yo'q belgilar (emoji, bayroq, ko'rinmas belgilar) olib tashlanadi — rasmda «□» chiqmasin."""
    kept = []
    for ch in text or "":
        category = unicodedata.category(ch)
        if category in ("So", "Cs", "Co", "Cn", "Cf") or ch in "︎️":
            continue
        kept.append(" " if category.startswith("Z") else ch)
    return " ".join("".join(kept).split())


def avatar_color(name: str) -> tuple[int, int, int]:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return AVATARS[h % len(AVATARS)]


class Canvas:
    """RGBA kanvas: 1x birliklarda koordinatalar (ichkarida S ga ko'paytiriladi)."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.image = Image.new("RGBA", (p(width), p(height)), BG + (255,))
        self.draw = ImageDraw.Draw(self.image)

    def text(self, xy: tuple[float, float], text: str, size: int, fill, *, bold: bool = False, anchor: str = "ls") -> None:
        self.draw.text((p(xy[0]), p(xy[1])), text, font=font(size, bold), fill=fill, anchor=anchor)

    def width_of(self, text: str, size: int, bold: bool = False) -> float:
        return self.draw.textlength(text, font=font(size, bold)) / S

    def fit(self, text: str, size: int, limit: float, bold: bool = False) -> str:
        if self.width_of(text, size, bold) <= limit:
            return text
        while text and self.width_of(text + "…", size, bold) > limit:
            text = text[:-1]
        return text.rstrip() + "…"

    def box(self, x0: float, y0: float, x1: float, y1: float, radius: float, fill) -> None:
        self.draw.rounded_rectangle((p(x0), p(y0), p(x1), p(y1)), radius=p(radius), fill=fill)

    def emoji(self, emoji: str, x: float, y: float, size: int) -> bool:
        """Emoji (chap yuqori burchagi x, y); rasm topilmasa — False."""
        picture = emoji_image(emoji, size)
        if picture is None:
            return False
        self.image.alpha_composite(picture, dest=(p(x), p(y)))
        return True

    def overlay_box(self, x0: float, y0: float, x1: float, y1: float, radius: float, rgba) -> None:
        """Yarim shaffof to'rtburchak (gradient ustidagi «shisha» chip)."""
        layer = Image.new("RGBA", (p(x1 - x0), p(y1 - y0)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((0, 0, layer.width - 1, layer.height - 1), radius=p(radius), fill=rgba)
        self.image.alpha_composite(layer, dest=(p(x0), p(y0)))

    def gradient_box(self, x0: float, y0: float, x1: float, y1: float, radius: float) -> None:
        size = (p(x1 - x0), p(y1 - y0))
        mask = Image.linear_gradient("L").rotate(90, expand=True).resize(size)  # chapda 0, o'ngda 255
        left, right = Image.new("RGBA", size, HERO_LEFT + (255,)), Image.new("RGBA", size, HERO_RIGHT + (255,))
        fill = Image.composite(right, left, mask)  # chap — to'q yashil, o'ng — firuza
        shape = Image.new("L", size, 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=p(radius), fill=255)
        self.image.paste(fill, (p(x0), p(y0)), shape)

    def finish(self, height: float) -> bytes:
        cropped = self.image.crop((0, 0, p(self.width), p(height))).convert("RGB")
        final = cropped.resize((self.width, int(round(height))), Image.Resampling.LANCZOS)
        out = BytesIO()
        final.save(out, format="PNG", optimize=True)
        return out.getvalue()


def _period(ov: Overview, interim_at: datetime | None, late: bool) -> str:
    text = f"{ov.start:%d.%m %H:%M} → {ov.end:%d.%m %H:%M}"
    if interim_at is not None:
        text += f" · oraliq holat {interim_at:%H:%M} gacha"
    if late:
        text += " · kechikib yuborildi"
    return text


def _share(part: int, whole: int) -> int:
    return round(part * 100 / whole) if whole else 0


class Layout:
    def __init__(self, ov: Overview) -> None:
        self.ov = ov
        self.cols = ov.columns()
        self.labels: dict[str, str] = {st.emoji: st.label for st in ov.statuses}
        self.labels[OTHER] = "aniqlanmagan"
        self.positive = next((st for st in ov.statuses if st.positive), None)
        n = len(self.cols)
        need = PAD * 2 + INNER * 2 + AVATAR + 16 + NAME_MIN + 12 + TOTAL_W + n * COL_W
        self.width = max(WIDTH, need)
        self.card_x0, self.card_x1 = PAD, self.width - PAD
        right = self.card_x1 - INNER
        self.col_x = [right - (n - i) * COL_W + COL_W / 2 for i in range(n)]  # ustun markazlari
        self.total_x = right - n * COL_W - 14  # «Jami» o'ng cheti
        self.name_x = self.card_x0 + INNER + AVATAR + 16
        self.name_right = self.total_x - TOTAL_W + 8


def _hero(c: Canvas, lay: Layout, y: float, title: str, day_text: str, period: str) -> float:
    ov = lay.ov
    everyone = ov.rows + ov.extras
    calls = sum(r.total for r in everyone)
    counts: dict[str, int] = {}
    for r in everyone:
        for key, value in r.counts.items():
            counts[key] = counts.get(key, 0) + value
    x0, x1 = lay.card_x0, lay.card_x1
    left = x0 + 34
    # balandlik: sarlavha + davr + katta raqam + izoh + chiplar + ko'rsatkichlar
    chips = [c_ for c_ in lay.cols if counts.get(c_)]
    chip_rows = _chip_rows(c, lay, chips, counts, x1 - 34 - left)
    extra_line = bool(ov.extras)
    height = 34 + 36 + 40 + 104 + (34 if extra_line else 0) + len(chip_rows) * 54 + 16 + 100 + 30
    c.gradient_box(x0, y, x1, y + height, 30)
    top = y + 34
    c.emoji("📊", left, top - 2, 34)
    c.text((left + 46, top + 28), title, 30, WHITE, bold=True)
    c.text((x1 - 34, top + 28), day_text, 25, MINT, anchor="rs")
    top += 36
    c.text((left, top + 30), period, 23, MINT)
    top += 40 + 84
    c.text((left, top), str(calls), 88, WHITE, bold=True)
    c.text((left + c.width_of(str(calls), 88, True) + 16, top), "ta lidga qo'ng'iroq", 30, WHITE)
    top += 20
    if extra_line:
        parts = [f"operatorlar {ov.totals.total}"]
        if ov.leaders:
            parts.append(f"rahbarlar {sum(r.total for r in ov.leaders)}")
        for group, members in ov.departments:
            parts.append(f"{clean(group).lower()} {sum(r.total for r in members)}")
        if ov.unlisted:
            parts.append(f"ro'yxatda yo'q {sum(r.total for r in ov.unlisted)}")
        c.text((left, top + 22), "shundan: " + " · ".join(parts), 22, MINT)
        top += 34
    top += 14
    for row in chip_rows:
        x = left
        for key, width in row:
            c.overlay_box(x, top, x + width, top + 44, 22, (255, 255, 255, 46))
            cx = x + 14
            if c.emoji(key, cx, top + 7, 30):
                cx += 38
            else:
                cx += 4
            c.text((cx, top + 31), str(counts[key]), 24, WHITE, bold=True)
            cx += c.width_of(str(counts[key]), 24, True) + 8
            c.text((cx, top + 31), clean(lay.labels.get(key, "")), 22, WHITE)
            x += width + 10
        top += 54
    top += 16
    tiles = [("Yangi lidlar", str(ov.new_leads)), ("Belgilanmagan", str(ov.unmarked))]
    if lay.positive is not None:
        positive = sum(ov.positive(r) for r in everyone)
        tiles.append((f"{clean(lay.positive.label)} ulushi", f"{_share(positive, calls)}%"))
    tile_w = (x1 - 34 - left - 12 * (len(tiles) - 1)) / len(tiles)
    for index, (label, value) in enumerate(tiles):
        tx = left + index * (tile_w + 12)
        c.overlay_box(tx, top, tx + tile_w, top + 88, 18, (255, 255, 255, 34))
        c.text((tx + 18, top + 32), c.fit(label, 21, tile_w - 30), 21, MINT)
        c.text((tx + 18, top + 72), value, 34, WHITE, bold=True)
    return y + height


def _chip_rows(c: Canvas, lay: Layout, keys: list[str], counts: dict[str, int], limit: float) -> list[list[tuple[str, float]]]:
    rows: list[list[tuple[str, float]]] = [[]]
    used = 0.0
    for key in keys:
        width = 14 + (38 if has_emoji(key) else 4) + c.width_of(str(counts[key]), 24, True) + 8
        width += c.width_of(clean(lay.labels.get(key, "")), 22) + 16
        if rows[-1] and used + width > limit:
            rows.append([])
            used = 0.0
        rows[-1].append((key, width))
        used += width + 10
    return [row for row in rows if row]


def _badge(c: Canvas, x: float, y_mid: float, text: str, colors: tuple) -> float:
    width = c.width_of(text, 18, True) + 20
    c.box(x, y_mid - 15, x + width, y_mid + 15, 15, colors[0])
    c.text((x + width / 2, y_mid + 1), text, 18, colors[1], bold=True, anchor="mm")
    return width


HEADER_H = 96  # sarlavha + ustunlar qatori
SECTION_H = 52  # «Rahbarlar», «Ro'yxatda yo'q» kichik sarlavhasi


def _header(c: Canvas, lay: Layout, y: float, title: str) -> float:
    c.text((lay.card_x0 + INNER, y + 42), title, 27, TEXT, bold=True)
    y += 60
    c.text((lay.card_x0 + INNER, y + 22), "Xodim", 20, MUTED)
    c.text((lay.total_x, y + 22), "Jami", 20, MUTED, anchor="rs")
    for key, x in zip(lay.cols, lay.col_x):
        if key == OTHER:  # oq «❔» oq fonda ko'rinmaydi — kulrang doira
            c.draw.ellipse((p(x - 14), p(y - 1), p(x + 14), p(y + 27)), fill=TRACK)
            c.text((x, y + 13), "?", 19, MUTED, bold=True, anchor="mm")
        elif not c.emoji(key, x - 15, y - 2, 30):
            c.text((x, y + 22), clean(lay.labels.get(key, ""))[:4] or "?", 18, MUTED, anchor="ms")
    y += 36
    c.draw.line((p(lay.card_x0 + INNER), p(y), p(lay.card_x1 - INNER), p(y)), fill=SEP, width=p(2))
    return y


def _section(c: Canvas, lay: Layout, y: float, icon: str, title: str, note: str) -> float:
    x = lay.card_x0 + INNER
    if c.emoji(icon, x, y + 13, 28):
        x += 38
    c.text((x, y + 36), title, 23, TEXT, bold=True)
    c.text((x + c.width_of(title, 23, True) + 10, y + 36), c.fit(note, 20, lay.card_x1 - INNER - x - c.width_of(title, 23, True) - 10), 20, MUTED)
    y += SECTION_H
    c.draw.line((p(lay.card_x0 + INNER), p(y), p(lay.card_x1 - INNER), p(y)), fill=SEP, width=p(2))
    return y


def _person(c: Canvas, lay: Layout, y: float, row: Row, *, badge: bool = True, handle: bool = False) -> float:
    ov = lay.ov
    mid = y + ROW_H / 2
    name = clean(row.name) or (f"@{row.username}" if row.username else "?")
    ax = lay.card_x0 + INNER
    c.draw.ellipse((p(ax), p(mid - AVATAR / 2), p(ax + AVATAR), p(mid + AVATAR / 2)), fill=avatar_color(name))
    c.text((ax + AVATAR / 2, mid + 1), name.lstrip("@")[:1].upper() or "?", 22, WHITE, bold=True, anchor="mm")
    tag = ("admin", (ADMIN_BG, ADMIN_FG)) if row.admin and badge else ("rahbar", (LEAD_BG, LEAD_FG)) if row.leader and badge else None
    room = lay.name_right - lay.name_x - (c.width_of(tag[0], 18, True) + 32 if tag else 0)
    shown = c.fit(name, 29, room, bold=True)
    c.text((lay.name_x, mid - 6), shown, 29, TEXT, bold=True)
    if tag:
        _badge(c, lay.name_x + c.width_of(shown, 29, True) + 12, mid - 16, tag[0], tag[1])
    # ikkinchi qator: asosiy natija ulushi (yoki username / «natija yo'q»)
    sub_y = mid + 26
    if handle and row.username and name != f"@{row.username}":
        c.text((lay.name_x, sub_y + 4), c.fit(f"@{row.username}", 20, lay.name_right - lay.name_x), 20, MUTED)
    elif not row.total:
        c.text((lay.name_x, sub_y + 4), "natija yo'q", 20, MUTED)
    elif lay.positive is not None:
        share = _share(ov.positive(row), row.total)
        label = f"{share}% {clean(lay.positive.label)}"
        bar_x1 = lay.name_right - c.width_of(label, 19) - 14
        c.box(lay.name_x, sub_y - 5, bar_x1, sub_y + 5, 5, TRACK)
        if share:
            c.box(lay.name_x, sub_y - 5, lay.name_x + max(10, (bar_x1 - lay.name_x) * share / 100), sub_y + 5, 5, GREEN)
        c.text((lay.name_right, sub_y + 7), label, 19, MUTED, anchor="rs")
    c.text((lay.total_x, mid + 12), str(row.total), 32, TEXT, bold=True, anchor="rs")
    positive = {st.emoji for st in ov.statuses if st.positive}
    for key, x in zip(lay.cols, lay.col_x):
        value = row.counts.get(key, 0)
        color = FAINT if not value else GREEN if key in positive else TEXT
        c.text((x, mid + 11), str(value), 27, color, bold=bool(value) and key in positive, anchor="ms")
    return y + ROW_H


def _totals(c: Canvas, lay: Layout, y: float) -> float:
    total = lay.ov.totals
    c.box(lay.card_x0 + 12, y + 8, lay.card_x1 - 12, y + 72, 16, TOTAL_BG)
    c.text((lay.card_x0 + INNER, y + 51), "JAMI", 26, TEXT, bold=True)
    c.text((lay.total_x, y + 52), str(total.total), 32, TEXT, bold=True, anchor="rs")
    positive = {st.emoji for st in lay.ov.statuses if st.positive}
    for key, x in zip(lay.cols, lay.col_x):
        value = total.counts.get(key, 0)
        c.text((x, y + 51), str(value), 27, (GREEN if key in positive and value else TEXT if value else FAINT),
               bold=True, anchor="ms")
    return y + 80


def _rows(c: Canvas, lay: Layout, y: float, rows: list[Row], **kwargs: Any) -> float:
    for index, row in enumerate(rows):
        if index:
            c.draw.line((p(lay.name_x), p(y), p(lay.card_x1 - INNER), p(y)), fill=SEP, width=p(2))
        y = _person(c, lay, y, row, **kwargs)
    return y


def render_card(ov: Overview, *, interim_at: datetime | None = None, late: bool = False) -> bytes | None:
    """Hisobot rasmi (PNG). Odam juda ko'p bo'lsa — None (matn yuboriladi)."""
    people = len(ov.rows) + len(ov.leaders) + sum(len(m) for _, m in ov.departments) + min(len(ov.unlisted), UNLISTED_SHOWN)
    if people > MAX_PEOPLE:
        return None
    lay = Layout(ov)
    shown = ov.rows if (ov.rows and (ov.totals.total or ov.has_operator_list)) else []
    unlisted = ov.unlisted[:UNLISTED_SHOWN]
    more = len(ov.unlisted) - len(unlisted)
    sections = [("👔", "Rahbarlar", "lidni o'zi belgilagan", ov.leaders, {"badge": False})] if ov.leaders else []
    sections += [("🧑‍💼", clean(group) or "Bo'lim", "bo'limi", members, {"badge": False})
                 for group, members in ov.departments]
    if unlisted:
        sections.append(("➕", "Ro'yxatda yo'q", "lekin qo'ng'iroq qilgan", unlisted, {"handle": True}))
    body = HEADER_H + (len(shown) * ROW_H + 82 if shown else (0 if sections else 92))
    body += sum(SECTION_H + len(section[3]) * ROW_H for section in sections) + (46 if more > 0 else 0)
    c = Canvas(lay.width, 760 + 60 * len(lay.cols) + body + 200)
    day = ov.end
    title = "KUNLIK HISOBOT" if interim_at is None else "HISOBOT · oraliq"
    y = _hero(c, lay, PAD, title, f"{day:%d.%m.%Y} · {WEEKDAYS[day.weekday()]}", _period(ov, interim_at, late))

    # bitta jadval: operatorlar (+ JAMI), keyin rahbarlar va ro'yxatda yo'qlar — ustunlar bir xil
    top = y + 20
    c.box(lay.card_x0, top, lay.card_x1, top + body + 12, 28, CARD)
    y = _header(c, lay, top, "Operatorlar" if ov.has_operator_list else "Xodimlar")
    if shown:
        y = _rows(c, lay, y, shown)
        y = _totals(c, lay, y)
    elif not sections:
        c.text(((lay.card_x0 + lay.card_x1) / 2, y + 56), "Bu davrda qo'ng'iroq qayd etilmadi", 24, MUTED, anchor="ms")
    for icon, name, note, items, kwargs in sections:
        y = _section(c, lay, y, icon, name, note)
        y = _rows(c, lay, y, items, **kwargs)
    if more > 0:
        c.text((lay.name_x, y + 32), f"… va yana {more} kishi", 21, MUTED)
    y = top + body + 12
    c.text((lay.card_x0 + 6, y + 34), "Bir davrda bitta lid bir odamga bir marta sanaladi (so'nggi natija). "
           "JAMI — operatorlar.", 18, MUTED)
    return c.finish(y + 34 + PAD)

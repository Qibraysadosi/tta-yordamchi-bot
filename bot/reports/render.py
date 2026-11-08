"""Kunlik hisobot matni (Telegram HTML)."""
from __future__ import annotations

import html
from datetime import datetime

from ..prompt import WEEKDAYS
from .stats import Overview, Row

NAME_WIDTH = 9
EXTRAS_SHOWN = 5
COURSES_SHOWN = 4
ADMIN_MARK = "*"  # jadvalda administrator ismi yonida (tor <pre> ustunga sig'adi)
ADMIN_LEGEND = "<i>* — administrator</i>"
MESSAGE_LIMIT = 3800  # Telegram xabari 4096 belgi (UTF-16; emoji — 2) — zaxira bilan


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Uzun hisobotni qatorlar bo'yicha bo'laklaydi; <pre> jadval bo'linsa, har bo'lak o'z tegini oladi."""
    if _units(text) <= limit:
        return [text]
    parts: list[str] = []
    chunk: list[str] = []
    size = 0
    in_pre = False
    for line in text.split("\n"):
        cost = _units(line) + 1
        if chunk and size + cost + 6 > limit:  # 6 — yopuvchi </pre> uchun joy
            if in_pre:
                chunk[-1] += "</pre>"
                line = "<pre>" + line
                cost += 5
            parts.append("\n".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += cost
        if "</pre>" in line:
            in_pre = False
        elif "<pre>" in line:
            in_pre = True
    if chunk:
        parts.append("\n".join(chunk))
    return parts


def _name(row: Row) -> str:
    """Ism ustuni: kamida bitta bo'sh joy qoladi (uzun ism «…» bilan qisqartiriladi); administrator — «*» bilan."""
    name = " ".join(row.name.split()) or "?"
    mark = ADMIN_MARK if row.admin else ""
    room = NAME_WIDTH - 1 - len(mark)
    if len(name) > room:
        name = name[: room - 1] + "…"
    return (name + mark).ljust(NAME_WIDTH)


def table(ov: Overview) -> str:
    cols = ov.columns()
    lines = ["Operator".ljust(NAME_WIDTH) + "Jami".rjust(5) + "".join("  " + c for c in cols)]
    width = NAME_WIDTH + 5 + 4 * len(cols)
    for row in ov.rows:
        lines.append(_name(row) + str(row.total).rjust(5) + "".join(str(row.counts.get(c, 0)).rjust(4) for c in cols))
    total = ov.totals
    lines.append("─" * width)
    lines.append("JAMI".ljust(NAME_WIDTH) + str(total.total).rjust(5) + "".join(str(total.counts.get(c, 0)).rjust(4) for c in cols))
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def _breakdown(row: Row, cols: list[str]) -> str:
    return ", ".join(f"{html.escape(c)} {row.counts[c]}" for c in cols if row.counts.get(c))


def _period_line(ov: Overview, interim_at: datetime | None, late: bool) -> str:
    start, end = ov.start, ov.end
    text = f"{start:%d.%m %H:%M} → {end:%d.%m %H:%M}"
    if interim_at is not None:
        text += f" · oraliq holat {interim_at:%H:%M} gacha"
    if late:
        text += " · kechikib yuborildi"
    return f"<i>{text}</i>"


def render_report(ov: Overview, reasons: list[tuple[str, int]] | None = None, *,
                  interim_at: datetime | None = None, late: bool = False) -> str:
    day = ov.end
    title = "KUNLIK HISOBOT" if interim_at is None else "HISOBOT (oraliq)"
    lines = [f"📊 <b>{title} · {day:%d.%m.%Y} ({WEEKDAYS[day.weekday()]})</b>", _period_line(ov, interim_at, late), ""]
    if ov.rows and (ov.totals.total or ov.has_operator_list):
        lines.append(table(ov))
        if any(row.admin for row in ov.rows):
            lines.append(ADMIN_LEGEND)
    elif not ov.extras:
        lines.append("Bu davrda qo'ng'iroq qayd etilmadi.")
    cols = ov.columns()
    leaders, unlisted = ov.leaders, ov.unlisted
    for row in leaders[:EXTRAS_SHOWN]:  # rahbarlar — ro'yxatdagi ismi bilan, jadvaldan tashqarida
        detail = _breakdown(row, cols)
        lines.append(f"👔 Rahbar: {html.escape(row.name)} — {row.total} ta lid" + (f" ({detail})" if detail else ""))
    if len(leaders) > EXTRAS_SHOWN:
        lines.append(f"👔 … va yana {len(leaders) - EXTRAS_SHOWN} rahbar")
    for row in unlisted[:EXTRAS_SHOWN]:
        who = html.escape(row.name) + (f" (@{html.escape(row.username)})" if row.username and row.name != f"@{row.username}" else "")
        detail = _breakdown(row, cols)
        lines.append(f"➕ Ro'yxatda yo'q: {who} — {row.total} ta lid" + (f" ({detail})" if detail else ""))
    if len(unlisted) > EXTRAS_SHOWN:
        lines.append(f"➕ … va yana {len(unlisted) - EXTRAS_SHOWN} kishi")
    lines.append(f"📥 Yangi lidlar: {ov.new_leads} · belgilanmagan: {ov.unmarked}")

    positive = [s for s in ov.statuses if s.positive]
    if positive and any(p for _, _, p in ov.courses):
        mark = html.escape(positive[0].emoji)
        shown = [(c, p) for c, _, p in ov.courses if p][:COURSES_SHOWN]
        rest = sum(p for _, _, p in ov.courses) - sum(p for _, p in shown)
        parts = [f"{html.escape(c)} {p}" for c, p in shown] + ([f"boshqa {rest}"] if rest else [])
        lines.append(f"🎓 Kurslar bo'yicha {mark}: " + " · ".join(parts))

    notes = []
    if reasons:
        notes.append("rad sabablari — " + ", ".join(f"{html.escape(label)} ({count})" for label, count in reasons))
    if ov.callbacks:
        notes.append(f"qayta qo'ng'iroq so'raganlar — {ov.callbacks} ta")
    if notes:
        lines.append("💬 Izohlardan: " + "; ".join(notes))
    if ov.pending:
        lines.append(f"⏳ {ov.pending} ta izoh hali tahlil qilinmadi")
    if ov.conflicts:
        lines.append(f"⚠️ Ziddiyat: {ov.conflicts} ta lidda reaksiya va izoh mos emas")
    return "\n".join(lines)

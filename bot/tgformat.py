"""Claude javobidagi Markdown'ni Telegram HTML'iga o'girish va 4096 belgilik bo'laklarga bo'lish."""
from __future__ import annotations

import html
import re

TG_LIMIT = 4096
SOURCE_CHUNK = 3300  # bitta xabardagi xom matn (jadval va ro'yxat bezaklari uchun zaxira qoldiriladi)
PREVIEW_LIMIT = 3800

Block = tuple[str, str, str]  # (tur: "text" | "code", dasturlash tili, matn)

_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+#.-]*)")
_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$")  # "C#" dagi # matnga tegishli
_HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEP_CELL = re.compile(r"^:?-{2,}:?$")

_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_URL = r"(?:[^\s()]|\([^\s()]*\))+"  # bir qavat qavsga ruxsat: wiki/Python_(dasturlash_tili)
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://" + _URL + r"|tg://" + _URL + r"|mailto:" + _URL + r")(?:\s+\"[^\"\n]*\")?\)")
_TRIPLE = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDER = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_MARKS = re.compile(r"\*\*|__|~~|`")
_ANY_TAG = re.compile(r"<[^>]*>")
_FMT_TAG = re.compile(r"<(/?)([bis])>")


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _strip_marks(text: str) -> str:
    return _MARKS.sub("", text)


def _u16(text: str) -> int:
    """Telegram uzunlikni UTF-16 birliklarida sanaydi (emoji — 2)."""
    return len(text.encode("utf-16-le")) // 2


def _visible_len(h: str) -> int:
    """HTML xabarning Telegram sanaydigan uzunligi: teglar hisobga olinmaydi, &lt; — 1 belgi."""
    return _u16(html.unescape(_ANY_TAG.sub("", h)))


def _balanced(text: str) -> bool:
    stack: list[str] = []
    for m in _FMT_TAG.finditer(text):
        if not m.group(1):
            stack.append(m.group(2))
        elif not stack or stack.pop() != m.group(2):
            return False
    return not stack


def _wrap(tag: str):
    """Ichidagi teglar to'g'ri yopilgan bo'lsagina o'raydi (aks holda belgilar o'zgarmaydi)."""
    def sub(m: re.Match) -> str:
        inner = m.group(m.lastindex)
        return f"<{tag}>{inner}</{tag}>" if _balanced(inner) else m.group(0)
    return sub


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- bloklarga ajratish


def split_blocks(md: str) -> list[Block]:
    """Matnni oddiy matn va ``` kod bloklariga ajratadi."""
    blocks: list[Block] = []
    text_buf: list[str] = []
    code_buf: list[str] = []
    fence: str | None = None
    lang = ""
    for line in md.split("\n"):
        if fence is None:
            m = _FENCE_OPEN.match(line)
            # ```kod``` bitta qatorda yopilgan bo'lsa — bu oddiy matn
            if m and m.group(1) not in line.strip()[len(m.group(1)):]:
                if text_buf:
                    blocks.append(("text", "", "\n".join(text_buf)))
                    text_buf = []
                fence, lang, code_buf = m.group(1), m.group(2), []
            else:
                text_buf.append(line)
        else:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                blocks.append(("code", lang, "\n".join(code_buf)))
                fence = None
            else:
                code_buf.append(line)
    if fence is not None:
        blocks.append(("code", lang, "\n".join(code_buf)))
    elif text_buf:
        blocks.append(("text", "", "\n".join(text_buf)))
    return blocks


def _split_long(kind: str, lang: str, body: str, limit: int) -> list[Block]:
    if len(body) <= limit:
        return [(kind, lang, body)]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    head: list[str] = []  # joriy jadval sarlavhasi (sarlavha + ajratuvchi qator)
    data = 0  # sarlavhadan keyingi qatorlar soni
    for line in body.split("\n"):
        row = kind == "text" and bool(_TABLE_ROW.match(line))
        is_head = False
        if not row:
            head, data = [], 0
        elif not head:
            head, is_head = [line], True
        elif len(head) == 1 and not data and _is_sep_row(line):
            head.append(line)
            is_head = True
        while len(line) > limit:
            if buf:
                parts.append("\n".join(buf))
                buf, size = [], 0
            cut = line.rfind(" ", 0, limit)
            if cut <= limit // 2:
                cut = limit
            parts.append(line[:cut])
            line = line[cut:]
        if buf and size + len(line) + 1 > limit:
            # Jadval o'rtasida bo'linsa — keyingi qismda sarlavha takrorlanadi
            carry: list[str] = []
            if row and not (is_head and len(head) == 1):
                if data:
                    carry = list(head)  # sarlavha nusxasi
                elif 0 < len(head) - is_head < len(buf):
                    carry = buf[len(buf) - len(head) + is_head :]  # hali qatorsiz sarlavha ko'chadi
                if sum(len(c) + 1 for c in carry) + len(line) + 1 > limit:
                    carry = []
                elif not data:
                    buf = buf[: len(buf) - len(carry)]
            parts.append("\n".join(buf))
            buf, size = carry, sum(len(c) + 1 for c in carry)
        buf.append(line)
        size += len(line) + 1
        if row and not is_head:
            data += 1
    if buf:
        parts.append("\n".join(buf))
    return [(kind, lang, part) for part in parts]


def chunk_markdown(md: str, limit: int = SOURCE_CHUNK) -> list[list[Block]]:
    """Matnni Telegram xabarlariga sig'adigan bo'laklarga bo'ladi (kod bloklari buzilmaydi)."""
    chunks: list[list[Block]] = []
    current: list[Block] = []
    size = 0
    for kind, lang, body in split_blocks(md):
        for piece in _split_long(kind, lang, body, limit):
            piece_size = len(piece[2]) + (len(piece[1]) + 8 if piece[0] == "code" else 0) + 1
            if current and size + piece_size > limit:
                chunks.append(current)
                current, size = [], 0
            current.append(piece)
            size += piece_size
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------- HTML'ga o'girish


def _inline(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    def code(m: re.Match) -> str:
        inner = m.group(2).strip() or m.group(2)
        return keep(f"<code>{_esc(inner)}</code>")

    def link(m: re.Match) -> str:
        url = html.escape(m.group(2), quote=True)
        # Havola matnidagi `kod` oddiy matnga aylanadi (Telegram <a> ichida <code> ni qabul qilmaydi)
        label = _PLACEHOLDER.sub(lambda p: _ANY_TAG.sub("", stash[int(p.group(1))]), _esc(_strip_marks(m.group(1))))
        return keep(f'<a href="{url}">{label}</a>')

    text = text.replace("\x00", "")
    text = _CODE_SPAN.sub(code, text)
    text = _LINK.sub(link, text)
    text = _esc(text)
    # Har o'rash ichki teglar to'g'ri yopilgandagina bajariladi — kesishgan teglar chiqmaydi
    text = _TRIPLE.sub(lambda m: f"<b><i>{m.group(1)}</i></b>", text)
    text = _BOLD.sub(_wrap("b"), text)
    text = _STRIKE.sub(_wrap("s"), text)
    text = _ITALIC_STAR.sub(_wrap("i"), text)
    text = _ITALIC_UNDER.sub(_wrap("i"), text)
    return _PLACEHOLDER.sub(lambda m: stash[int(m.group(1))], text)


def _render_line(line: str) -> str:
    if _HR.match(line):
        return "──────────"
    m = _HEADER.match(line)
    if m:
        return f"<b>{_esc(_strip_marks(m.group(1)))}</b>"
    m = _BULLET.match(line)
    if m:
        depth = len(m.group(1).expandtabs(2)) // 2
        return "   " * depth + "• " + _inline(m.group(2))
    return _inline(line)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_sep_row(line: str) -> bool:
    cells = [c for c in _cells(line) if c]
    return bool(cells) and all(_SEP_CELL.match(c) for c in cells)


def _render_table(lines: list[str]) -> str:
    rows = [_cells(line) for line in lines]
    rows = [r for r in rows if not (any(r) and all(_SEP_CELL.match(c) for c in r if c))]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    col_w = [max(len(_strip_marks(r[i])) for r in rows) for i in range(width)]

    # Tor jadval — monospace ko'rinishda, keng jadval — telefonda o'qiladigan ro'yxat ko'rinishida
    if sum(col_w) + 3 * (width - 1) <= 34:
        def fmt(row: list[str]) -> str:
            return " │ ".join(_strip_marks(c).ljust(col_w[i]) for i, c in enumerate(row)).rstrip()

        out = [fmt(header), "─┼─".join("─" * w for w in col_w)] + [fmt(r) for r in body]
        return "<pre>" + _esc("\n".join(out)) + "</pre>"

    items = []
    for row in body or [header]:
        head = _esc(_strip_marks(row[0])) or "—"
        if body:
            rest = [f"{_esc(_strip_marks(header[i]))}: {_inline(row[i])}" for i in range(1, width) if row[i]]
        else:
            rest = [_inline(c) for c in row[1:] if c]
        items.append(f"▪️ <b>{head}</b>" + (" — " + "; ".join(rest) if rest else ""))
    return "\n".join(items)


def render_text(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _TABLE_ROW.match(line):
            j = i
            while j < len(lines) and _TABLE_ROW.match(lines[j]):
                j += 1
            if j - i >= 2:
                out.append(_render_table(lines[i:j]))
                i = j
                continue
        if _QUOTE.match(line):
            j = i
            quoted = []
            while j < len(lines) and _QUOTE.match(lines[j]):
                quoted.append(_render_line(_QUOTE.match(lines[j]).group(1)))
                j += 1
            if any(q.strip() for q in quoted):
                out.append("<blockquote>" + "\n".join(quoted).strip("\n") + "</blockquote>")
            i = j
            continue
        out.append(_render_line(line))
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def render_chunk(blocks: list[Block]) -> str:
    parts = []
    for kind, lang, body in blocks:
        if kind == "code":
            if not body.strip():
                continue
            cls = f' class="language-{lang}"' if lang else ""
            parts.append(f"<pre><code{cls}>{_esc(body)}</code></pre>")
        else:
            parts.append(render_text(body))
    return "\n".join(parts).strip()


def plain_chunk(blocks: list[Block]) -> str:
    parts = [f"```{lang}\n{body}\n```" if kind == "code" else body for kind, lang, body in blocks]
    return "\n".join(parts).strip()


def _fit(text: str, limit: int) -> int:
    """UTF-16 bo'yicha limit'ga sig'adigan eng uzun boshlang'ich qism (belgilar soni)."""
    units = 0
    for i, ch in enumerate(text):
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            return i
    return len(text)


def _hard_split(text: str, limit: int) -> list[str]:
    pieces = []
    while _u16(text) > limit:
        size = _fit(text, limit)
        cut = text.rfind("\n", 0, size)
        if cut <= size // 2:
            cut = text.rfind(" ", 0, size)
        if cut <= size // 2:
            cut = size
        pieces.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        pieces.append(text)
    return pieces


def format_answer(
    text: str,
    sources: list[tuple[str, str]],
    notes: list[str],
    footer: str | None,
) -> list[tuple[str | None, str]]:
    """Yakuniy javobni Telegram xabarlariga tayyorlaydi.

    Har bir element: (HTML matn yoki None, oddiy matn). HTML rad etilsa, oddiy matn yuboriladi.
    """
    text = (text or "").strip()
    rendered: list[list[str]] = []
    for chunk in chunk_markdown(text) if text else []:
        plain = plain_chunk(chunk)
        if plain:
            rendered.append([render_chunk(chunk) or _esc(plain), plain])

    tail_html: list[str] = []
    tail_plain: list[str] = []
    if sources:
        items, plain_items = [], []
        for n, (title, url) in enumerate(sources[:10], 1):
            name = _shorten(title or url, 70)
            items.append(f'{n}. <a href="{html.escape(url, quote=True)}">{_esc(name)}</a>')
            plain_items.append(f"{n}. {name} — {url}")
        tail_html.append("<blockquote expandable>🔗 <b>Manbalar</b>\n" + "\n".join(items) + "</blockquote>")
        tail_plain.append("🔗 Manbalar:\n" + "\n".join(plain_items))
    for note in notes:
        tail_html.append(_esc(note))
        tail_plain.append(note)
    if footer:
        tail_html.append(f"<i>{_esc(footer)}</i>")
        tail_plain.append(footer)

    if tail_html:
        h, p = "\n\n".join(tail_html), "\n\n".join(tail_plain)
        if (
            rendered
            and _visible_len(rendered[-1][0]) + _visible_len(h) + 2 <= TG_LIMIT
            and _u16(rendered[-1][1]) + _u16(p) + 2 <= TG_LIMIT
        ):
            rendered[-1][0] += "\n\n" + h
            rendered[-1][1] += "\n\n" + p
        else:
            rendered.append([h, p])
    if not rendered:
        rendered.append([_esc("(bo'sh javob)"), "(bo'sh javob)"])

    out: list[tuple[str | None, str]] = []
    for h, p in rendered:
        # Telegram cheklovi teglar olib tashlangandan keyingi matnga qo'yiladi (UTF-16)
        if _visible_len(h) <= TG_LIMIT and _u16(p) <= TG_LIMIT:
            out.append((h, p))
        else:
            out.extend((None, piece) for piece in _hard_split(p, TG_LIMIT))
    return out


def preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    """Javob yozilayotganda ko'rsatiladigan oddiy matn (oxirgi qismi)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]

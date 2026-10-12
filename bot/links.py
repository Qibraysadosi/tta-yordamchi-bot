"""Xabardagi havolalarni o'qish: sahifa matni yoki PDF Claude'ga hujjat sifatida beriladi."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

import httpx2

from . import media

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
MAX_LINKS = 3
TIMEOUT = 25.0
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
QUIET_TAGS = {"nav", "footer", "aside", "form", "button"}
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
    "blockquote", "pre", "td", "th", "dd", "dt", "figcaption", "hr", "table", "ul", "ol",
}
HEADING_TAGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}
SKIP_HOSTS = ("t.me", "telegram.me")


class LinkError(Exception):
    pass


@dataclass
class Fetched:
    url: str
    title: str
    block: dict  # Claude uchun document bloki


def find_links(text: str) -> list[str]:
    seen: list[str] = []
    for raw in URL_RE.findall(text):
        url = raw.rstrip(".,;:!?»")
        host = url.split("/", 3)[2].lower() if url.count("/") >= 2 else ""
        if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            continue
        if url not in seen:
            seen.append(url)
    return seen[:MAX_LINKS]


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._quiet = 0
        self._parts: list[str] = []
        self._pending_prefix = ""

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in QUIET_TAGS:
            self._quiet += 1
        if tag in BLOCK_TAGS:
            self._parts.append("\n")
            if tag in HEADING_TAGS:
                self._pending_prefix = HEADING_TAGS[tag]
            elif tag == "li":
                self._pending_prefix = "• "

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in QUIET_TAGS and self._quiet:
            self._quiet -= 1
        if tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip or self._quiet:
            return
        text = re.sub(r"\s+", " ", data)
        if text.strip():
            if self._pending_prefix:
                self._parts.append(self._pending_prefix)
                self._pending_prefix = ""
            self._parts.append(text)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(html: str) -> tuple[str, str]:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    return unescape(" ".join(parser.title.split())), parser.text()


async def fetch(url: str, max_bytes: int, client: httpx2.AsyncClient | None = None) -> Fetched:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TTAYordamchiBot/1.0; +https://t.me/TTA_yordamchi_bot)",
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "uz,ru;q=0.8,en;q=0.6",
    }
    own = client is None
    client = client or httpx2.AsyncClient(follow_redirects=True, timeout=TIMEOUT, headers=headers)
    try:
        try:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise LinkError(f"sahifa {response.status_code} xato qaytardi")
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise LinkError(f"fayl {max(1, max_bytes // (1024 * 1024))} MB dan katta")
                    chunks.append(chunk)
                data = b"".join(chunks)
                encoding = response.encoding or "utf-8"
        except httpx2.TimeoutException as exc:
            raise LinkError("sahifa juda sekin ochildi (vaqt tugadi)") from exc
        except httpx2.HTTPError as exc:
            raise LinkError(f"ulanib bo'lmadi ({type(exc).__name__})") from exc
    finally:
        if own:
            await client.aclose()

    name = url.rstrip("/").rsplit("/", 1)[-1] or url
    if content_type == "application/pdf" or data[:5] == b"%PDF-":
        block = await asyncio.to_thread(media.pdf_block, data, name if name.lower().endswith(".pdf") else name + ".pdf")
        return Fetched(url, name, block)
    if content_type.startswith("text/html") or content_type in ("application/xhtml+xml", ""):
        try:
            html = data.decode(encoding, errors="replace")
        except LookupError:
            html = data.decode("utf-8", errors="replace")
        title, text = await asyncio.to_thread(html_to_text, html)
        if len(text) < 200:
            raise LinkError("sahifadan matn olib bo'lmadi (ehtimol u faqat brauzerda yuklanadi)")
        text = text[: media.MAX_TEXT_CHARS]
        return Fetched(url, title or name, media.text_block(f"Manba: {url}\nSarlavha: {title}\n\n{text}", title or name))
    if content_type.startswith("text/") or content_type in ("application/json", "application/xml"):
        text = media.decode_text(data)[: media.MAX_TEXT_CHARS]
        return Fetched(url, name, media.text_block(f"Manba: {url}\n\n{text}", name))
    raise LinkError(f"bu turdagi faylni o'qiy olmayman ({content_type or 'noma’lum'})")


async def read_links(text: str, max_bytes: int) -> tuple[list[dict], list[str]]:
    """Matndagi havolalarni o'qiydi: (hujjat bloklari, xato izohlari)."""
    urls = find_links(text)
    if not urls:
        return [], []
    results = await asyncio.gather(*(fetch(u, max_bytes) for u in urls), return_exceptions=True)
    blocks: list[dict] = []
    errors: list[str] = []
    for url, result in zip(urls, results):
        if isinstance(result, Fetched):
            blocks.append(result.block)
        elif isinstance(result, LinkError):
            errors.append(f"⚠️ {url} — {result}")
        else:
            errors.append(f"⚠️ {url} — o'qib bo'lmadi")
    return blocks, errors

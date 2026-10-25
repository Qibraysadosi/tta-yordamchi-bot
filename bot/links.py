"""Xabardagi havolalarni o'qish: sahifa matni yoki PDF Claude'ga hujjat sifatida beriladi."""
from __future__ import annotations

import asyncio
import codecs
import ipaddress
import re
import socket
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx2

from . import media

# Qavslar faqat juft bo'lsa URL ichida qoladi: .../Python_(programming_language), lekin (https://kun.uz/x)
URL_RE = re.compile(r"https?://(?:[^\s<>()\[\]\"'`]|\([^\s()<>]*\))+")
META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.I)
MAX_LINKS = 3
TIMEOUT = 25.0
# head yo'q: </head> yozilmasa butun sahifa tushib qolardi (sarlavha _in_title orqali olinadi)
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "select", "textarea"}
# form yo'q: ASP.NET/SharePoint sahifalarida butun matn bitta <form> ichida bo'ladi
QUIET_TAGS = {"nav", "footer", "aside", "button"}
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
    "blockquote", "pre", "td", "th", "dd", "dt", "figcaption", "hr", "table", "ul", "ol",
}
HEADING_TAGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}
SKIP_HOSTS = ("t.me", "telegram.me")
MAX_REDIRECTS = 5
URL_SHOWN = 80  # xato xabaridagi havola uzunligi (uzun havolalar xabarni Telegram chegarasidan oshirmasin)


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
        self._svg = 0  # <svg><title> — belgi nomi, sahifa sarlavhasi emas
        self._pre = 0  # <pre> ichida bo'shliq va qator ko'chishi saqlanadi (kod)
        self._parts: list[str] = []
        self._pending_prefix = ""

    def handle_starttag(self, tag, attrs):
        if tag == "svg":
            self._svg += 1
        elif tag == "pre":
            self._pre += 1
        if tag == "title" and not self._svg and not self.title:
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
        elif tag == "svg" and self._svg:
            self._svg -= 1
        elif tag == "pre" and self._pre:
            self._pre -= 1
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
        text = data if self._pre else re.sub(r"\s+", " ", data)
        if not text.strip():
            # faqat bo'shliq: <b>Salom</b> <i>dunyo</i> so'zlari yopishib qolmasin; <pre> da — o'zgarishsiz
            if self._pre:
                self._parts.append(text)
            elif text and self._parts and not self._parts[-1][-1:].isspace():
                self._parts.append(" ")
            return
        if self._pending_prefix:
            self._parts.append(self._pending_prefix)
            self._pending_prefix = ""
        self._parts.append(text)

    def text(self) -> str:
        raw = "".join(self._parts)
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _meta_charset(data: bytes) -> str | None:
    match = META_CHARSET_RE.search(data[:4096])
    if not match:
        return None
    name = match.group(1).decode("ascii", "ignore")
    return "utf-8" if name.lower().startswith("utf-16") else name  # BOM'siz meta utf-16 — aslida UTF-8 (HTML qoidasi)


def decode_html(data: bytes, header_charset: str | None) -> str:
    """Kodlash tartibi: BOM → Content-Type charset → <meta charset> → UTF-8 (juda buzuq bo'lsa cp1251)."""
    if data.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return media.decode_text(data)
    for name in (header_charset, _meta_charset(data)):
        if not name:
            continue
        try:
            return data.decode(codecs.lookup(name).name, errors="replace")
        except LookupError:
            continue
    text = data.decode("utf-8", errors="replace")
    if text.count("\ufffd") <= len(text) // 100:  # bir-ikki buzuq bayt — baribir UTF-8
        return text
    return media.decode_text(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    return unescape(" ".join(parser.title.split())), parser.text()


def shown_url(url: str) -> str:
    return url if len(url) <= URL_SHOWN else url[: URL_SHOWN - 1] + "…"


async def check_public(url: str) -> None:
    """Faqat ochiq internet: localhost, ichki tarmoq (10.x, 192.168.x, 172.16–31.x), server o'zi va maxsus
    manzillar o'qilmaydi — bot orqali serverdagi ichki xizmatlarga kirib bo'lmasin."""
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise LinkError("havola noto'g'ri") from exc
    if parts.scheme not in ("http", "https") or not host:
        raise LinkError("havola noto'g'ri")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except (socket.gaierror, UnicodeError) as exc:
        raise LinkError("sayt topilmadi (manzil noto'g'ri yoki internet yo'q)") from exc
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not address.is_global:
            raise LinkError("ichki tarmoq manzili — bunday havola o'qilmaydi")


async def _download(client: httpx2.AsyncClient, url: str, max_bytes: int, *, guard: bool) -> tuple[bytes, str, str | None]:
    """(baytlar, content-type, charset). guard — har qadamda (yo'naltirishlar ham) manzil tekshiriladi."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if guard:
            await check_public(current)
        async with client.stream("GET", current) as response:
            if guard and response.is_redirect:
                current = urljoin(current, response.headers.get("location", ""))
                continue
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
            return b"".join(chunks), content_type, response.charset_encoding
    raise LinkError("sahifa juda ko'p marta boshqa manzilga yo'naltirdi")


async def fetch(url: str, max_bytes: int, client: httpx2.AsyncClient | None = None, *,
                transport: httpx2.AsyncBaseTransport | None = None) -> Fetched:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TTAYordamchiBot/1.0; +https://t.me/TTA_yordamchi_bot)",
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "uz,ru;q=0.8,en;q=0.6",
    }
    own = client is None
    # O'z mijozimiz: yo'naltirishlarni o'zimiz kuzatamiz — har bir manzil ichki tarmoqqa olib bormasligi tekshiriladi
    client = client or httpx2.AsyncClient(follow_redirects=False, timeout=TIMEOUT, headers=headers, transport=transport)
    try:
        try:
            data, content_type, header_charset = await _download(client, url, max_bytes, guard=own)
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
        html = decode_html(data, header_charset)
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
            errors.append(f"⚠️ {shown_url(url)} — {result}")
        else:
            errors.append(f"⚠️ {shown_url(url)} — o'qib bo'lmadi")
    return blocks, errors

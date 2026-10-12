"""Test yordamchilari: soxta Anthropic SSE oqimi va soxta Telegram sessiyasi."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import anthropic
import httpx2
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Document, File, Message, PhotoSize, User


# ------------------------------------------------------------- Anthropic SSE
def sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def msg_start(model="claude-opus-5-5", input_tokens=100, cache_read=0, cache_write=0):
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1,
                      "cache_creation_input_tokens": cache_write, "cache_read_input_tokens": cache_read},
        },
    }


def thinking(index):
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": "sig123"}},
        {"type": "content_block_stop", "index": index},
    ]


def text(index, pieces, citations=None):
    evs = [{"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}]
    for p in pieces:
        evs.append({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": p}})
    for c in citations or []:
        evs.append({"type": "content_block_delta", "index": index, "delta": {"type": "citations_delta", "citation": c}})
    evs.append({"type": "content_block_stop", "index": index})
    return evs


def search(index, query, url="https://kun.uz/x", title="Kun.uz maqola"):
    return [
        {"type": "content_block_start", "index": index,
         "content_block": {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}}},
        {"type": "content_block_delta", "index": index,
         "delta": {"type": "input_json_delta", "partial_json": json.dumps({"query": query})}},
        {"type": "content_block_stop", "index": index},
        {"type": "content_block_start", "index": index + 1, "content_block": {
            "type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
            "content": [{"type": "web_search_result", "url": url, "title": title,
                         "encrypted_content": "enc", "page_age": "1 day"}]}},
        {"type": "content_block_stop", "index": index + 1},
    ]


def tool_use(index, name, args, tool_id="toolu_1"):
    """Claude vositani chaqiradi (client tool): input JSON bo'lak-bo'lak keladi."""
    return [
        {"type": "content_block_start", "index": index,
         "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}}},
        {"type": "content_block_delta", "index": index,
         "delta": {"type": "input_json_delta", "partial_json": json.dumps(args, ensure_ascii=False)}},
        {"type": "content_block_stop", "index": index},
    ]


def tool_answer(name, args, before="", model="claude-opus-5-5", tool_id="toolu_1", output_tokens=80):
    """Javob: (ixtiyoriy matn) + vosita chaqiruvi, stop_reason=tool_use."""
    events = [msg_start(model, 100)]
    if before:
        events += text(0, [before])
    events += tool_use(1 if before else 0, name, args, tool_id)
    events += [delta("tool_use", output_tokens), STOP]
    return (200, sse(events))


def delta(stop_reason, output_tokens, *, stop_details=None, searches=None, thinking_tokens=None):
    usage: dict[str, Any] = {"output_tokens": output_tokens}
    if searches is not None:
        usage["server_tool_use"] = {"web_search_requests": searches, "web_fetch_requests": 0}
    if thinking_tokens is not None:
        usage["output_tokens_details"] = {"thinking_tokens": thinking_tokens}
    d: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details:
        d["stop_details"] = stop_details
    return {"type": "message_delta", "delta": d, "usage": usage}


STOP = {"type": "message_stop"}


def simple_answer(answer: str, model="claude-opus-5-5", input_tokens=100, output_tokens=50):
    return (200, sse([msg_start(model, input_tokens), *thinking(0), *text(1, [answer]), delta("end_turn", output_tokens), STOP]))


def json_answer(answer: str, model="claude-haiku-5-5", input_tokens=120, output_tokens=2):
    """Oqimsiz (messages.create) javob — tasniflash uchun."""
    return (200, {
        "id": "msg_c", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": answer}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


class FakeAnthropic:
    """Navbatdagi javoblarni qaytaradi va so'rovlarni yozib boradi."""

    def __init__(self, responses: list | None = None, delay: float = 0.0):
        self.responses = list(responses or [])
        self.requests: list[dict] = []
        self.delay = delay

    async def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.delay:
            await asyncio.sleep(self.delay)
        status, payload = self.responses.pop(0)
        if status == 200 and isinstance(payload, (bytes, str)):
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=payload)
        return httpx2.Response(status, json=payload)

    def client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(
            api_key="test-key",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)),
            max_retries=0,
        )


# ------------------------------------------------------------- Telegram
class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []
        self.next_id = 5000
        self.files: dict[str, bytes] = {}
        self.fail_html_marker: str | None = None
        self.updates: list[Any] = []  # start_polling sinovi uchun: getUpdates navbati
        self.photo_uploads = 0
        self.documents: list[tuple[str | None, bytes, str | None]] = []  # yuborilgan fayllar (nom, bayt, izoh)
        self.resent: list[str] = []  # file_id bilan qayta yuborilgan fayllar

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        now = datetime.now()
        if name in ("SendMessage", "EditMessageText"):
            if (
                self.fail_html_marker
                and getattr(method, "parse_mode", None) == "HTML"
                and self.fail_html_marker in method.text
            ):
                raise TelegramBadRequest(method=method, message="Bad Request: can't parse entities: unsupported start tag")
            if name == "SendMessage":
                self.next_id += 1
                mid = self.next_id
            else:
                mid = method.message_id
            return Message(message_id=mid, date=now, chat=Chat(id=method.chat_id, type="private"), text=method.text).as_(bot)
        if name == "SendPhoto":
            self.next_id += 1
            self.photo_uploads += 0 if isinstance(method.photo, str) else 1
            photo = [PhotoSize(file_id=f"photo-{self.next_id}", file_unique_id=f"u{self.next_id}", width=1280, height=720)]
            return Message(message_id=self.next_id, date=now, chat=Chat(id=method.chat_id, type="private"),
                           photo=photo, caption=method.caption).as_(bot)
        if name == "EditMessageCaption":
            return Message(message_id=method.message_id, date=now, chat=Chat(id=method.chat_id, type="private"),
                           caption=method.caption).as_(bot)
        if name == "SendChatAction":
            return True
        if name == "EditMessageReplyMarkup":
            return Message(message_id=method.message_id, date=now, chat=Chat(id=method.chat_id, type="private")).as_(bot)
        if name == "SendDocument":
            self.next_id += 1
            doc = method.document
            self.documents.append((getattr(doc, "filename", None), getattr(doc, "data", b""), method.caption))
            if isinstance(doc, str):  # file_id bilan qayta yuborish
                self.resent.append(doc)
            document = Document(file_id=f"doc-{self.next_id}", file_unique_id=f"du{self.next_id}",
                                file_name=getattr(doc, "filename", None) or "fayl")
            return Message(message_id=self.next_id, date=now, chat=Chat(id=method.chat_id, type="private"),
                           document=document, caption=method.caption).as_(bot)
        if name == "GetFile":
            return File(file_id=method.file_id, file_unique_id="u" + method.file_id, file_path=f"files/{method.file_id}")
        if name == "GetMe":
            return User(id=999, is_bot=True, first_name="ClaudeBot", username="claude_test_bot")
        if name == "GetUpdates":
            if self.updates:
                return [self.updates.pop(0)]
            await asyncio.sleep(0.05)
            return []
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        file_id = url.rsplit("/", 1)[-1]
        yield self.files[file_id]

    # qulay filtrlar
    def of(self, name: str) -> list[Any]:
        return [c for c in self.calls if type(c).__name__ == name]

    def outgoing(self) -> list[tuple[str, Any]]:
        """Foydalanuvchi ko'radigan matnlar tartib bilan: xabar matni yoki rasm izohi."""
        result = []
        for call in self.calls:
            name = type(call).__name__
            if name in ("SendMessage", "EditMessageText"):
                result.append((name, call.text))
            elif name in ("SendPhoto", "EditMessageCaption"):
                result.append((name, call.caption))
        return result

    def last_out(self) -> str:
        return self.outgoing()[-1][1]

    def clear(self) -> None:
        self.calls.clear()

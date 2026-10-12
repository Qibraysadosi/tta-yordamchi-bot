"""Claude API bilan ishlash: oqimli javob, internet qidiruvi, pause_turn, rad etish va narx hisobi."""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from .models import cost_usd, usage_numbers

log = logging.getLogger(__name__)

# Uzoq qidiruvda API javobni "pause_turn" bilan to'xtatib turadi — shuncha marta davom ettiramiz
MAX_CONTINUATIONS = 4


class StreamListener(Protocol):
    async def on_message_start(self, model: str, usage: Any) -> None: ...
    async def on_thinking(self) -> None: ...
    async def on_search(self, query: str | None) -> None: ...
    async def on_text(self, text: str) -> None: ...
    async def on_iteration_done(self, cost: float) -> None: ...


@dataclass
class TurnResult:
    model: str
    text: str
    stop_reason: str | None
    numbers: dict[str, int]
    cost: float
    sources: list[tuple[str, str]] = field(default_factory=list)  # (sarlavha, url)
    prompt_tokens: int = 0
    refusal_category: str | None = None
    refusal_explanation: str | None = None

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"

    @property
    def searches(self) -> int:
        return self.numbers.get("searches", 0)


class ClaudeError(Exception):
    """Foydalanuvchiga ko'rsatiladigan API xatosi."""

    def __init__(self, message: str, *, search_problem: bool = False) -> None:
        super().__init__(message)
        self.user_message = message
        self.search_problem = search_problem


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def blocks_to_text(blocks: list[Any]) -> str:
    """Javobdagi matn bloklarini birlashtiradi (qidiruvdan oldingi va keyingi matn orasiga bo'sh qator)."""
    parts: list[str] = []
    previous = None
    for block in blocks:
        btype = _attr(block, "type")
        if btype == "text":
            piece = _attr(block, "text") or ""
            if parts and previous != "text" and not parts[-1].endswith("\n") and not piece.startswith("\n"):
                parts.append("\n\n")
            parts.append(piece)
        previous = btype
    return "".join(parts).strip()


def collect_sources(blocks: list[Any], into: dict[str, str]) -> None:
    for block in blocks:
        if _attr(block, "type") != "text":
            continue
        for citation in _attr(block, "citations") or []:
            if _attr(citation, "type") == "web_search_result_location":
                url = _attr(citation, "url")
                if url and url not in into:
                    into[url] = _attr(citation, "title") or url


def _error_text(exc: anthropic.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(getattr(exc, "message", "") or exc)


def translate_error(exc: anthropic.APIStatusError, web_search: bool) -> ClaudeError:
    status = exc.status_code
    text = _error_text(exc)
    low = text.lower()
    log.warning("Claude API xatosi %s: %s", status, text)
    if "credit balance" in low or status == 402:
        return ClaudeError(
            "💳 API kredit tugadi. Oylik kredit yangilanishini kuting yoki "
            "platform.claude.com → Settings → Billing bo'limida to'ldiring."
        )
    if status == 401:
        return ClaudeError("🔑 ANTHROPIC_API_KEY noto'g'ri yoki bekor qilingan. Serverdagi .env faylini tekshiring.")
    if status == 403:
        return ClaudeError("🚫 Bu API kalit bilan ushbu modelga ruxsat yo'q. /model orqali boshqasini tanlang.")
    if status == 404:
        return ClaudeError("❓ Model topilmadi. /model orqali boshqasini tanlang.")
    if status == 413:
        return ClaudeError("📦 So'rov juda katta (fayl yoki suhbat uzun). /new bosing yoki kichikroq fayl yuboring.")
    if status == 429:
        if "spend" in low:
            return ClaudeError("💸 Console'dagi xarajat limitiga yetildi. Limitni oshiring yoki keyingi oyni kuting.")
        return ClaudeError("⏳ So'rovlar limiti oshdi. Bir daqiqadan keyin qayta urinib ko'ring.")
    if status >= 500:
        return ClaudeError("🛠 Anthropic serverlari hozir band. Birozdan keyin qayta urinib ko'ring.")
    if status == 400:
        if "prompt is too long" in low or "context window" in low or "too many tokens" in low:
            return ClaudeError("📚 Suhbat model chegarasidan uzun bo'lib ketdi. /new bosing.")
        if "spend" in low and "limit" in low:
            return ClaudeError("💸 Xarajat limitiga yetildi (Console → Limits).")
        if web_search and ("web_search" in low or "tool" in low):
            return ClaudeError("🔎 Internet qidiruvi bu modelda ishlamadi.", search_problem=True)
    return ClaudeError(f"⚠️ API xatosi ({status}): {text[:300]}")


class ClaudeService:
    def __init__(
        self,
        *,
        api_key: str,
        max_tokens: int,
        web_search_tool: str,
        web_search_max_uses: int,
        cache_ttl: str,
        search_city: str = "Tashkent",
        search_country: str = "UZ",
        timezone: str = "Asia/Tashkent",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.client = client or anthropic.AsyncAnthropic(
            api_key=api_key,
            max_retries=3,
            timeout=anthropic.Timeout(900.0, connect=20.0),
        )
        self.max_tokens = max_tokens
        self.web_search_tool = web_search_tool
        self.web_search_max_uses = web_search_max_uses
        self.cache_ttl = cache_ttl
        self.user_location = {
            "type": "approximate",
            "city": search_city,
            "country": search_country,
            "timezone": timezone,
        }

    def build_params(
        self, *, model: str, system: str, messages: list[dict], effort: str | None, web_search: bool
    ) -> dict[str, Any]:
        cache_control: dict[str, str] = {"type": "ephemeral"}
        if self.cache_ttl == "1h":
            cache_control["ttl"] = "1h"
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            # Avtomatik kesh: takrorlanuvchi suhbat tarixi 10–40 baravar arzon o'qiladi
            "cache_control": cache_control,
        }
        if effort:
            params["output_config"] = {"effort": effort}
        if web_search:
            params["tools"] = [
                {
                    "type": self.web_search_tool,
                    "name": "web_search",
                    "max_uses": self.web_search_max_uses,
                    "user_location": dict(self.user_location),
                }
            ]
        return params

    async def run_turn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        effort: str | None,
        web_search: bool,
        listener: StreamListener,
    ) -> TurnResult:
        assistant_blocks: list[Any] = []
        numbers: Counter[str] = Counter()
        cost = 0.0
        sources: dict[str, str] = {}
        display = ""
        last_type: str | None = None
        prompt_tokens = 0
        final = None

        for _ in range(MAX_CONTINUATIONS + 1):
            convo = list(messages)
            if assistant_blocks:  # pause_turn: to'xtagan javobni o'zgarishsiz qaytarib, davom ettiramiz
                convo.append({"role": "assistant", "content": list(assistant_blocks)})
            params = self.build_params(
                model=model, system=system, messages=convo, effort=effort, web_search=web_search
            )
            try:
                async with self.client.messages.stream(**params) as stream:
                    async for event in stream:
                        etype = event.type
                        if etype == "message_start":
                            await listener.on_message_start(model, event.message.usage)
                        elif etype == "content_block_start":
                            btype = event.content_block.type
                            if btype in ("thinking", "redacted_thinking"):
                                await listener.on_thinking()
                            elif btype == "server_tool_use":
                                await listener.on_search(None)
                            elif btype == "text" and display and last_type != "text" and not display.endswith("\n"):
                                display += "\n\n"
                            last_type = btype
                        elif etype == "content_block_stop":
                            block = getattr(event, "content_block", None)
                            if block is not None and block.type == "server_tool_use":
                                query = block.input.get("query") if isinstance(block.input, dict) else None
                                await listener.on_search(query)
                        elif etype == "text":
                            display += event.text
                            await listener.on_text(display)
                    final = await stream.get_final_message()
            except anthropic.APIStatusError as exc:
                raise translate_error(exc, web_search) from exc
            except anthropic.APITimeoutError as exc:
                raise ClaudeError("⏱ Claude javob berishga ulgurmadi (vaqt tugadi). Qayta urinib ko'ring.") from exc
            except anthropic.APIConnectionError as exc:
                raise ClaudeError("🌐 Anthropic serveriga ulanib bo'lmadi. Server internetini tekshiring.") from exc

            step = usage_numbers(final.usage, self.cache_ttl)
            step_cost = cost_usd(model, step)
            numbers.update(step)
            cost += step_cost
            prompt_tokens = step["input"] + step["cache_read"] + step["cache_write"]
            await listener.on_iteration_done(step_cost)
            collect_sources(final.content, sources)
            assistant_blocks.extend(final.content)
            if final.stop_reason != "pause_turn":
                break

        details = getattr(final, "stop_details", None)
        refused = final.stop_reason == "refusal"
        return TurnResult(
            model=model,
            # Rad etilgan javobning chala qismi tashlab yuboriladi (rasmiy tavsiya)
            text="" if refused else blocks_to_text(assistant_blocks),
            stop_reason=final.stop_reason,
            numbers=dict(numbers),
            cost=cost,
            sources=[] if refused else [(title, url) for url, title in sources.items()],
            prompt_tokens=prompt_tokens,
            refusal_category=_attr(details, "category") if details else None,
            refusal_explanation=_attr(details, "explanation") if details else None,
        )

    def estimate_partial(self, model: str, start_usage: Any, partial_text: str) -> tuple[dict[str, int], float]:
        """Javob to'xtatilganda taxminiy narx: kirish aniq, chiqish matn uzunligidan taxminan."""
        numbers = usage_numbers(start_usage, self.cache_ttl) if start_usage is not None else {}
        numbers = dict(numbers)
        numbers["output"] = max(numbers.get("output", 0), len(partial_text) // 3)
        return numbers, cost_usd(model, numbers)

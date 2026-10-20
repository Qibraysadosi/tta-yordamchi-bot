"""Claude API bilan ishlash: oqimli javob, internet qidiruvi, pause_turn, rad etish va narx hisobi."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import anthropic
import httpx2

from .models import cost_usd, usage_numbers

log = logging.getLogger(__name__)

# Uzoq qidiruvda API javobni "pause_turn" bilan to'xtatib turadi — shuncha marta davom ettiramiz
MAX_CONTINUATIONS = 4
# Bitta javob ichida vositalar (fayl yaratish va h.k.) ko'pi bilan shuncha marta chaqiriladi
MAX_TOOL_ROUNDS = 6
# Tasniflashda fikrlashni o'chirish mumkin bo'lgan modellar (boshqalarida `disabled` 400 qaytaradi)
CLASSIFY_NO_THINKING = frozenset({"claude-haiku-5-5"})
# Oqim ichidagi `event: error` (HTTP 200): xato turi bo'yicha mos HTTP holati
STREAM_ERROR_STATUS = {
    "invalid_request_error": 400, "authentication_error": 401, "billing_error": 402,
    "permission_error": 403, "not_found_error": 404, "request_too_large": 413,
    "rate_limit_error": 429, "api_error": 500, "timeout_error": 504, "overloaded_error": 529,
}

ToolRunner = Callable[[str, dict], Awaitable[str]]


class StreamListener(Protocol):
    async def on_message_start(self, model: str, usage: Any) -> None: ...
    async def on_thinking(self) -> None: ...
    async def on_search(self, query: str | None) -> None: ...
    async def on_text(self, text: str) -> None: ...
    async def on_tool(self, status: str) -> None: ...
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
    tool_calls: int = 0

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"

    @property
    def searches(self) -> int:
        return self.numbers.get("searches", 0)


class ClaudeError(Exception):
    """Foydalanuvchiga ko'rsatiladigan API xatosi."""

    def __init__(self, message: str, *, search_problem: bool = False, retryable: bool = False) -> None:
        super().__init__(message)
        self.user_message = message
        self.search_problem = search_problem
        self.retryable = retryable  # vaqtinchalik xato (band, limit, tarmoq) — qayta urinsa bo'ladi
        # Xatogacha tugagan (to'langan) qadamlar — usage_log ga yoziladi
        self.numbers: dict[str, int] = {}
        self.cost = 0.0


def _with_spent(err: ClaudeError, numbers: Counter[str], cost: float) -> ClaudeError:
    err.numbers, err.cost = dict(numbers), cost
    return err


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
    if status < 400:  # oqim ichidagi `event: error`: javob 200 bilan ochilgan, asl turi body'da
        error = exc.body.get("error") if isinstance(exc.body, dict) else None
        status = STREAM_ERROR_STATUS.get(error.get("type") if isinstance(error, dict) else None, 500)
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
        return ClaudeError("⏳ So'rovlar limiti oshdi. Bir daqiqadan keyin qayta urinib ko'ring.", retryable=True)
    if status >= 500:
        return ClaudeError("🛠 Anthropic serverlari hozir band. Birozdan keyin qayta urinib ko'ring.", retryable=True)
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
        search_country: str = "",
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
        # Qidiruv uchun joylashuv: country — ISO kodi, lekin API ba'zi mamlakatlarni (UZ ham) qabul qilmaydi
        # («Country code UZ is not supported»), shuning uchun standartda yuborilmaydi; shahar va vaqt zonasi yetarli
        self.user_location = {"type": "approximate"}
        if search_city:
            self.user_location["city"] = search_city
        if search_country:
            self.user_location["country"] = search_country
        if timezone:
            self.user_location["timezone"] = timezone

    def build_params(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        effort: str | None,
        web_search: bool,
        tools: list[dict] | None = None,
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
        all_tools: list[dict] = []
        if web_search:
            all_tools.append(
                {
                    "type": self.web_search_tool,
                    "name": "web_search",
                    "max_uses": self.web_search_max_uses,
                    "user_location": dict(self.user_location),
                }
            )
        all_tools.extend(tools or [])
        if all_tools:
            params["tools"] = all_tools
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
        tools: list[dict] | None = None,
        tool_runner: ToolRunner | None = None,
    ) -> TurnResult:
        assistant_blocks: list[Any] = []  # javobning barcha bloklari (matn tarixga shu yerdan olinadi)
        pending_blocks: list[Any] = []  # pause_turn: to'xtagan javobni o'zgarishsiz qaytarib, davom ettiramiz
        extra: list[dict] = []  # vositalar: assistant(tool_use) + user(tool_result) juftlari
        numbers: Counter[str] = Counter()
        cost = 0.0
        sources: dict[str, str] = {}
        display = ""
        last_type: str | None = None
        prompt_tokens = 0
        final = None
        tool_calls = 0
        tool_rounds = 0

        for _ in range(MAX_CONTINUATIONS + MAX_TOOL_ROUNDS + 1):
            convo = list(messages) + extra
            if pending_blocks:
                convo.append({"role": "assistant", "content": list(pending_blocks)})
            params = self.build_params(
                model=model, system=system, messages=convo, effort=effort, web_search=web_search, tools=tools
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
                raise _with_spent(translate_error(exc, web_search), numbers, cost) from exc
            except (anthropic.APITimeoutError, httpx2.TimeoutException) as exc:
                err = ClaudeError("⏱ Claude javob berishga ulgurmadi (vaqt tugadi). Qayta urinib ko'ring.", retryable=True)
                raise _with_spent(err, numbers, cost) from exc
            except anthropic.APIConnectionError as exc:
                err = ClaudeError("🌐 Anthropic serveriga ulanib bo'lmadi. Server internetini tekshiring.", retryable=True)
                raise _with_spent(err, numbers, cost) from exc
            except httpx2.TransportError as exc:  # oqim o'rtasida aloqa uzildi (SDK buni o'ramaydi)
                err = ClaudeError("🌐 Anthropic bilan aloqa uzilib qoldi. Qayta urinib ko'ring.", retryable=True)
                raise _with_spent(err, numbers, cost) from exc

            step = usage_numbers(final.usage, self.cache_ttl)
            step_cost = cost_usd(model, step)
            numbers.update(step)
            cost += step_cost
            prompt_tokens = step["input"] + step["cache_read"] + step["cache_write"]
            await listener.on_iteration_done(step_cost)
            collect_sources(final.content, sources)
            assistant_blocks.extend(final.content)
            if final.stop_reason == "pause_turn":
                pending_blocks.extend(final.content)
                continue
            if final.stop_reason == "tool_use" and tool_runner is not None and tool_rounds < MAX_TOOL_ROUNDS:
                tool_rounds += 1
                results = []
                for block in final.content:
                    if _attr(block, "type") != "tool_use":
                        continue
                    tool_calls += 1
                    try:
                        output = await tool_runner(block.name, dict(block.input or {}))
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                    except Exception as exc:  # noqa: BLE001 — xato Claude'ga qaytadi, u foydalanuvchiga tushuntiradi
                        log.warning("Vosita xatosi (%s): %s", block.name, exc)
                        results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": f"Xato: {exc}", "is_error": True}
                        )
                extra += pending_blocks and [{"role": "assistant", "content": list(pending_blocks)}] or []
                extra.append({"role": "assistant", "content": list(final.content)})
                extra.append({"role": "user", "content": results})
                pending_blocks = []
                if display and not display.endswith("\n"):
                    display += "\n\n"
                    last_type = None
                continue
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
            tool_calls=tool_calls,
        )

    async def classify(self, model: str, text: str, has_files: bool) -> tuple[str, dict[str, int], float]:
        """Avto-model uchun savol murakkabligi: simple / medium / complex (arzon model bilan, bir necha token)."""
        snippet = " ".join(text.split())[:1500] or "(matnsiz)"
        note = "Xabarga fayl yoki rasm biriktirilgan.\n" if has_files else ""
        prompt = (
            "Quyidagi foydalanuvchi so'rovini murakkablik bo'yicha tasnifla. Faqat bitta so'z bilan javob ber:\n"
            "simple — salomlashish, qisqa faktik savol, oddiy tarjima yoki qayta yozish, 1-2 jumlalik javob yetarli;\n"
            "medium — tushuntirish, matn yozish (post, xat, ro'yxat), fayl mazmuni bo'yicha savol, o'rtacha kod;\n"
            "complex — chuqur tahlil, ko'p bosqichli mantiq, hujjat tuzish, murakkab kod yoki hisob-kitob, tadqiqot.\n\n"
            f"{note}So'rov:\n{snippet}"
        )
        # Fikrlash max_tokens ga kiradi: joy qoldiramiz, Haiku'da esa fikrlashni o'chiramiz
        extra: dict[str, Any] = {"thinking": {"type": "disabled"}} if model in CLASSIFY_NO_THINKING else {}
        try:
            response = await self.client.messages.create(
                model=model, max_tokens=1024, output_config={"effort": "low"},
                messages=[{"role": "user", "content": prompt}], **extra,
            )
        except (anthropic.APIError, anthropic.APIConnectionError) as exc:
            log.warning("Tasniflash ishlamadi, o'rta model tanlanadi: %s", exc)
            return "medium", {}, 0.0
        try:
            answer = blocks_to_text(response.content).strip().lower()
            numbers = usage_numbers(response.usage, self.cache_ttl)
        except (AttributeError, TypeError) as exc:
            log.warning("Tasniflash javobi tushunarsiz, o'rta model tanlanadi: %s", exc)
            return "medium", {}, 0.0
        level = next((name for name in ("simple", "medium", "complex") if name in answer), None)
        if level is None:
            log.warning("Tasniflash javobsiz (stop_reason=%s), o'rta model tanlanadi", _attr(response, "stop_reason"))
            level = "medium"
        return level, numbers, cost_usd(model, numbers)

    async def complete_json(
        self, model: str, system: str, payload: str, max_tokens: int = 4000
    ) -> tuple[Any, dict[str, int], float]:
        """Bitta oqimsiz so'rov, javobdan JSON (massiv yoki obyekt) ajratiladi. JSON bo'lmasa — None.

        Hisobot tahlili uchun (izohlarni tasniflash): fikrlash o'chiq (Haiku), effort past."""
        extra: dict[str, Any] = {"thinking": {"type": "disabled"}} if model in CLASSIFY_NO_THINKING else {}
        try:
            response = await self.client.messages.create(
                model=model, max_tokens=max_tokens, system=system, output_config={"effort": "low"},
                messages=[{"role": "user", "content": payload}], **extra,
            )
        except anthropic.APIStatusError as exc:
            raise translate_error(exc, False) from exc
        except (anthropic.APITimeoutError, httpx2.TimeoutException) as exc:
            raise ClaudeError("⏱ Claude javob berishga ulgurmadi.", retryable=True) from exc
        except (anthropic.APIConnectionError, httpx2.TransportError) as exc:
            raise ClaudeError("🌐 Anthropic serveriga ulanib bo'lmadi.", retryable=True) from exc
        numbers = usage_numbers(response.usage, self.cache_ttl)
        cost = cost_usd(model, numbers)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", blocks_to_text(response.content).strip())
        starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
        if not starts:
            return None, numbers, cost
        start = min(starts)
        end = text.rfind("]" if text[start] == "[" else "}")
        try:
            parsed = json.loads(text[start : end + 1]) if end > start else None
        except json.JSONDecodeError:
            parsed = None
        return parsed, numbers, cost

    async def translate_items(
        self, model: str, target: str, glossary: str | None, items: list[str]
    ) -> tuple[list[str], dict[str, int], float]:
        """Hujjat bo'laklarini (abzatslar) JSON massiv ko'rinishida tarjima qiladi — tartib va soni saqlanadi."""
        system = (
            "Sen professional hujjat tarjimonisan. Foydalanuvchi JSON massivda matn bo'laklarini beradi. "
            f"Har bir bo'lakni {target} tiliga tarjima qil va FAQAT shu uzunlikdagi JSON massiv (satrlar) qaytar — "
            "izohsiz, kod blokisiz. Bo'laklar tartibi va soni o'zgarmasin. Sanalar, raqamlar, ismlar, kodlar va "
            "URL'larni saqla; rasmiy uslub va atamalar izchilligini saqla; faqat raqam yoki belgi bo'lgan "
            "bo'lakni o'zgarishsiz qaytar."
        )
        if glossary and glossary.strip():
            system += f"\n\nQo'shimcha ko'rsatmalar va lug'at:\n{glossary.strip()[:4000]}"
        payload = json.dumps(items, ensure_ascii=False)
        try:
            response = await self.client.messages.create(
                model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": payload}]
            )
        except anthropic.APIStatusError as exc:
            raise translate_error(exc, False) from exc
        except anthropic.APITimeoutError as exc:
            raise ClaudeError("⏱ Tarjima vaqti tugadi. Qayta urinib ko'ring.") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeError("🌐 Anthropic serveriga ulanib bo'lmadi.") from exc
        # Narx tahlildan oldin: javob yaroqsiz bo'lsa ham to'langan chaqiruv usage_log ga tushadi
        numbers = usage_numbers(response.usage, self.cache_ttl)
        cost = cost_usd(model, numbers)
        text = blocks_to_text(response.content).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        start, end = text.find("["), text.rfind("]")
        try:
            parsed = json.loads(text[start : end + 1]) if start >= 0 and end > start else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, list):
            # Bo'sh ro'yxat: chaqiruvchi narxni yozadi, doctrans esa «soni mos kelmadi» xatosini beradi
            log.warning("Tarjima javobi JSON massiv emas (stop_reason=%s)", _attr(response, "stop_reason"))
            return [], numbers, cost
        return [str(x) if x is not None else "" for x in parsed], numbers, cost

    def estimate_partial(self, model: str, start_usage: Any, partial_text: str) -> tuple[dict[str, int], float]:
        """Javob to'xtatilganda taxminiy narx: kirish aniq, chiqish matn uzunligidan taxminan."""
        numbers = usage_numbers(start_usage, self.cache_ttl) if start_usage is not None else {}
        numbers = dict(numbers)
        numbers["output"] = max(numbers.get("output", 0), len(partial_text) // 3)
        return numbers, cost_usd(model, numbers)

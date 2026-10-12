"""Claude modellari va ularning narxlari.

Narxlar ($ / 1M token) rasmiy sahifadan olingan (2026-10-12):
https://platform.claude.com/docs/en/about-claude/pricing
Narx o'zgarsa, faqat shu faylni yangilang.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rates:
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    note: str
    rates: Rates
    # Haiku 5.5 da 100 000 tokendan uzun promptlar qimmatroq narxda hisoblanadi
    long_prompt_threshold: int | None = None
    long_rates: Rates | None = None

    def rates_for(self, prompt_tokens: int) -> Rates:
        if (
            self.long_rates is not None
            and self.long_prompt_threshold is not None
            and prompt_tokens > self.long_prompt_threshold
        ):
            return self.long_rates
        return self.rates


MODELS: dict[str, ModelSpec] = {
    spec.id: spec
    for spec in (
        ModelSpec("claude-fable-5-1", "Fable 5.1", "eng kuchli, sekinroq", Rates(10, 50, 12.5, 20, 0.25)),
        ModelSpec("claude-opus-5-5", "Opus 5.5", "kuchli, o'rtacha tezlik", Rates(4, 20, 5, 8, 0.20)),
        ModelSpec("claude-sonnet-5-5", "Sonnet 5.5", "tez va arzonroq", Rates(2, 10, 2.5, 4, 0.10)),
        ModelSpec(
            "claude-haiku-5-5",
            "Haiku 5.5",
            "eng tez va arzon",
            Rates(0.10, 0.50, 0.125, 0.20, 0.01),
            long_prompt_threshold=100_000,
            long_rates=Rates(0.50, 2.50, 0.625, 1.0, 0.05),
        ),
        # Zaxira: boshqa model so'rovni rad etsa, javobni shu model beradi
        ModelSpec("claude-opus-4-8", "Opus 4.8", "zaxira model", Rates(5, 25, 6.25, 10, 0.50)),
    )
}

# /model menyusida ko'rinadiganlar
SELECTABLE_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")

WEB_SEARCH_USD = 10 / 1000  # $10 / 1000 qidiruv


def label(model_id: str) -> str:
    spec = MODELS.get(model_id)
    return spec.label if spec else model_id


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _num(obj: Any, name: str) -> int:
    return int(_get(obj, name) or 0)


def usage_numbers(usage: Any, cache_ttl: str = "5m") -> dict[str, int]:
    """API qaytargan usage obyektidan kerakli sonlarni ajratadi."""
    cache_write = _num(usage, "cache_creation_input_tokens")
    breakdown = _get(usage, "cache_creation")
    if breakdown is not None:
        write_1h = _num(breakdown, "ephemeral_1h_input_tokens")
        write_5m = max(cache_write - write_1h, 0)
    elif cache_ttl == "1h":
        write_1h, write_5m = cache_write, 0
    else:
        write_1h, write_5m = 0, cache_write
    return {
        "input": _num(usage, "input_tokens"),
        "output": _num(usage, "output_tokens"),  # fikrlash tokenlari ham shu ichida
        "thinking": _num(_get(usage, "output_tokens_details"), "thinking_tokens"),
        "cache_write": cache_write,
        "cache_write_5m": write_5m,
        "cache_write_1h": write_1h,
        "cache_read": _num(usage, "cache_read_input_tokens"),
        "searches": _num(_get(usage, "server_tool_use"), "web_search_requests"),
    }


def cost_usd(model_id: str, numbers: dict[str, int]) -> float:
    """Bitta API so'rovining narxi (USD)."""
    spec = MODELS.get(model_id)
    if spec is None:
        return 0.0
    prompt_tokens = numbers.get("input", 0) + numbers.get("cache_read", 0) + numbers.get("cache_write", 0)
    r = spec.rates_for(prompt_tokens)
    tokens = (
        numbers.get("input", 0) * r.input
        + numbers.get("output", 0) * r.output
        + numbers.get("cache_write_5m", 0) * r.cache_write_5m
        + numbers.get("cache_write_1h", 0) * r.cache_write_1h
        + numbers.get("cache_read", 0) * r.cache_read
    ) / 1_000_000
    return tokens + numbers.get("searches", 0) * WEB_SEARCH_USD

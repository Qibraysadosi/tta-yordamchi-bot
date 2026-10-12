"""Sozlamalar — hammasi .env faylidan (muhit o'zgaruvchilaridan) o'qiladi."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import MODELS, SELECTABLE_MODELS

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
WEB_SEARCH_TOOLS = ("web_search_20250305", "web_search_20260209", "web_search_20260318")


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} butun son bo'lishi kerak (hozir: {raw!r})") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} {low}…{high} oralig'ida bo'lishi kerak (hozir: {value})")
    return value


def _float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} son bo'lishi kerak (hozir: {raw!r})") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "ha")


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    anthropic_api_key: str
    owner_ids: frozenset[int]
    default_model: str
    fallback_model: str | None
    max_tokens: int
    web_search_default: bool
    web_search_tool: str
    web_search_max_uses: int
    search_city: str
    search_country: str
    cache_ttl: str
    history_max_messages: int
    long_context_tokens: int
    image_max_edge: int
    monthly_budget: float
    budget_reset_day: int
    timezone: str
    db_path: Path
    system_prompt_path: Path


def load_settings() -> Settings:
    token = _env("TELEGRAM_BOT_TOKEN")
    api_key = _env("ANTHROPIC_API_KEY")
    owners_raw = _env("OWNER_IDS")
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("ANTHROPIC_API_KEY", api_key),
            ("OWNER_IDS", owners_raw),
        )
        if not value
    ]
    if missing:
        raise ConfigError(".env faylida to'ldirilmagan: " + ", ".join(missing))

    try:
        owner_ids = frozenset(int(x) for x in owners_raw.replace(" ", "").split(",") if x)
    except ValueError as exc:
        raise ConfigError("OWNER_IDS faqat raqamlar bo'lishi kerak, masalan: 123456789,987654321") from exc
    if not owner_ids:
        raise ConfigError("OWNER_IDS bo'sh")

    default_model = _env("DEFAULT_MODEL", "claude-opus-5-5")
    if default_model not in SELECTABLE_MODELS:
        raise ConfigError(f"DEFAULT_MODEL quyidagilardan biri bo'lsin: {', '.join(SELECTABLE_MODELS)}")

    fallback_model = _env("FALLBACK_MODEL", "claude-opus-4-8")
    if fallback_model.lower() in ("off", "none", "0", "yoq", "yo'q"):
        fallback_model = None
    elif fallback_model not in MODELS:
        raise ConfigError(f"FALLBACK_MODEL quyidagilardan biri bo'lsin: {', '.join(MODELS)} yoki off")

    web_search_tool = _env("WEB_SEARCH_TOOL", "web_search_20250305")
    if web_search_tool not in WEB_SEARCH_TOOLS:
        raise ConfigError(f"WEB_SEARCH_TOOL quyidagilardan biri bo'lsin: {', '.join(WEB_SEARCH_TOOLS)}")

    cache_ttl = _env("CACHE_TTL", "5m")
    if cache_ttl not in ("5m", "1h"):
        raise ConfigError("CACHE_TTL faqat 5m yoki 1h bo'lishi mumkin")

    return Settings(
        telegram_token=token,
        anthropic_api_key=api_key,
        owner_ids=owner_ids,
        default_model=default_model,
        fallback_model=fallback_model,
        max_tokens=_int("MAX_TOKENS", 32000, 1024, 128000),
        web_search_default=_bool("WEB_SEARCH", True),
        web_search_tool=web_search_tool,
        web_search_max_uses=_int("WEB_SEARCH_MAX_USES", 5, 1, 20),
        search_city=_env("SEARCH_CITY", "Tashkent"),
        search_country=_env("SEARCH_COUNTRY", "UZ"),
        cache_ttl=cache_ttl,
        history_max_messages=_int("HISTORY_MAX_MESSAGES", 40, 4, 1000),
        long_context_tokens=_int("LONG_CONTEXT_WARN_TOKENS", 80000, 1000, 1_000_000),
        image_max_edge=_int("IMAGE_MAX_EDGE", 1568, 256, 2576),
        monthly_budget=_float("MONTHLY_BUDGET", 100.0),
        budget_reset_day=_int("BUDGET_RESET_DAY", 1, 1, 28),
        timezone=_env("TIMEZONE", "Asia/Tashkent"),
        db_path=Path(_env("DB_PATH", "data/bot.db")),
        system_prompt_path=Path(_env("SYSTEM_PROMPT_FILE", "prompts/system.md")),
    )

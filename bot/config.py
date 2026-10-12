"""Sozlamalar — hammasi .env faylidan (muhit o'zgaruvchilaridan) o'qiladi."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import AUTO_MODEL, MODELS, SELECTABLE_MODELS

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
    budget_economy_percent: int  # kredit shu foizga yetganda hamma tejamkor modelga o'tadi (0 — o'chiq)
    economy_model: str
    auto_models: tuple[str, str, str]  # avto-model: oddiy / o'rta / murakkab savollar uchun
    quick_model: str  # tasniflash, qisqartirish, tarjima kabi tez ishlar uchun arzon model
    link_reading: bool
    link_max_bytes: int
    backup_time: str | None  # kunlik zaxira vaqti (HH:MM), None — o'chiq
    webapp_url: str | None  # Mini App'ning ochiq (HTTPS) manzili; None — ilova o'chiq
    webapp_port: int  # konteyner ichida Mini App serveri tinglaydigan port
    files_max_bytes: int  # har foydalanuvchi uchun saqlanadigan yaratilgan fayllar hajmi
    timezone: str
    db_path: Path
    system_prompt_path: Path
    modes_dir: Path


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
    if default_model not in SELECTABLE_MODELS and default_model != AUTO_MODEL:
        raise ConfigError(f"DEFAULT_MODEL quyidagilardan biri bo'lsin: {AUTO_MODEL}, {', '.join(SELECTABLE_MODELS)}")

    auto_raw = _env("AUTO_MODELS", "claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5")
    auto_models = tuple(x.strip() for x in auto_raw.split(",") if x.strip())
    if len(auto_models) != 3 or any(m not in MODELS for m in auto_models):
        raise ConfigError("AUTO_MODELS vergul bilan 3 ta model bo'lsin: arzon,o'rta,kuchli")

    quick_model = _env("QUICK_MODEL", "claude-haiku-5-5")
    economy_model = _env("ECONOMY_MODEL", "claude-haiku-5-5")
    for name, value in (("QUICK_MODEL", quick_model), ("ECONOMY_MODEL", economy_model)):
        if value not in MODELS:
            raise ConfigError(f"{name} quyidagilardan biri bo'lsin: {', '.join(MODELS)}")

    backup_time: str | None = _env("BACKUP_TIME", "03:30")
    if backup_time.lower() in ("off", "none", "0", "yoq", "yo'q"):
        backup_time = None
    else:
        try:
            hours, minutes = backup_time.split(":")
            if not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
                raise ValueError
        except ValueError as exc:
            raise ConfigError("BACKUP_TIME HH:MM ko'rinishida bo'lsin (masalan 03:30) yoki off") from exc

    webapp_url: str | None = _env("WEBAPP_URL")
    if webapp_url is not None:
        if webapp_url.lower() in ("off", "none", "0", "yoq", "yo'q"):
            webapp_url = None
        elif not webapp_url.startswith("https://"):
            raise ConfigError("WEBAPP_URL https:// bilan boshlanishi kerak (Telegram Mini App faqat HTTPS'da ochiladi)")
        elif not webapp_url.endswith("/"):
            webapp_url += "/"

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
        budget_economy_percent=_int("BUDGET_ECONOMY_PERCENT", 90, 0, 100),
        economy_model=economy_model,
        auto_models=auto_models,  # type: ignore[arg-type]
        quick_model=quick_model,
        link_reading=_bool("LINK_READING", True),
        link_max_bytes=_int("LINK_MAX_MB", 8, 1, 20) * 1024 * 1024,
        backup_time=backup_time,
        webapp_url=webapp_url,
        webapp_port=_int("WEBAPP_PORT", 8091, 1, 65535),
        files_max_bytes=_int("FILES_MAX_MB", 300, 10, 10_000) * 1024 * 1024,
        timezone=_env("TIMEZONE", "Asia/Tashkent"),
        db_path=Path(_env("DB_PATH", "data/bot.db")),
        system_prompt_path=Path(_env("SYSTEM_PROMPT_FILE", "prompts/system.md")),
        modes_dir=Path(_env("MODES_DIR", "prompts/modes")),
    )

"""Ish rejimlari: har biri tizim ko'rsatmasiga qo'shimcha matn (prompts/modes/<kalit>.md)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    emoji: str
    hint: str  # /rejim ro'yxatidagi bir qatorli izoh


DEFAULT_MODE = "oddiy"

MODES: dict[str, Mode] = {
    m.key: m
    for m in (
        Mode("oddiy", "Oddiy", "💬", "umumiy yordamchi — har qanday savol"),
        Mode("rasmiy_xat", "Rasmiy xat", "📝", "tashkilot nomidan xat, ariza, bildirishnoma"),
        Mode("instagram", "Instagram post", "📸", "post matni, sarlavha, heshteglar"),
        Mode("meta_reklama", "Meta reklama", "📣", "reklama matni va auditoriya variantlari"),
        Mode("tarjimon", "Tarjimon", "🌐", "yuborilgan matnni tarjima qiladi, izohsiz"),
        Mode("dasturchi", "Dasturchi", "💻", "kod, xatolar, texnik tushuntirish"),
    )
}


def mode_of(key: str | None) -> Mode:
    return MODES.get(key or DEFAULT_MODE, MODES[DEFAULT_MODE])


def mode_label(key: str | None) -> str:
    m = mode_of(key)
    return f"{m.emoji} {m.label}"


def load_mode_prompt(modes_dir: Path, key: str | None) -> str:
    """Rejim ko'rsatmasi matni; oddiy rejimda yoki fayl bo'lmasa — bo'sh."""
    m = mode_of(key)
    if m.key == DEFAULT_MODE:
        return ""
    path = modes_dir / f"{m.key}.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

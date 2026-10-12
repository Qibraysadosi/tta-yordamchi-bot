"""Tizim ko'rsatmasi (system prompt): prompts/system.md faylidan o'qiladi, {sana} o'rniga bugungi sana qo'yiladi."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]
DEFAULT_PROMPT = "Sen foydali AI yordamchisan. Foydalanuvchi qaysi tilda yozsa, o'sha tilda javob ber. Bugungi sana: {sana}."


def uz_date(moment: datetime) -> str:
    return f"{moment.day}-{MONTHS[moment.month - 1]} {moment.year}-yil, {WEEKDAYS[moment.weekday()]}"


class PromptLoader:
    """Faylni har so'rovda tekshiradi: tahrirlasangiz, botni qayta ishga tushirish shart emas."""

    def __init__(self, path: Path, timezone: str) -> None:
        self.path = path
        self.tz = ZoneInfo(timezone)
        self._cache: str | None = None
        self._mtime: float | None = None

    def _read(self) -> str:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return DEFAULT_PROMPT
        if self._cache is None or mtime != self._mtime:
            self._cache = self.path.read_text(encoding="utf-8")
            self._mtime = mtime
        return self._cache

    def build(self, now: datetime | None = None, extra: str = "") -> str:
        # Faqat sana (vaqt emas) — kun davomida ko'rsatma o'zgarmaydi va kesh ishlayveradi
        moment = now or datetime.now(self.tz)
        text = self._read().replace("{sana}", uz_date(moment)).strip()
        return f"{text}\n\n{extra.strip()}" if extra.strip() else text

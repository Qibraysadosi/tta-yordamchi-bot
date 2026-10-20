"""Lid kartochkasini o'qish: ism, telefon(lar), yosh, tuman, kurs; telefon raqamlarini yashirish.

Kartochkalar ikki xil ko'rinishda keladi (kalit: qiymat qatorlari):
  «Ism / Telefon raqami / Yoshi / Tumani / Kurs» va
  «YANGI ARIZA: Ismi / Yoshi / Raqami / Tuman / Tanlagan kursi + Qo'shimcha aloqa: ISM | p:+998…».
Raqamlar 998 bilan yoki usiz, bo'shliq, tire, nuqta bilan yozilishi mumkin. AI ishlatilmaydi — raqamlar
Claude'ga hech qachon yuborilmaydi."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

APOSTROPHES = "'ʻʼ‘’`´"
_APOS = str.maketrans({c: "'" for c in APOSTROPHES})

# Raqam: (+998) 90 123-45-67, 998901234567, 90 123 45 67, 901234567 …
PHONE_RE = re.compile(
    r"(?<![\d+])(?:\+?\s?998[\s\-.()]*)?\(?\d{2}\)?[\s\-.]*\d{3}[\s\-.]*\d{2}[\s\-.]*\d{2}(?!\d)"
)

# Kalit so'z (kichik harf, apostrof bir xil) → maydon. Tartib muhim: birinchi mos kelgani olinadi.
KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("extra", ("qo'shimcha", "qoshimcha", "доп")),
    ("phone", ("telefon", "raqam", "nomer", "tel", "телефон", "номер")),
    ("name", ("ism", "f.i.sh", "fish", "fio", "имя", "ф.и.о", "фио")),
    ("age", ("yosh", "возраст")),
    ("district", ("tuman", "hudud", "manzil", "район", "адрес")),
    ("course", ("kurs", "yo'nalish", "yonalish", "kasb", "курс", "направление")),
)
SEPARATORS = re.compile(r"\s*(?::|—|–|\s-\s)\s*")
VALUE_LIMIT = 80


@dataclass
class LeadInfo:
    name: str = ""
    phones: list[str] = field(default_factory=list)
    age: str = ""
    district: str = ""
    course: str = ""
    keys: int = 0  # nechta tanish kalit topildi

    @property
    def looks_like_lead(self) -> bool:
        """Qo'lda yozilgan kartochka: raqam bor va kamida ism yoki kurs."""
        return bool(self.phones) and bool(self.name or self.course)


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return None


def extract_phones(text: str) -> list[str]:
    phones: list[str] = []
    for match in PHONE_RE.finditer(text or ""):
        phone = normalize_phone(match.group(0))
        if phone and phone not in phones:
            phones.append(phone)
    return phones


def mask_phones(text: str) -> str:
    """Claude'ga yuboriladigan matndan telefon raqamlarini olib tashlaydi."""
    return PHONE_RE.sub("[raqam]", text or "")


def _clean_key(key: str) -> str:
    key = key.translate(_APOS).lower()
    key = re.sub(r"[^\w\s'.]", " ", key)  # emoji va belgilar
    return " ".join(key.split())


def _clean_value(value: str) -> str:
    value = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", value)  # emoji
    return " ".join(value.split())[:VALUE_LIMIT].strip(" ,;|")


def _field_for(key: str) -> str | None:
    if not key or len(key.split()) > 4:
        return None
    for name, words in KEYS:
        if any(word in key for word in words):
            return name
    return None


def parse_lead(text: str) -> LeadInfo:
    info = LeadInfo(phones=extract_phones(text))
    for line in (text or "").splitlines():
        parts = SEPARATORS.split(line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        name = _field_for(_clean_key(parts[0]))
        if name is None:
            continue
        info.keys += 1
        value = _clean_value(parts[1])
        if name in ("phone", "extra"):
            continue  # raqamlar butun matndan olinadi
        if value and not getattr(info, name):
            setattr(info, name, value)
    return info


def message_link(chat_id: int, message_id: int) -> str | None:
    """Superguruhdagi xabarga havola (guruh a'zolari uchun ishlaydi)."""
    text = str(chat_id)
    if not text.startswith("-100"):
        return None
    return f"https://t.me/c/{text[4:]}/{message_id}"

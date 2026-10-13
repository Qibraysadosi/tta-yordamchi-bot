"""O'zbek lotin ↔ kirill transliteratsiyasi (rasmiy alifbo qoidalari asosida)."""
from __future__ import annotations

import re

APOSTROPHES = "'’ʻʼ`‘"
VOWELS_LATIN = set("aeiouAEIOU")
VOWELS_CYR = set("аеёиоуўэюяАЕЁИОУЎЭЮЯ")

# Lotin → kirill: uzun birikmalar birinchi tekshiriladi
_LAT_PAIRS = [("tsiya", "ция"), ("tsion", "цион")]
# Barcha tutuq belgilari bir xil ishlaydi: yo‘q, o`g`il, oʻzbek
_LAT_PAIRS += [(lat + a, cyr) for lat, cyr in (("yo", "йў"), ("o", "ў"), ("g", "ғ")) for a in APOSTROPHES]
_LAT_PAIRS += [("sh", "ш"), ("ch", "ч"), ("ya", "я"), ("yu", "ю"), ("yo", "ё"), ("ye", "е")]
_LAT_SINGLE = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к",
    "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "x": "х", "y": "й", "z": "з", "c": "ц", "w": "в",
}
_CYR_PAIRS = {
    "ў": "o'", "ғ": "g'", "ш": "sh", "ч": "ch", "я": "ya", "ю": "yu", "ё": "yo", "щ": "sh",
    "э": "e", "ъ": "'", "ь": "", "й": "y", "ж": "j", "х": "x", "ҳ": "h", "қ": "q",
}
_CYR_SINGLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "з": "z", "и": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "ы": "i",
}
# O'zgarmaydigan qismlar: kod, havola, e-pochta, sayt nomi, @nom, #teg.
# Harfga yopishgan ` — tutuq belgisi (O`zbekiston), kod emas.
_CODE = re.compile(
    r"(```.*?```|(?<![^\W\d_])`[^`\n]+`|https?://\S+"
    r"|(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?:uz|com|ru|org|net|io|info)\b(?:/[^\s,;)]*)?"
    r"|www\.[\w-]+(?:\.[\w-]+)+(?:/[^\s,;)]*)?|@\w+|#\w+)",
    re.DOTALL,
)
_A = re.escape(APOSTROPHES)
# So'z oxiridagi tutuq faqat g dan keyin so'zga qo'shiladi (tog' → тоғ, 'radio' esa o'zgarmaydi)
_WORD = re.compile(rf"[^\W\d_]+(?:[{_A}]+[^\W\d_]+)*(?:(?<=[gG])[{_A}])?|[{_A}]")


def _case(sample: str, out: str, next_char: str = "", prev_char: str = "") -> str:
    """Natijaga manba harfining registrini beradi (SH → Ш, Sh → Ш, sh → ш; Ш. → Sh., АҚШ → AQSH)."""
    if sample.isupper() and (
        len(sample) > 1 or next_char.isupper() or (not next_char.isalpha() and prev_char.isupper())
    ):
        return out.upper()
    if sample[:1].isupper():
        return out[:1].upper() + out[1:]
    return out


def _latin_word_to_cyrillic(word: str) -> str:
    out: list[str] = []
    i = 0
    n = len(word)
    while i < n:
        matched = False
        low = word[i:].lower()
        for pair, cyr in _LAT_PAIRS:
            if low.startswith(pair):
                out.append(_case(word[i : i + len(pair)], cyr, word[i + len(pair) : i + len(pair) + 1]))
                i += len(pair)
                matched = True
                break
        if matched:
            continue
        ch = word[i]
        lower = ch.lower()
        if lower == "e":
            # So'z boshida va unlidan keyin — э (ekran, poema), qolgan joyda — е
            prev = out[-1] if out else ""
            cyr = "э" if (i == 0 or prev.lower() in "аеёиоуўэюя" or prev == "") else "е"
            out.append(_case(ch, cyr, word[i + 1 : i + 2]))
        elif ch in APOSTROPHES:
            # Tutuq belgisi (ma'no → маъно); unlidan keyin ham ъ
            out.append("ъ" if out and out[-1].isalpha() else ch)
        elif lower in _LAT_SINGLE:
            out.append(_case(ch, _LAT_SINGLE[lower], word[i + 1 : i + 2]))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _cyrillic_word_to_latin(word: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(word):
        lower = ch.lower()
        if lower == "е":
            prev = word[i - 1].lower() if i else ""
            lat = "ye" if (i == 0 or prev in "аеёиоуўэюяъь") else "e"
        elif lower == "ц":
            # So'z boshida va undoshdan keyin — s (sirk, aksiya), unlidan keyin — ts (politsiya)
            lat = "ts" if i and word[i - 1].lower() in "аеёиоуўэюяы" else "s"
        elif lower in _CYR_PAIRS:
            lat = _CYR_PAIRS[lower]
        elif lower in _CYR_SINGLE:
            lat = _CYR_SINGLE[lower]
        else:
            out.append(ch)
            continue
        out.append(_case(ch, lat, word[i + 1 : i + 2], word[i - 1] if i else ""))
    return "".join(out)


def _apply(text: str, convert) -> str:
    """Kod bloklari, havolalar, @nomlar va #teglar o'zgarmaydi; qolgan matn so'zma-so'z o'giriladi."""
    def word(m: re.Match) -> str:
        w = m.group(0)
        # 'pedagog' — oxirgi belgi yopuvchi qo'shtirnoq, tutuq emas
        if len(w) > 1 and w[-1] in "'’`‘" and m.start() and m.string[m.start() - 1] in "'’`‘":
            return convert(w[:-1]) + w[-1]
        return convert(w)

    parts = _CODE.split(text)
    for idx in range(0, len(parts), 2):
        parts[idx] = _WORD.sub(word, parts[idx])
    return "".join(parts)


def to_cyrillic(text: str) -> str:
    return _apply(text, _latin_word_to_cyrillic)


def to_latin(text: str) -> str:
    return _apply(text, _cyrillic_word_to_latin)


def looks_cyrillic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if "Ѐ" <= c <= "ӿ") > len(letters) / 2

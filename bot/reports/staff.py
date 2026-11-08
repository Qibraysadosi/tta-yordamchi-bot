"""Xodimlarni bitta xabar bilan qo'shish (v1.11, /xodimlar): egasi har qatorga «@username — Ism — rol» yozadi.

Rollar: operator (standart), admin — administrator (jadvalda operatorlar bilan, ismi yonida belgi), rahbar —
Hisobotlar bo'limiga kiradi, lidni o'zi belgilasa alohida qatorda sanaladi, istisno — hisobotda sanalmaydi,
o'chir — barcha ro'yxatlardan olib tashlash. Odam: @username, t.me havola yoki Telegram ID.

Username'i yo'q xodim — egasi uning kontaktini botga ulashadi (Telegram kontaktda ID bor): bot rol tugmalarini
chiqaradi (contact_prompt / contact_keyboard), ism — kontakt nomining birinchi so'zi."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiogram.exceptions import AiogramError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .store import ADMIN, PEOPLE_KINDS, Person, ReportStore, clean_username

if TYPE_CHECKING:
    from aiogram import Bot

    from .service import ReportService

log = logging.getLogger(__name__)

OPERATOR, LEADER, EXCLUDED, REMOVE = "operator", "leader", "excluded", "remove"
ROLE_WORDS = {
    **dict.fromkeys(("operator", "operatorlar", "op", "оператор"), OPERATOR),
    **dict.fromkeys(("admin", "adminlar", "administrator", "administratorlar", "adm", "админ", "администратор"), ADMIN),
    **dict.fromkeys(("rahbar", "rahbarlar", "leader", "boshliq", "раҳбар", "рахбар", "руководитель"), LEADER),
    **dict.fromkeys(("istisno", "istisnolar", "excluded", "истисно"), EXCLUDED),
    **dict.fromkeys(("o'chir", "o'chirish", "ochir", "ochirish", "uchir", "delete", "remove", "ўчир"), REMOVE),
}
ROLE_TEXT = {OPERATOR: "operator", ADMIN: "administrator", LEADER: "rahbar", EXCLUDED: "istisno"}
LABEL_WORDS = {"username", "user", "ism", "ismi", "rol", "roli", "lavozim", "lavozimi", "name", "role", "id",
               "telegram", "tg", "nick", "login", "akkaunt"}  # «Ism: Malika, Rol: operator» kabi yozuvlar
RESERVED = {"joinchat", "addstickers", "addemoji", "addlist", "addtheme", "share", "proxy", "socks", "boost",
            "setlanguage", "login", "invoice", "confirmphone"}  # t.me/… — odam emas
MAX_LINES = 100
NAME_LIMIT = 40
_APOS = str.maketrans({c: "'" for c in "ʻʼ‘’`´"})

TG_RE = re.compile(r"tg://user\?id=(\d{4,15})", re.I)
LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/([A-Za-z0-9_]{4,32})(?![A-Za-z0-9_])(?:[/?#]\S*)?", re.I)
AT_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{4,32})(?![A-Za-z0-9_])")
ID_RE = re.compile(r"(?<![\w])id\s*[:#=]?\s*(\d{5,15})(?!\d)", re.I)
NUM_RE = re.compile(r"(?<![\w+@/=.])(\d{5,15})(?![\w.])")
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
BULLET_RE = re.compile(r"^\s*(?:[-–—•*·▪►✅👉]+|\d{1,3}[.)])\s*")
SEP_RE = re.compile(r"\s*(?:[—–|;,:()\[\]/«»\"]|\s-\s|\t)\s*")


@dataclass
class Entry:
    line_no: int
    raw: str
    username: str | None = None
    user_id: int | None = None
    name: str = ""
    roles: set[str] = field(default_factory=set)
    explicit: bool = False  # rol yozilgan (yo'q bo'lsa — operator, mavjud lavozim o'zgarmaydi)
    error: str = ""

    @property
    def who(self) -> str:
        return f"@{self.username}" if self.username else f"ID {self.user_id}"


def _role(word: str) -> str | None:
    key = word.translate(_APOS).lower().strip(" .!-–—'\"«»")
    return ROLE_WORDS.get(key)


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _identify(line: str) -> tuple[str | None, int | None, tuple[int, int] | None, str]:
    """Qatordagi odam: (username, ID, matndagi o'rni, xato). Ustunlik: tg://, t.me havola, @username, ID."""
    match = TG_RE.search(line)
    if match:
        return None, int(match.group(1)), match.span(), ""
    for match in LINK_RE.finditer(line):
        name = clean_username(match.group(1))
        if name and name not in RESERVED:
            return name, None, match.span(), ""
    match = AT_RE.search(line)
    if match:
        name = clean_username(match.group(1))
        if name:
            return name, None, match.span(), ""
    match = ID_RE.search(line)
    if match:
        return None, int(match.group(1)), match.span(), ""
    match = NUM_RE.search(line)
    if match and not (match.group(1).startswith("998") and len(match.group(1)) == 12):
        return None, int(match.group(1)), match.span(), ""
    if PHONE_RE.search(line):
        return None, None, None, "telefon raqami bilan qo'shib bo'lmaydi — @username, t.me havola yoki Telegram ID yozing"
    return None, None, None, "@username, t.me havola yoki Telegram ID topilmadi"


def parse_line(line_no: int, raw: str) -> Entry | None:
    """Bitta qator → Entry; bo'sh qator — None."""
    line = BULLET_RE.sub("", raw.strip(), count=1).strip()
    words = [w.strip(" .'\"«»").lower() for chunk in SEP_RE.split(line) for w in chunk.split()]
    if not line or all(w in LABEL_WORDS for w in words if w):  # bo'sh yoki sarlavha: «Username — Ism — Rol»
        return None
    entry = Entry(line_no=line_no, raw=raw.strip())
    username, user_id, span, error = _identify(line)
    if span is None:
        entry.error = error
        return entry
    entry.username, entry.user_id = username, user_id
    rest = (line[: span[0]] + " | " + line[span[1]:]).strip()
    names: list[str] = []
    unknown = ""  # «@x — Ism — direktor»: oxirgi bo'lak rolga o'xshaydi, lekin noma'lum
    for part in SEP_RE.split(rest):
        part = part.strip(" -–—.'\"«»")
        if not part:
            continue
        role = _role(part)
        if role:
            entry.roles.add(role)
            continue
        if part.lower() in LABEL_WORDS:
            continue
        words = part.split()
        if len(words) > 1 and _role(words[-1]):  # «Malika operator» — ajratuvchisiz
            entry.roles.add(_role(words[-1]))  # type: ignore[arg-type]
            part = " ".join(words[:-1])
        names.append(part)
    if not entry.roles and len(names) > 1 and len(names[-1].split()) == 1:
        unknown = names[-1]
    entry.name = " ".join(" ".join(names).split())[:NAME_LIMIT].strip()
    entry.explicit = bool(entry.roles)
    if unknown:
        entry.error = (f"«{_short(unknown, 20)}» — noma'lum rol (operator, admin, rahbar, istisno yoki o'chir); "
                       "ism bir necha bo'lak bo'lsa, ajratuvchisiz yozing")
    elif REMOVE in entry.roles and len(entry.roles) > 1:
        entry.error = "«o'chir» boshqa rol bilan birga yozilmaydi"
    elif EXCLUDED in entry.roles and len(entry.roles) > 1:
        entry.error = "istisno boshqa rol bilan birga bo'lmaydi (istisno hech qayerda sanalmaydi)"
    if not entry.roles:
        entry.roles = {OPERATOR}
    return entry


def command_body(text: str) -> str:
    """«/xodimlar …» → buyruqdan keyingi matn (birinchi qatorning qolgani va keyingi qatorlar)."""
    text = (text or "").lstrip()
    if text.startswith("/"):
        first, _, rest = text.partition("\n")
        tail = first.split(maxsplit=1)[1] if len(first.split(maxsplit=1)) > 1 else ""
        return (tail + "\n" + rest).strip()
    return text.strip()


def parse(text: str) -> list[Entry]:
    entries = []
    for index, raw in enumerate((text or "").splitlines(), start=1):
        entry = parse_line(index, raw)
        if entry is not None:
            entries.append(entry)
    return entries


def _strong(entry: Entry) -> bool:
    """Ro'yxat qatoriga aniq o'xshaydi: rol yozilgan yoki ajratuvchi bor («@malika — Malika»)."""
    return entry.explicit or bool(SEP_RE.search(BULLET_RE.sub("", entry.raw, count=1)))


def looks_like_list(text: str) -> bool:
    """/xodimlar'dan keyingi xabar ro'yxatmi (aks holda — oddiy savol, AI suhbatiga ketadi): savol emas, kamida bitta
    aniq ro'yxat qatori bor va o'qilgan qatorlar o'qilmaganlardan kam emas."""
    text = text or ""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > MAX_LINES or text.lstrip().startswith("/"):
        return False
    if any(line.rstrip().endswith("?") for line in lines):
        return False
    entries = [e for e in (parse_line(i, line) for i, line in enumerate(lines, start=1)) if e is not None]
    good = [e for e in entries if not e.error]
    return any(_strong(e) for e in good) and len(good) >= len(entries) - len(good)


# ---------------------------------------------------------------------- qo'llash
@dataclass
class Outcome:
    lines: list[str] = field(default_factory=list)
    added: int = 0
    updated: int = 0
    failed: int = 0
    unseen: bool = False  # kimdir hali guruhda ko'rinmagan (ID bilan bog'lanmagan)


def _person_line(icon: str, name: str, role: str, who: str, status: str, notes: list[str]) -> str:
    tail = " · ".join([status] + notes)
    return f"{icon} <b>{html.escape(name)}</b> — {role} · {html.escape(who)} · {tail}"


async def _put(store: ReportStore, kind: str, entry: Entry, title: str | None) -> tuple[Person, str]:
    """Ro'yxatga qo'shadi yoki yangilaydi: (odam, «new» | «updated» | «same»)."""
    before = {p.id: p for p in await store.people(kind)}
    person = await store.add_person(kind, username=entry.username, user_id=entry.user_id, name=entry.name, title=title)
    old = before.get(person.id)
    if old is None:
        return person, "new"
    changed = old.name != person.name or old.title != person.title or old.username != person.username
    return person, "updated" if changed else "same"


async def apply(service: "ReportService", bot: "Bot", entries: list[Entry]) -> Outcome:
    store = service.store
    result = Outcome()
    for entry in entries[:MAX_LINES]:
        if entry.error:
            result.failed += 1
            result.lines.append(f"⚠️ {entry.line_no}-qator «{html.escape(_short(entry.raw))}» — {entry.error}")
            continue
        if REMOVE in entry.roles:
            removed: list[Person] = []
            for kind in PEOPLE_KINDS:
                removed += await store.drop_from(kind, username=entry.username, user_id=entry.user_id)
            label = removed[0].label if removed else entry.who
            result.updated += bool(removed)
            result.lines.append(f"🗑 <b>{html.escape(label)}</b> · {html.escape(entry.who)} · "
                                + ("ro'yxatlardan olib tashlandi" if removed else "ro'yxatlarda yo'q edi"))
            continue
        notes: list[str] = []
        states: list[str] = []
        shown: Person | None = None
        greet: Person | None = None  # yangi xodimga bitta salom (rahbar bo'lsa — Hisobotlar tugmasi bilan)
        staff = bool(entry.roles & {OPERATOR, ADMIN})
        if staff:
            title = ADMIN if ADMIN in entry.roles else ("" if entry.explicit else None)
            shown, state = await _put(store, "operator", entry, title)
            states.append(state)
            if state == "new":
                greet = shown
        if LEADER in entry.roles:
            leader, state = await _put(store, "leader", entry, None)
            states.append(state)
            shown = shown or leader
            if state == "new":
                greet = leader
            if not staff and await store.drop_from("operator", username=leader.username or entry.username,
                                                   user_id=leader.user_id):
                notes.append("operatorlar jadvalidan olindi")
        if EXCLUDED in entry.roles:
            shown, state = await _put(store, "excluded", entry, None)
            states.append(state)
            if await store.drop_from("operator", username=shown.username or entry.username, user_id=shown.user_id):
                notes.append("operatorlardan olindi")
        assert shown is not None
        if entry.roles & {OPERATOR, ADMIN, LEADER} and await store.drop_from(
            "excluded", username=shown.username or entry.username, user_id=shown.user_id
        ):
            notes.append("istisnodan olindi — endi sanaladi")
        moved = bool(notes)  # boshqa ro'yxatdan olindi — bu ham o'zgarish
        if shown.user_id is None:
            notes.append("guruhda hali ko'rinmagan")
            result.unseen = True
        if greet is not None:
            try:
                await service.welcome(bot, greet)  # botni avval ochgan bo'lsa — darrov ulanadi (eslatmalar)
            except AiogramError as exc:  # salom — ixtiyoriy
                log.info("Xodimga salom yuborilmadi: %s", exc)
        if "new" in states:
            result.added += 1
            icon, status = "✅", "qo'shildi"
        elif "updated" in states or moved:
            result.updated += 1
            icon, status = "✏️", "yangilandi"
        else:
            icon, status = "▫️", "o'zgarishsiz"
        roles = [ROLE_TEXT[r] for r in (ADMIN, OPERATOR, LEADER, EXCLUDED) if r in entry.roles]
        if ADMIN in entry.roles and OPERATOR in entry.roles:
            roles.remove("operator")
        if staff and not entry.explicit and shown.is_admin:
            roles = ["administrator"]
        who = f"@{shown.username}" if shown.username else f"ID {shown.user_id}"
        result.lines.append(_person_line(icon, shown.label, " + ".join(roles), who, status, notes))
    return result


def outcome_text(result: Outcome, skipped: int = 0) -> str:
    head = []
    if result.added:
        head.append(f"{result.added} ta qo'shildi")
    if result.updated:
        head.append(f"{result.updated} ta yangilandi")
    if result.failed:
        head.append(f"{result.failed} ta qator o'qilmadi")
    lines = ["👥 <b>Xodimlar</b>" + (": " + ", ".join(head) if head else " — o'zgarish yo'q"), ""] + result.lines
    if skipped:
        lines.append(f"… yana {skipped} ta qator o'qilmadi: bitta xabarda ko'pi bilan {MAX_LINES} ta odam")
    lines += ["", "Hisobotda: operator va administratorlar — jadvalda (administrator ismi yonida *), rahbarlar — "
                  "alohida qatorda ismi bilan."
              + (" «Guruhda hali ko'rinmagan» — birinchi reaksiya yoki izohidan keyin o'zi bog'lanadi." if result.unseen else "")
              + " Ro'yxat: /xodimlar"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- joriy ro'yxat
EXAMPLE = (
    "<pre>@malika_op — Malika — operator\n"
    "@dilnoza_admin — Dilnoza — admin\n"
    "@akmal_boss — Akmal aka — rahbar</pre>"
)
HELP = (
    "✍️ <b>Qo'shish yoki o'zgartirish</b> — keyingi xabarda har qatorga bitta odam (yoki /xodimlar bilan bir xabarda):\n"
    + EXAMPLE + "\n"
    "Rollar: <b>operator</b>, <b>admin</b> (administrator — jadvalda operatorlar bilan), <b>rahbar</b> (Hisobotlar "
    "bo'limiga kiradi; lidni o'zi belgilasa — alohida qatorda sanaladi), <b>istisno</b> (sanalmaydi), "
    "<b>o'chir</b>. Rol yozilmasa — operator.\n"
    "📇 <b>Username'i yo'q bo'lsa</b> — xodim profilida «Поделиться контактом» (Kontaktni ulashish) → shu botni "
    "tanlang → rol tugmasini bosing. t.me havola yoki Telegram ID ham bo'ladi."
)
CONTACT_ROLES = {"op": OPERATOR, "ad": ADMIN, "le": LEADER, "ex": EXCLUDED}
KIND_TEXT = {"operator": "operator", "leader": "rahbar", "excluded": "istisno"}


def contact_name(*parts: str | None) -> str:
    """Kontakt nomidan hisobotdagi ism — birinchi so'z: «Asal Ta'lim akademiyasi 🇺🇿» → «Asal»."""
    for word in " ".join(p or "" for p in parts).split():
        word = word.strip(".,;:!?\"'«»()[]")
        if any(ch.isalpha() for ch in word):
            return word[:NAME_LIMIT]
    return ""


def contact_keyboard(user_id: int) -> InlineKeyboardMarkup:
    def button(text: str, code: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text, callback_data=f"sc:{user_id}:{code}")
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("🎧 Operator", "op"), button("⭐ Administrator", "ad")],
        [button("👔 Rahbar", "le"), button("🚫 Istisno", "ex")],
    ])


async def listed_as(store: ReportStore, user_id: int, username: str | None) -> list[Person]:
    return [p for p in await store.people() if p.same(username, user_id)]


async def contact_prompt(store: ReportStore, user_id: int, username: str | None, full_name: str, name: str) -> str:
    lines = [f"📇 <b>{html.escape(full_name or 'Kontakt')}</b> · ID {user_id}"
             + (f" · @{html.escape(username)}" if username else "")]
    current = await listed_as(store, user_id, username)
    if current:
        lines.append("Hozir: " + ", ".join(
            f"{'administrator' if p.is_admin else KIND_TEXT.get(p.kind, p.kind)} «{html.escape(p.label)}»" for p in current))
    lines.append(f"Hisobotda ismi: <b>{html.escape(name or f'ID {user_id}')}</b> — kim bo'lib chiqsin?")
    lines.append(f"<i>Boshqa ism kerak bo'lsa: /xodimlar va «ID {user_id} — Ism — rol»</i>")
    return "\n".join(lines)


def _listed(person: Person) -> str:
    text = f"• {html.escape(person.label)}"
    if person.name:
        text += " — " + (f"@{html.escape(person.username)}" if person.username else f"ID {person.user_id}")
    if person.is_admin:
        text += " · <b>admin</b>"
    if person.user_id is None:
        text += " · <i>hali ko'rinmagan</i>"
    return text


async def list_text(store: ReportStore) -> str:
    people = await store.people()
    groups = (
        ("🎧", "Operatorlar va administratorlar", [p for p in people if p.kind == "operator"]),
        ("👔", "Rahbarlar", [p for p in people if p.kind == "leader"]),
        ("🚫", "Istisnolar", [p for p in people if p.kind == "excluded"]),
    )
    lines = ["👥 <b>Xodimlar</b> — hisobotlarda shu ismlar bilan chiqadi", ""]
    for icon, title, items in groups:
        lines.append(f"{icon} <b>{title}</b> ({len(items)})")
        lines += [_listed(p) for p in items] or ["—"]
    lines += ["", HELP]
    return "\n".join(lines)

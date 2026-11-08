"""Xodimlarni bitta xabar bilan qo'shish (v1.11, /xodimlar): egasi har qatorga «@username — Ism — rol» yozadi.

Rollar: operator (standart), admin — administrator (jadvalda operatorlar bilan, ismi yonida belgi), rahbar —
Hisobotlar bo'limiga kiradi, lidni o'zi belgilasa alohida qatorda sanaladi, istisno — hisobotda sanalmaydi,
o'chir — barcha ro'yxatlardan olib tashlash. Boshqa so'z (v1.12) — bo'lim nomi («Marketing», «Menejer»): lidni
belgilasa — bo'limi bilan alohida qatorda. Odam: @username, t.me havola yoki Telegram ID; bir qatorda bir nechta
akkaunt («@shaxsiy @ish — Ism — rol»). Bir ro'yxatda bir xil ismli akkauntlar hisobotda bitta odam.

Username'i yo'q xodim — egasi uning kontaktini botga ulashadi yoki /xodimlar'dan keyin uning xabarini uzatadi
(forward): bot rol, bo'lim va «kimning akkaunti» tugmalarini chiqaradi (contact_prompt / contact_keyboard)."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from aiogram.exceptions import AiogramError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import translit
from .store import ADMIN, PEOPLE_KINDS, Person, ReportStore, clean_dept, clean_username, name_key

if TYPE_CHECKING:
    from aiogram import Bot

    from .service import ReportService

log = logging.getLogger(__name__)

OPERATOR, LEADER, EXCLUDED, REMOVE, STAFF = "operator", "leader", "excluded", "remove", "staff"
ROLE_WORDS = {
    **dict.fromkeys(("operator", "operatorlar", "op", "оператор"), OPERATOR),
    **dict.fromkeys(("admin", "adminlar", "administrator", "administratorlar", "adm", "админ", "администратор"), ADMIN),
    **dict.fromkeys(("rahbar", "rahbarlar", "rahbariyat", "leader", "boshliq", "direktor", "direktorlar", "director",
                     "раҳбар", "рахбар", "раҳбарият", "директор", "руководитель"), LEADER),
    **dict.fromkeys(("istisno", "istisnolar", "excluded", "истисно"), EXCLUDED),
    **dict.fromkeys(("o'chir", "o'chirish", "ochir", "ochirish", "uchir", "delete", "remove", "ўчир"), REMOVE),
}
ROLE_TEXT = {OPERATOR: "operator", ADMIN: "administrator", LEADER: "rahbar", EXCLUDED: "istisno"}
KIND_TEXT = {"operator": "operator", "leader": "rahbar", "excluded": "istisno", "staff": "bo'lim"}
LABEL_WORDS = {"username", "user", "ism", "ismi", "rol", "roli", "lavozim", "lavozimi", "name", "role", "id",
               "telegram", "tg", "nick", "login", "akkaunt", "bo'lim", "bo'limi"}  # «Ism: Malika, Rol: operator»
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
    explicit: bool = False  # rol yoki bo'lim yozilgan (yo'q bo'lsa — operator, mavjud lavozim o'zgarmaydi)
    department: str = ""  # STAFF: bo'lim nomi
    error: str = ""
    accounts: list[tuple[str | None, int | None]] = field(default_factory=list)  # qatordagi hamma akkauntlar

    @property
    def who(self) -> str:
        return f"@{self.username}" if self.username else f"ID {self.user_id}"


def _role(word: str) -> str | None:
    key = word.translate(_APOS).lower().strip(" .!-–—'\"«»")
    return ROLE_WORDS.get(key)


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _identify(line: str) -> tuple[list[tuple[str | None, int | None, tuple[int, int]]], str]:
    """Qatordagi akkauntlar: [(username, ID, matndagi o'rni)] va xato. tg://, t.me havola, @username, ID raqam."""
    found: list[tuple[str | None, int | None, tuple[int, int]]] = []

    def add(username: str | None, user_id: int | None, span: tuple[int, int]) -> None:
        if all(span[1] <= a or span[0] >= b for _, _, (a, b) in found):
            found.append((username, user_id, span))

    for match in TG_RE.finditer(line):
        add(None, int(match.group(1)), match.span())
    for match in LINK_RE.finditer(line):
        name = clean_username(match.group(1))
        if name and name not in RESERVED:
            add(name, None, match.span())
    for match in AT_RE.finditer(line):
        name = clean_username(match.group(1))
        if name:
            add(name, None, match.span())
    for match in ID_RE.finditer(line):
        add(None, int(match.group(1)), match.span())
    if not found:  # yolg'iz raqam — faqat boshqa belgi bo'lmasa (998… — telefon, ID emas)
        for match in NUM_RE.finditer(line):
            if not (match.group(1).startswith("998") and len(match.group(1)) == 12):
                add(None, int(match.group(1)), match.span())
    if found:
        return sorted(found, key=lambda item: item[2][0]), ""
    if PHONE_RE.search(line):
        return [], ("telefon raqami bilan qo'shib bo'lmaydi — @username yoki t.me havola yozing; username'i yo'q "
                    "bo'lsa, kontaktini botga ulashing")
    return [], "@username, t.me havola yoki Telegram ID topilmadi"


def parse_line(line_no: int, raw: str) -> Entry | None:
    """Bitta qator → Entry (qatordagi birinchi akkaunt; hammasi — accounts); bo'sh qator va sarlavha — None."""
    line = BULLET_RE.sub("", raw.strip(), count=1).strip()
    words = [w.strip(" .'\"«»").lower() for chunk in SEP_RE.split(line) for w in chunk.split()]
    if not line or all(w in LABEL_WORDS for w in words if w):  # bo'sh yoki sarlavha: «Username — Ism — Rol»
        return None
    entry = Entry(line_no=line_no, raw=raw.strip())
    found, error = _identify(line)
    if not found:
        entry.error = error
        return entry
    entry.accounts = [(username, user_id) for username, user_id, _ in found]
    entry.username, entry.user_id = entry.accounts[0]
    rest, last = [], 0
    for _, _, (a, b) in found:
        rest.append(line[last:a])
        last = b
    rest.append(line[last:])
    names: list[str] = []
    for part in SEP_RE.split(" | ".join(rest)):
        part = part.strip(" -–—.'\"«»")
        if not part:
            continue
        role = _role(part)
        if role:
            entry.roles.add(role)
            continue
        if part.lower().translate(_APOS) in LABEL_WORDS:
            continue
        words = part.split()
        if len(words) > 1 and _role(words[-1]):  # «Malika operator» — ajratuvchisiz
            entry.roles.add(_role(words[-1]))  # type: ignore[arg-type]
            part = " ".join(words[:-1])
        names.append(part)
    if not entry.roles and len(names) > 1:  # «@x — Baxtiyor — Marketing»: oxirgi bo'lak — bo'lim nomi
        entry.department = clean_dept(names.pop())
        if entry.department:
            entry.roles = {STAFF}
    entry.name = " ".join(" ".join(names).split())[:NAME_LIMIT].strip()
    entry.explicit = bool(entry.roles)
    if REMOVE in entry.roles and len(entry.roles) > 1:
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
    """Qatorlar → Entry'lar; bir qatordagi bir nechta akkaunt — har biri alohida Entry (ismi va roli bir xil)."""
    entries = []
    for index, raw in enumerate((text or "").splitlines(), start=1):
        entry = parse_line(index, raw)
        if entry is None:
            continue
        entries.append(entry)
        for username, user_id in entry.accounts[1:]:
            entries.append(replace(entry, username=username, user_id=user_id, roles=set(entry.roles)))
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
    return f"{icon} <b>{html.escape(name)}</b> — {html.escape(role)} · {html.escape(who)} · {tail}"


async def _put(store: ReportStore, kind: str, entry: Entry, title: str | None) -> tuple[Person, str]:
    """Ro'yxatga qo'shadi yoki yangilaydi: (odam, «new» | «updated» | «same»)."""
    before = {p.id: p for p in await store.people(kind)}
    person = await store.add_person(kind, username=entry.username, user_id=entry.user_id, name=entry.name, title=title)
    old = before.get(person.id)
    if old is None:
        return person, "new"
    changed = old.name != person.name or old.title != person.title or old.username != person.username
    return person, "updated" if changed else "same"


MOVED = {"operator": "operatorlar jadvalidan olindi", "leader": "rahbarlar ro'yxatidan olindi (Hisobotlar yopildi)",
         "staff": "boshqa bo'limdan olindi", "excluded": "istisnodan olindi — endi sanaladi"}


async def _move(store: ReportStore, person: Person, entry: Entry, kinds: tuple[str, ...], notes: list[str]) -> None:
    """Odam bitta hisobot guruhida bo'lsin: boshqa ro'yxatlardagi yozuvlari olib tashlanadi."""
    for kind in kinds:
        if await store.drop_from(kind, username=person.username or entry.username, user_id=person.user_id):
            notes.append(MOVED[kind])


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
            await _move(store, shown, entry, ("staff",), notes)
        if LEADER in entry.roles:
            leader, state = await _put(store, "leader", entry, None)
            states.append(state)
            shown = shown or leader
            if state == "new":
                greet = leader
            await _move(store, leader, entry, ("staff",) if staff else ("operator", "staff"), notes)
        if STAFF in entry.roles:
            shown, state = await _put(store, "staff", entry, entry.department)
            states.append(state)
            await _move(store, shown, entry, ("operator", "leader"), notes)
        if EXCLUDED in entry.roles:
            shown, state = await _put(store, "excluded", entry, None)
            states.append(state)
            await _move(store, shown, entry, ("operator", "staff"), notes)
        assert shown is not None
        if not entry.roles & {EXCLUDED}:
            await _move(store, shown, entry, ("excluded",), notes)
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
        if STAFF in entry.roles:
            roles = [shown.title]
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
    lines += ["", "Hisobotda: operator va administratorlar — jadvalda (administrator belgisi bilan), rahbarlar va "
                  "boshqa bo'limlar — alohida qatorda ismi bilan; bir xil ismli akkauntlar — bitta odam."
              + (" «Guruhda hali ko'rinmagan» — birinchi reaksiya yoki izohidan keyin o'zi bog'lanadi." if result.unseen else "")
              + " Ro'yxat: /xodimlar"]
    return "\n".join(lines)


# ---------------------------------------------------------------------- joriy ro'yxat
EXAMPLE = (
    "<pre>@malika_op — Malika — operator\n"
    "@dilnoza_admin @dilnoza_ish — Dilnoza — admin\n"
    "@akmal_boss — Akmal aka — rahbar\n"
    "@baxtiyor_smm — Baxtiyor — Marketing</pre>"
)
HELP = (
    "✍️ <b>Qo'shish yoki o'zgartirish</b> — keyingi xabarda har qatorga bitta odam (yoki /xodimlar bilan bir xabarda):\n"
    + EXAMPLE + "\n"
    "Rollar: <b>operator</b>, <b>admin</b> (administrator — jadvalda operatorlar bilan), <b>rahbar</b> (direktor; "
    "Hisobotlar bo'limiga kiradi), <b>istisno</b> (sanalmaydi), <b>o'chir</b>. Boshqa so'z — <b>bo'lim nomi</b> "
    "(Marketing, Menejer…): lidni belgilasa — bo'limi bilan alohida qatorda. Rol yozilmasa — operator.\n"
    "👥 <b>Ikki akkaunt</b> (shaxsiy va ish) — bir qatorda ikkala username yoki ikki qatorda bir xil ism: hisobotda "
    "bitta odam.\n"
    "📇 <b>Username'i yo'q bo'lsa</b> — xodim profilida «Поделиться контактом» → shu botni tanlang; yoki /xodimlar'dan "
    "keyin uning biror xabarini shu yerga uzating (forward). Bot rol tugmalarini chiqaradi."
)
CONTACT_ROLES = {"op": OPERATOR, "ad": ADMIN, "le": LEADER, "ex": EXCLUDED}
PEOPLE_SHOWN = 8  # «🔗 kimning akkaunti» tugmalari
DEPTS_SHOWN = 4


def contact_name(*parts: str | None) -> str:
    """Kontakt nomidan hisobotdagi ism — birinchi so'z, lotincha: «Asal Ta'lim akademiyasi 🇺🇿» → «Asal»,
    «Арам» → «Aram»."""
    for word in " ".join(p or "" for p in parts).split():
        word = word.strip(".,;:!?\"'«»()[]")
        if any(ch.isalpha() for ch in word):
            word = translit.to_latin(word) if translit.looks_cyrillic(word) else word
            return word[:NAME_LIMIT]
    return ""


@dataclass
class ContactChoices:
    """Kontakt tugmalari uchun tanlovlar (xotirada): ism, mavjud bo'limlar va «kimning akkaunti» odamlari."""

    name: str
    full_name: str = ""
    username: str | None = None
    departments: list[str] = field(default_factory=list)
    people: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, title, ism)


async def contact_choices(store: ReportStore, user_id: int, username: str | None, full_name: str,
                          name: str) -> ContactChoices:
    people = await store.people()
    departments = sorted({p.title for p in people if p.kind == "staff" and p.title})[:DEPTS_SHOWN]
    order = {"operator": 0, "leader": 1, "staff": 2}
    named: dict[tuple[str, str, str], None] = {}
    for p in sorted(people, key=lambda x: (order.get(x.kind, 9), name_key(x.name))):
        if p.kind in order and p.name and not p.same(username, user_id):
            named.setdefault((p.kind, p.title, p.name), None)
    return ContactChoices(name=name, full_name=full_name, username=username, departments=departments,
                          people=list(named)[:PEOPLE_SHOWN])


def contact_keyboard(user_id: int, choices: ContactChoices | None = None) -> InlineKeyboardMarkup:
    def button(text: str, code: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text=text[:60], callback_data=f"sc:{user_id}:{code}")
    rows = [
        [button("🎧 Operator", "op"), button("⭐ Administrator", "ad")],
        [button("👔 Rahbar", "le"), button("🚫 Istisno", "ex")],
    ]
    if choices is not None:
        depts = [button(f"🧑‍💼 {d}", f"d{i}") for i, d in enumerate(choices.departments)]
        rows += [depts[i:i + 2] for i in range(0, len(depts), 2)]
        mark = {"operator": "🎧", "leader": "👔", "staff": "🧑‍💼"}
        links = [button(f"🔗 {mark.get(kind, '')} {name} akkaunti", f"p{i}") for i, (kind, _, name) in enumerate(choices.people)]
        rows += [links[i:i + 2] for i in range(0, len(links), 2)]
    rows.append([button("✏️ Boshqa ism", "nm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def listed_as(store: ReportStore, user_id: int, username: str | None) -> list[Person]:
    return [p for p in await store.people() if p.same(username, user_id)]


def _kind_text(person: Person) -> str:
    if person.is_admin:
        return "administrator"
    return person.title if person.kind == "staff" else KIND_TEXT.get(person.kind, person.kind)


async def contact_prompt(store: ReportStore, user_id: int, username: str | None, full_name: str, name: str) -> str:
    lines = [f"📇 <b>{html.escape(full_name or 'Kontakt')}</b> · ID {user_id}"
             + (f" · @{html.escape(username)}" if username else "")]
    current = await listed_as(store, user_id, username)
    if current:
        lines.append("Hozir: " + ", ".join(f"{html.escape(_kind_text(p))} «{html.escape(p.label)}»" for p in current))
    lines.append(f"Hisobotda ismi: <b>{html.escape(name or f'ID {user_id}')}</b> — kim bo'lib chiqsin?")
    lines.append("<i>«🔗 … akkaunti» — shu odamning ikkinchi akkaunti (hisobotda bitta odam); «✏️ Boshqa ism» — "
                 "ismni o'zgartirish.</i>")
    return "\n".join(lines)


def choice_entry(user_id: int, username: str | None, choices: ContactChoices, code: str) -> Entry | None:
    """Tugma → Entry: rol (op/ad/le/ex), bo'lim (d0…), mavjud odamning akkaunti (p0…)."""
    entry = Entry(line_no=1, raw="", username=username, user_id=user_id, name=choices.name, explicit=True)
    if code in CONTACT_ROLES:
        entry.roles = {CONTACT_ROLES[code]}
    elif code[:1] == "d" and code[1:].isdigit() and int(code[1:]) < len(choices.departments):
        entry.roles, entry.department = {STAFF}, choices.departments[int(code[1:])]
    elif code[:1] == "p" and code[1:].isdigit() and int(code[1:]) < len(choices.people):
        kind, title, name = choices.people[int(code[1:])]
        entry.name = name
        if kind == "operator":
            entry.roles = {ADMIN} if title == ADMIN else {OPERATOR}
        elif kind == "leader":
            entry.roles = {LEADER}
        else:
            entry.roles, entry.department = {STAFF}, title
    else:
        return None
    return entry


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
    groups = [
        ("🎧", "Operatorlar va administratorlar", [p for p in people if p.kind == "operator"]),
        ("👔", "Rahbarlar", [p for p in people if p.kind == "leader"]),
    ]
    for dept in sorted({p.title for p in people if p.kind == "staff"}):
        groups.append(("🧑‍💼", dept, [p for p in people if p.kind == "staff" and p.title == dept]))
    groups.append(("🚫", "Istisnolar", [p for p in people if p.kind == "excluded"]))
    lines = ["👥 <b>Xodimlar</b> — hisobotlarda shu ismlar bilan chiqadi", ""]
    for icon, title, items in groups:
        lines.append(f"{icon} <b>{html.escape(title)}</b> ({len(items)})")
        lines += [_listed(p) for p in items] or ["—"]
    lines += ["", HELP]
    return "\n".join(lines)

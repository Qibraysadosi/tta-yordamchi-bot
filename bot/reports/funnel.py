"""Lid → vaucher (v1.17): vaucher guruhidagi odamni lidlar guruhidagi lid bilan bog'lash — qaysi lid vaucherga yetdi
va lidga kim qo'ng'iroq qilgan edi.

Qoida (egasi bilan kelishilgan: telefonning o'zi yetmaydi — F.I.Sh. bo'yicha):
- lidda ism va familiya (kamida 2 so'z) — so'zlari vaucherdagi F.I.Sh. ichida bo'lsa (kirill / lotin, tartib va
  «o'g'li / qizi» farqsiz) yoki aksincha;
- lidda faqat ism (1 so'z) — vaucherdagi F.I.Sh.da shu ism bor va telefon mos bo'lsa; yoki ko'rilayotgan vaucherlar
  orasida shu ismli odam bitta bo'lsa;
- lid vaucherdan oldin tushgan (1 kun farq mumkin), ko'pi bilan 120 kun oldin; bir lid — bitta vaucher, bir vaucher —
  bitta lid (bir nechtasi mos kelsa: to'liq ismlisi, telefoni mosi, keyin vaucherga eng yaqini).
Natija bazaga yozilmaydi — har so'rovda hisoblanadi (ism tuzatilsa, darrov to'g'rilanadi)."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable

from .store import Lead, iso
from .vouchers import person_key, phone_digits

if TYPE_CHECKING:
    from .service import ReportService

LOOKBACK = timedelta(days=120)
SLACK = timedelta(days=1)


@dataclass(frozen=True)
class Person:
    """Vaucherga yuborilgan odam (rp_vouchers qatori)."""

    id: int
    words: frozenset[str]
    phone: str
    at: datetime  # UTC


def _utc(value: str | None) -> datetime | None:
    try:
        return datetime.strptime((value or "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _get(row: Any, name: str) -> Any:
    return row[name] if isinstance(row, dict) else getattr(row, name)


def people_of(rows: Iterable[Any]) -> list[Person]:
    people: dict[int, Person] = {}
    for row in rows:
        at = _utc(_get(row, "created_at"))
        words = frozenset((_get(row, "name_key") or person_key(_get(row, "name") or "")).split())
        if at is not None and words:
            people[_get(row, "id")] = Person(_get(row, "id"), words, phone_digits(_get(row, "phone") or ""), at)
    return list(people.values())


def match(leads: Iterable[Lead], people: Iterable[Person]) -> dict[int, int]:
    """Vaucher odami ID → lid ID."""
    people = list(people)
    index: dict[str, list[tuple[Lead, frozenset[str], set[str], datetime]]] = defaultdict(list)
    for lead in leads:
        words, at = frozenset(person_key(lead.name).split()), _utc(lead.created_at)
        if not words or at is None:
            continue
        entry = (lead, words, {phone_digits(p) for p in lead.phones.split(",")} - {""}, at)
        for word in words:
            index[word].append(entry)
    name_count = Counter(word for person in people for word in person.words)
    taken: set[int] = set()
    result: dict[int, int] = {}
    for person in sorted(people, key=lambda p: (p.at, p.id)):
        best: tuple[tuple[int, datetime], Lead] | None = None
        seen: set[int] = set()
        for word in person.words:
            for lead, words, phones, at in index.get(word, ()):
                if lead.id in seen or lead.id in taken:
                    continue
                seen.add(lead.id)
                if not person.at - LOOKBACK <= at <= person.at + SLACK:
                    continue
                phone_ok = bool(person.phone) and person.phone in phones
                if len(words) >= 2:
                    if len(person.words) < 2 or not (words <= person.words or person.words <= words):
                        continue
                    score = 3 + phone_ok
                elif words <= person.words and phone_ok:
                    score = 2
                elif words <= person.words and name_count[next(iter(words))] == 1:
                    score = 1
                else:
                    continue
                if best is None or (score, at) > best[0]:
                    best = ((score, at), lead)
        if best is not None:
            result[person.id] = best[1].id
            taken.add(best[1].id)
    return result


async def leads_for_people(reports: "ReportService", rows: Iterable[Any]) -> dict[int, Lead]:
    """Vaucher odamlari (rp_vouchers qatorlari) → bog'langan lid."""
    people = people_of(rows)
    if not people:
        return {}
    start = min(p.at for p in people) - LOOKBACK
    end = max(p.at for p in people) + SLACK
    leads = await reports.store.leads_between(iso(start), iso(end))
    by_id = {lead.id: lead for lead in leads}
    return {person_id: by_id[lead_id] for person_id, lead_id in match(leads, people).items()}


async def people_for_leads(reports: "ReportService", leads: Iterable[Lead]) -> dict[int, dict]:
    """Lidlar → bog'langan vaucher odami (rp_vouchers qatori). Raqobat to'g'ri bo'lishi uchun shu vaucherlarga mos
    kelishi mumkin bo'lgan boshqa lidlar ham hisobga olinadi."""
    leads = list(leads)
    times = [t for t in (_utc(lead.created_at) for lead in leads) if t is not None]
    if not times or not await reports.vouchers.store.voucher_total():
        return {}
    rows = await reports.vouchers.store.people_between(iso(min(times) - SLACK), iso(max(times) + LOOKBACK))
    people = people_of(rows)
    if not people:
        return {}
    pool = await reports.store.leads_between(iso(min(p.at for p in people) - LOOKBACK),
                                             iso(max(p.at for p in people) + SLACK))
    by_person = {row["id"]: row for row in rows}
    wanted = {lead.id for lead in leads}
    return {lead_id: by_person[person_id] for person_id, lead_id in match(pool, people).items() if lead_id in wanted}


async def from_leads(reports: "ReportService", rows: list[Any], *, hide: Any = str) -> dict:
    """Vaucherga yuborilganlardan qaysilari lid edi va lidga kim birinchi qo'ng'iroq qilgan (operator bo'yicha)."""
    from .insights import first_marks  # noqa: PLC0415 — insights → archive → … aylanma importdan qochish

    links = await leads_for_people(reports, rows)
    marks = await first_marks(reports, [lead.id for lead in links.values()]) if links else {}
    names = await reports.archive._names()
    by_operator = Counter(hide(names(marks[lead.id][1])) if lead.id in marks else "belgilanmagan"
                          for lead in links.values())
    return {"links": links, "marks": marks, "count": len(links), "by_operator": by_operator}

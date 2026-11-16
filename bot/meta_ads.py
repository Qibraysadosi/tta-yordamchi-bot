"""Meta Ads (Marketing API) — faqat o'qish (v1.16): reklama kabineti holati, sarf va natijalar, muammoli reklamalar.

Sozlamalar faqat .env da: META_ACCESS_TOKEN (System User token, ads_read ruxsati bilan), META_AD_ACCOUNT_ID
(act_… yoki faqat raqami), ixtiyoriy META_APP_SECRET (ilovada «Require App Secret» yoqilgan bo'lsa — appsecret_proof)
va META_API_VERSION. Token so'rov sarlavhasida (Authorization: Bearer) yuboriladi — URL va loglarga tushmaydi.
Bot reklamani o'zgartirmaydi (byudjet, to'xtatish) — faqat o'qiydi. Testda — soxta provayder (MetaAdsClient)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import aiohttp

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
TIMEOUT = 20  # soniya
PAGE_LIMIT = 500
MAX_PAGES = 10
LEVELS = ("account", "campaign", "adset", "ad")
# Ads Manager'dagi «Leads» ustuni: avval umumiy «lead», bo'lmasa forma yoki sayt lidlari
LEAD_ACTIONS = ("lead", "onsite_conversion.lead_grouped", "leadgen_grouped", "offsite_conversion.fb_pixel_lead",
                "onsite_web_lead")
PROBLEM_STATUSES = ("DISAPPROVED", "WITH_ISSUES", "PENDING_BILLING_INFO")
ACCOUNT_STATUS = {1: "faol", 2: "o'chirilgan (bloklangan)", 3: "to'lanmagan qarz bor", 7: "xavfsizlik tekshiruvida",
                  8: "to'lov kutilmoqda", 9: "to'lov muddati o'tgan (imtiyozli davr)", 100: "yopilish arafasida",
                  101: "yopilgan"}
DISABLE_REASON = {1: "reklama qoidalari buzilgan", 2: "intellektual mulk tekshiruvi", 3: "to'lov xavfi",
                  4: "kabinet yopilgan", 5: "reklama tekshiruvi", 6: "biznes tekshiruvi", 7: "butunlay yopilgan",
                  9: "uzoq ishlatilmagan", 11: "Business Manager qoidalari", 12: "kabinet ma'lumoti noto'g'ri",
                  15: "kabinetni begona boshqargan bo'lishi mumkin"}
STATUS_LABELS = {"ACTIVE": "faol", "PAUSED": "to'xtatilgan", "DISAPPROVED": "rad etildi", "WITH_ISSUES": "muammo bor",
                 "PENDING_BILLING_INFO": "to'lov ma'lumoti kerak", "PENDING_REVIEW": "tekshiruvda",
                 "IN_PROCESS": "tayyorlanmoqda", "CAMPAIGN_PAUSED": "kampaniya to'xtatilgan",
                 "ADSET_PAUSED": "ad set to'xtatilgan", "PREAPPROVED": "oldindan tasdiqlangan", "ARCHIVED": "arxivda",
                 "DELETED": "o'chirilgan"}
LEVEL_LABELS = {"account": "kabinet", "campaign": "kampaniya", "adset": "ad set", "ad": "reklama"}
# Byudjetlar API'da kichik birlikda (sent); bu valyutalarda kichik birlik yo'q
NO_CENTS = frozenset({"CLP", "COP", "CRC", "HUF", "IDR", "ISK", "JPY", "KRW", "PYG", "TWD", "VND"})


class MetaAdsError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato; auth — token yoki ruxsat muammosi, retryable — keyinroq o'tib ketadi."""

    def __init__(self, message: str, *, auth: bool = False, retryable: bool = False) -> None:
        super().__init__(message)
        self.auth = auth
        self.retryable = retryable


@dataclass
class AdAccount:
    id: str
    name: str
    currency: str
    timezone: str
    status: int
    disable_reason: int = 0
    amount_spent: float = 0.0  # kichik birlikda (sent), spend_cap bilan solishtirish uchun
    spend_cap: float = 0.0  # 0 — chegara yo'q

    @property
    def active(self) -> bool:
        return self.status == 1

    @property
    def status_label(self) -> str:
        label = ACCOUNT_STATUS.get(self.status, f"holat {self.status}")
        reason = DISABLE_REASON.get(self.disable_reason)
        return f"{label} — {reason}" if reason and not self.active else label

    @property
    def cap_share(self) -> float | None:
        return self.amount_spent / self.spend_cap if self.spend_cap > 0 else None


@dataclass
class InsightRow:
    level: str
    id: str
    name: str
    campaign: str
    adset: str
    day: str | None  # YYYY-MM-DD (kunlarga bo'lingan so'rovda)
    spend: float
    impressions: int
    reach: int
    clicks: int
    leads: int
    frequency: float

    @property
    def ctr(self) -> float | None:  # foiz
        return self.clicks * 100 / self.impressions if self.impressions else None

    @property
    def cpm(self) -> float | None:
        return self.spend * 1000 / self.impressions if self.impressions else None

    @property
    def cpl(self) -> float | None:
        return self.spend / self.leads if self.leads else None


@dataclass
class AdObject:
    """Kampaniya, ad set yoki reklama: holati, byudjeti (asosiy birlikda) va muammosi."""

    level: str
    id: str
    name: str
    status: str
    campaign: str = ""
    adset: str = ""
    daily_budget: float | None = None
    lifetime_budget: float | None = None
    issue: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.lower())


class MetaAdsClient(Protocol):
    async def account(self) -> AdAccount: ...

    async def insights(self, level: str, since: date, until: date, *, by_day: bool = False) -> list[InsightRow]: ...

    async def objects(self, level: str, statuses: tuple[str, ...], *, cents: int = 100) -> list[AdObject]: ...


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    return int(_num(value))


def lead_count(actions: Any) -> int:
    """`actions` ro'yxatidan lidlar soni (LEAD_ACTIONS tartibida birinchi topilgani — ikki marta sanalmaydi)."""
    found = {str(a.get("action_type")): _int(a.get("value")) for a in actions or [] if isinstance(a, dict)}
    return next((found[name] for name in LEAD_ACTIONS if name in found), 0)


def cents_of(currency: str) -> int:
    return 1 if currency.upper() in NO_CENTS else 100


def _insight(level: str, row: dict) -> InsightRow:
    ids = {"account": row.get("account_id"), "campaign": row.get("campaign_id"), "adset": row.get("adset_id"),
           "ad": row.get("ad_id")}
    names = {"account": row.get("account_name"), "campaign": row.get("campaign_name"), "adset": row.get("adset_name"),
             "ad": row.get("ad_name")}
    return InsightRow(
        level=level, id=str(ids.get(level) or ""), name=str(names.get(level) or ""),
        campaign=str(row.get("campaign_name") or ""), adset=str(row.get("adset_name") or ""),
        day=row.get("date_start") if row.get("date_start") == row.get("date_stop") else None,
        spend=_num(row.get("spend")), impressions=_int(row.get("impressions")), reach=_int(row.get("reach")),
        clicks=_int(row.get("clicks")), leads=lead_count(row.get("actions")), frequency=_num(row.get("frequency")),
    )


def _issue(row: dict) -> str:
    """issues_info / ad_review_feedback dan qisqa sabab."""
    for item in row.get("issues_info") or []:
        if isinstance(item, dict):
            text = item.get("error_summary") or item.get("error_message")
            if text:
                return str(text)[:200]
    feedback = row.get("ad_review_feedback")
    if isinstance(feedback, dict):
        for part in ("global", "placement_specific"):
            values = feedback.get(part)
            if isinstance(values, dict):
                for key, text in values.items():
                    return str(text or key)[:200]
    return ""


def _object(level: str, row: dict, cents: int) -> AdObject:
    def budget(name: str) -> float | None:
        value = _num(row.get(name))
        return value / cents if value > 0 else None

    return AdObject(
        level=level, id=str(row.get("id") or ""), name=str(row.get("name") or ""),
        status=str(row.get("effective_status") or ""),
        campaign=str((row.get("campaign") or {}).get("name") or ""), adset=str((row.get("adset") or {}).get("name") or ""),
        daily_budget=budget("daily_budget"), lifetime_budget=budget("lifetime_budget"), issue=_issue(row),
    )


def _error(data: Any, status: int) -> MetaAdsError:
    err = data.get("error") if isinstance(data, dict) else None
    err = err if isinstance(err, dict) else {}
    code = _int(err.get("code"))
    message = str(err.get("message") or f"HTTP {status}")
    low = message.lower()
    log.warning("Meta API xatosi (kod %s, HTTP %s): %s", code, status, message[:300])
    if code == 190 or status == 401:
        return MetaAdsError("Meta token yaroqsiz yoki bekor qilingan — yangi token oling (README → «Reklama nazorati»)",
                            auth=True)
    if code in (4, 17, 32, 613) or 80000 <= code < 80100 or status == 429:
        return MetaAdsError("Meta so'rovlar chegarasiga yetildi — birozdan keyin qayta urinib ko'ring", retryable=True)
    if code in (10, 294) or 200 <= code < 300 or status == 403:
        return MetaAdsError("Tokenga ruxsat yetmaydi: ads_read ruxsati va reklama kabinetiga kirish kerak "
                            "(README → «Reklama nazorati»)", auth=True)
    if code == 100 and ("does not exist" in low or "unsupported get request" in low or "missing permission" in low):
        return MetaAdsError("Reklama kabineti topilmadi — META_AD_ACCOUNT_ID ni tekshiring yoki tokenga shu kabinetni "
                            "biriktiring", auth=True)
    if code in (1, 2) or status >= 500:
        return MetaAdsError("Meta serveri vaqtincha javob bermayapti — birozdan keyin qayta urinib ko'ring",
                            retryable=True)
    return MetaAdsError(f"Meta API xatosi ({code or status}): {message[:200]}")


class GraphMetaAds:
    """Graph API orqali (aiohttp). Har so'rov — alohida qisqa sessiya: so'rovlar kam (soatiga bir necha marta)."""

    def __init__(self, token: str, account_id: str, *, version: str = "v26.0", app_secret: str | None = None,
                 base_url: str = GRAPH_URL) -> None:
        self._token = token
        self.account_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        self.version = version
        self.base_url = base_url.rstrip("/")
        self._proof = hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest() if app_secret else None

    async def _get(self, path: str, params: dict[str, Any]) -> dict:
        query = {k: json.dumps(v) if isinstance(v, (dict, list, tuple)) else str(v) for k, v in params.items()}
        if self._proof:
            query["appsecret_proof"] = self._proof
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as session:
                async with session.get(f"{self.base_url}/{self.version}/{path}", params=query,
                                       headers={"Authorization": f"Bearer {self._token}"}) as res:
                    status = res.status
                    data = await res.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            log.warning("Meta API'ga ulanib bo'lmadi: %s", type(exc).__name__)
            raise MetaAdsError("Meta serveriga ulanib bo'lmadi — birozdan keyin qayta urinib ko'ring",
                               retryable=True) from None
        if status >= 400 or not isinstance(data, dict) or "error" in data:
            raise _error(data, status)
        return data

    async def _all(self, path: str, params: dict[str, Any]) -> list[dict]:
        """Sahifalab o'qish (cursor bilan — `next` havolasi ishlatilmaydi)."""
        items: list[dict] = []
        after: str | None = None
        for _ in range(MAX_PAGES):
            page = await self._get(path, {**params, **({"after": after} if after else {})})
            items += [row for row in page.get("data") or [] if isinstance(row, dict)]
            paging = page.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not after:
                break
        return items

    async def account(self) -> AdAccount:
        data = await self._get(self.account_id, {
            "fields": "name,currency,timezone_name,account_status,disable_reason,amount_spent,spend_cap"})
        return AdAccount(
            id=str(data.get("id") or self.account_id), name=str(data.get("name") or ""),
            currency=str(data.get("currency") or "USD"), timezone=str(data.get("timezone_name") or ""),
            status=_int(data.get("account_status")), disable_reason=_int(data.get("disable_reason")),
            amount_spent=_num(data.get("amount_spent")), spend_cap=_num(data.get("spend_cap")),
        )

    async def insights(self, level: str, since: date, until: date, *, by_day: bool = False) -> list[InsightRow]:
        if level not in LEVELS:
            raise ValueError(level)
        names = {"account": "account_id,account_name", "campaign": "campaign_id,campaign_name",
                 "adset": "adset_id,adset_name,campaign_name", "ad": "ad_id,ad_name,adset_name,campaign_name"}[level]
        params: dict[str, Any] = {
            "level": level, "fields": f"{names},spend,impressions,reach,frequency,clicks,actions",
            "time_range": {"since": since.isoformat(), "until": until.isoformat()}, "limit": PAGE_LIMIT,
        }
        if by_day:
            params["time_increment"] = 1
        return [_insight(level, row) for row in await self._all(f"{self.account_id}/insights", params)]

    async def objects(self, level: str, statuses: tuple[str, ...], *, cents: int = 100) -> list[AdObject]:
        edge = {"campaign": "campaigns", "adset": "adsets", "ad": "ads"}[level]
        fields = {
            "campaign": "id,name,effective_status,daily_budget,lifetime_budget,issues_info",
            "adset": "id,name,effective_status,daily_budget,lifetime_budget,issues_info,campaign{name}",
            "ad": "id,name,effective_status,issues_info,ad_review_feedback,adset{name},campaign{name}",
        }[level]
        rows = await self._all(f"{self.account_id}/{edge}",
                               {"fields": fields, "effective_status": list(statuses), "limit": 200})
        return [_object(level, row, cents) for row in rows]


def build(settings: Any) -> MetaAdsClient | None:
    """Sozlamalardan mijoz; token yoki kabinet yozilmagan bo'lsa — None (Meta ulanmagan)."""
    token, account = getattr(settings, "meta_access_token", None), getattr(settings, "meta_ad_account_id", None)
    if not token or not account:
        return None
    return GraphMetaAds(token, account, version=settings.meta_api_version, app_secret=settings.meta_app_secret)

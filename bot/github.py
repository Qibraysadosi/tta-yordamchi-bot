"""GitHub bilan ishlash: so'nggi chiqarish (Release) yoki tarmoq arxivini olish — /yangilash github uchun.

Provayder: GitHubClient (REST API, httpx2). Faqat o'qiydi: release ro'yxati, aktivni yuklash, VERSION fayli,
tarmoq arxivi (zipball). Testlarda soxta transport (httpx2.MockTransport) bilan almashtiriladi.
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import AsyncIterator

import httpx2

API = "https://api.github.com"
TIMEOUT = 60.0
MAX_BYTES = 20 * 1024 * 1024  # updater.MAX_ARCHIVE_BYTES bilan bir xil
ASSET_RE = re.compile(r"^claude-telegram-bot-v(\d+\.\d+\.\d+)\.zip$")
VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
JSON_ACCEPT = "application/vnd.github+json"
RAW_ACCEPT = "application/vnd.github.raw+json"
BINARY_ACCEPT = "application/octet-stream"


class GitHubError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato."""


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """«v1.6.0», «1.6.0», «release-1.6.0» → (1, 6, 0); topilmasa None."""
    match = VERSION_RE.search(text or "")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None:
        return False
    return old is None or new > old


@dataclass(frozen=True)
class Release:
    version: str  # «1.6.0»
    source: str  # release | branch
    tag: str  # «v1.6.0» yoki tarmoq nomi
    title: str
    notes: str
    download_url: str  # API manzili: aktiv yoki zipball
    asset_name: str | None  # None — manba arxivi (zipball)
    size: int  # aktiv hajmi; 0 — noma'lum (zipball)
    html_url: str

    @property
    def label(self) -> str:
        if self.source == "release":
            return f"GitHub Release {self.tag} ({self.asset_name or 'manba arxivi'})"
        return f"GitHub «{self.tag}» tarmog'i (release yo'q)"


class GitHubClient:
    def __init__(
        self,
        repo: str,
        token: str | None = None,
        branch: str = "main",
        *,
        client: httpx2.AsyncClient | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        if not REPO_RE.match(repo):
            raise ValueError(f"GitHub repo owner/repo ko'rinishida bo'lsin: {repo!r}")
        self.repo = repo
        self.token = token or None
        self.branch = branch
        self.max_bytes = max_bytes
        self._client = client  # testlar uchun; None bo'lsa har amalda yangi mijoz ochiladi

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.repo}"

    # ------------------------------------------------------------------ so'rovlar
    def _headers(self, accept: str = JSON_ACCEPT) -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "tta-yordamchi-bot"}
        if self.token:
            # Boshqa domenga (aktivlar S3'da) yo'naltirilganda httpx2 bu sarlavhani o'zi olib tashlaydi
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncIterator[httpx2.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx2.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            yield client

    async def _get(self, client: httpx2.AsyncClient, url: str, *, accept: str = JSON_ACCEPT, **params) -> httpx2.Response:
        try:
            return await client.get(url, headers=self._headers(accept), params=params or None)
        except httpx2.HTTPError as exc:
            raise GitHubError(f"GitHub'ga ulanib bo'lmadi ({type(exc).__name__}). Internetni tekshirib, qayta urinib ko'ring.") from exc

    def _not_found(self) -> GitHubError:
        if self.token:
            hint = "GITHUB_TOKEN ning huquqi yetarli emas (shu repo uchun Contents: Read-only kerak) yoki repo nomi xato."
        else:
            hint = "Yopiq (private) repo uchun serverdagi .env ga GITHUB_TOKEN yozing (faqat o'qish huquqi)."
        return GitHubError(f"GitHub'da {self.repo} topilmadi yoki yopiq. {hint}")

    def _error(self, response: httpx2.Response) -> GitHubError:
        code = response.status_code
        if code == 401:
            return GitHubError("GITHUB_TOKEN noto'g'ri yoki muddati tugagan — .env dagi tokenni yangilang.")
        if code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            extra = "" if self.token else " GITHUB_TOKEN bilan chegara ancha katta."
            return GitHubError(f"GitHub so'rovlar chegarasi tugadi — birozdan keyin qayta urinib ko'ring.{extra}")
        if code == 403:
            return GitHubError("GitHub ruxsat bermadi (403) — tokenning huquqlarini tekshiring.")
        if code == 404:
            return self._not_found()
        if code >= 500:
            return GitHubError(f"GitHub vaqtincha ishlamayapti (HTTP {code}) — keyinroq urinib ko'ring.")
        return GitHubError(f"GitHub kutilmagan javob berdi (HTTP {code}).")

    # ------------------------------------------------------------------ release
    async def latest(self) -> Release:
        """Eng so'nggi chiqarish; release bo'lmasa — tarmoqning manba arxivi (VERSION fayli orqali)."""
        async with self._session() as client:
            response = await self._get(client, f"{API}/repos/{self.repo}/releases/latest")
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise GitHubError("GitHub tushunarsiz javob qaytardi (JSON emas).") from exc
                if not isinstance(data, dict):
                    raise GitHubError("GitHub tushunarsiz javob qaytardi.")
                return self._release_from(data)
            if response.status_code != 404:
                raise self._error(response)
            # 404: release yo'q yoki repo ko'rinmaydi — VERSION fayli buni ajratadi
            version = await self._get(
                client, f"{API}/repos/{self.repo}/contents/VERSION", accept=RAW_ACCEPT, ref=self.branch
            )
            if version.status_code != 200:
                raise self._error(version)
            text = version.text.strip().splitlines()
            number = text[0].strip()[:32] if text else ""
            if parse_version(number) is None:
                raise GitHubError(f"{self.branch} tarmog'idagi VERSION fayli tushunarsiz: {number!r}")
            return Release(
                version=number,
                source="branch",
                tag=self.branch,
                title=f"{self.branch} tarmog'i",
                notes="",
                download_url=f"{API}/repos/{self.repo}/zipball/{self.branch}",
                asset_name=None,
                size=0,
                html_url=f"{self.html_url}/tree/{self.branch}",
            )

    def _release_from(self, data: dict) -> Release:
        tag = str(data.get("tag_name") or "")
        assets = [a for a in data.get("assets") or [] if isinstance(a, dict) and str(a.get("name", "")).endswith(".zip")]
        parsed = parse_version(tag)
        if parsed is None:
            for asset in assets:
                parsed = parse_version(asset.get("name"))
                if parsed:
                    break
        if parsed is None:
            raise GitHubError(f"Release tegida versiya raqami yo'q (vX.Y.Z kutilgan): {tag or '—'}")
        version = ".".join(str(x) for x in parsed)
        chosen = next((a for a in assets if a.get("name") == f"claude-telegram-bot-v{version}.zip"), None)
        chosen = chosen or next((a for a in assets if ASSET_RE.match(str(a.get("name", "")))), None) or (assets[0] if assets else None)
        if chosen is not None:
            download_url, asset_name, size = str(chosen.get("url") or ""), str(chosen.get("name")), int(chosen.get("size") or 0)
        else:
            download_url, asset_name, size = str(data.get("zipball_url") or f"{API}/repos/{self.repo}/zipball/{tag}"), None, 0
        if not download_url:
            raise GitHubError("Release'da yuklab olish manzili yo'q.")
        return Release(
            version=version,
            source="release",
            tag=tag,
            title=str(data.get("name") or tag),
            notes=str(data.get("body") or "").strip(),
            download_url=download_url,
            asset_name=asset_name,
            size=size,
            html_url=str(data.get("html_url") or f"{self.html_url}/releases/tag/{tag}"),
        )

    async def download(self, release: Release) -> bytes:
        """Aktiv yoki manba arxivini yuklaydi (hajm chegarasi bilan)."""
        if release.size > self.max_bytes:
            raise GitHubError(f"Arxiv juda katta ({release.size // (1024 * 1024)} MB; {self.max_bytes // (1024 * 1024)} MB gacha bo'lsin).")
        accept = BINARY_ACCEPT if release.asset_name else JSON_ACCEPT
        chunks: list[bytes] = []
        total = 0
        async with self._session() as client:
            try:
                async with client.stream("GET", release.download_url, headers=self._headers(accept)) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise self._error(response)
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise GitHubError(f"Arxiv juda katta ({self.max_bytes // (1024 * 1024)} MB dan oshmasin).")
                        chunks.append(chunk)
            except httpx2.HTTPError as exc:
                raise GitHubError(f"GitHub'dan yuklab bo'lmadi ({type(exc).__name__}). Qayta urinib ko'ring.") from exc
        data = b"".join(chunks)
        if not data:
            raise GitHubError("GitHub bo'sh fayl qaytardi.")
        return data

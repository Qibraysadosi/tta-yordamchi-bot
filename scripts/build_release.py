#!/usr/bin/env python3
"""Chiqarish arxivini yig'adi: dist/claude-telegram-bot-v<VERSION>.zip (/yangilash uchun).

Arxivga faqat serverga kerakli fayllar kiradi (bot/, prompts/, requirements.txt, VERSION, README.md,
.env.example, install*.sh, docker-compose.yml, launcher.py, Dockerfile, .dockerignore) + UPDATE_NOTES.md.
Har fayl maxfiy satrlar (API kaliti, bot tokeni) bo'yicha tekshiriladi — topilsa arxiv yig'ilmaydi.

`--publish` — arxivdan keyin GitHub Release (teg v<VERSION>, izoh UPDATE_NOTES.md, aktiv .zip) yaratadi
yoki mavjudini yangilaydi. Repo: `--repo` → `GITHUB_REPO` → `git remote origin`. Transport: `gh` CLI
(kirilgan bo'lsa), aks holda REST (`GITHUB_TOKEN` yoki `GH_TOKEN` muhitdan). Token hech qachon chop etilmaydi.

Ishlatish: python scripts/build_release.py [--out dist] [--notes UPDATE_NOTES.md]
                                           [--publish [--repo owner/name] [--branch main]]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "claude-telegram-bot"
FOLDERS = ("bot", "prompts")
FILES = (
    "requirements.txt", "VERSION", "launcher.py", "Dockerfile", "docker-compose.yml", ".dockerignore",
    ".env.example", "install.sh", "install-test.sh", "README.md",
)
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SECRET_PATTERNS = [
    re.compile(rb"sk-ant-[A-Za-z0-9_-]{30,}"),  # Anthropic kaliti
    re.compile(rb"\b\d{8,11}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot tokeni
    re.compile(rb"sk-[A-Za-z0-9]{40,}"),  # OpenAI va o'xshash kalitlar
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),  # GitHub tokenlari (PAT, OAuth, App)
    re.compile(rb"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]
API = "https://api.github.com"
# https://github.com/owner/name(.git) yoki git@github.com:owner/name(.git)
REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class PublishError(Exception):
    """Release yaratishda xato — foydalanuvchiga ko'rsatiladigan matn."""


def iter_files() -> list[Path]:
    items: list[Path] = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and not (SKIP_PARTS & set(path.parts)) and path.suffix != ".pyc":
                items.append(path)
    for name in FILES:
        path = ROOT / name
        if path.exists():
            items.append(path)
    return items


def scan_secrets(path: Path, data: bytes) -> str | None:
    if path.name == ".env.example":
        # namunada haqiqiy qiymat bo'lmasligi kerak — kalit nomidan keyin bo'sh yoki izoh
        for line in data.splitlines():
            if re.match(rb"\s*(ANTHROPIC_API_KEY|TELEGRAM_BOT_TOKEN|[A-Z_]*TOKEN|[A-Z_]*API_KEY)\s*=\s*\S", line):
                return line.decode(errors="replace").strip()
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            return pattern.pattern.decode(errors="replace")
    return None


# ----------------------------------------------------------------------------- GitHub Release

def _normalize_repo(value: str) -> str | None:
    """`owner/name` yoki GitHub URL (https/ssh) → `owner/name`."""
    value = value.strip()
    match = REMOTE_RE.search(value)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        return value
    return None


def _resolve_repo(arg: str | None) -> str | None:
    """Repo: --repo → GITHUB_REPO → git remote origin."""
    candidate = arg or os.environ.get("GITHUB_REPO", "")
    if candidate.strip():
        return _normalize_repo(candidate)
    proc = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                          check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return _normalize_repo(proc.stdout)


def _gh(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *argv], check=False, capture_output=True, text=True)


def _gh_available() -> bool:
    """`gh` o'rnatilgan va kirilgan bo'lsa — shu orqali chiqaramiz."""
    if not shutil.which("gh"):
        return False
    return _gh("auth", "status").returncode == 0


def _publish_gh(repo: str, tag: str, zip_path: Path, notes_path: Path, branch: str) -> str:
    if _gh("release", "view", tag, "--repo", repo).returncode == 0:
        steps = [
            ["release", "upload", tag, str(zip_path), "--repo", repo, "--clobber"],
            ["release", "edit", tag, "--repo", repo, "--title", tag, "--notes-file", str(notes_path)],
        ]
    else:
        steps = [["release", "create", tag, str(zip_path), "--repo", repo, "--title", tag,
                  "--notes-file", str(notes_path), "--target", branch]]
    for step in steps:
        proc = _gh(*step)
        if proc.returncode != 0:
            # gh xato matnida token bo'lmaydi
            raise PublishError(f"gh {step[0]} {step[1]} bajarilmadi:\n{proc.stderr.strip()}")
    return f"https://github.com/{repo}/releases/tag/{tag}"


def _api(method: str, url: str, token: str, data: bytes | None = None,
         content_type: str | None = None) -> tuple[int, dict | bytes]:
    """GitHub REST chaqiruvi: (holat kodi, JSON lug'at yoki xom baytlar). HTTP xato ham kod bilan qaytadi."""
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "tta-yordamchi-bot-release")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise PublishError(f"GitHub'ga ulanib bo'lmadi: {exc.reason}") from None
    if not raw:
        return status, {}
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _expect(status: int, body: dict | bytes, *ok: int, step: str) -> dict:
    if status in ok and isinstance(body, dict):
        return body
    message = (body.get("message", "") if isinstance(body, dict) else "") or "noma'lum xato"
    raise PublishError(f"GitHub API {status} ({step}): {message}")


def _publish_rest(repo: str, tag: str, zip_path: Path, notes: str, branch: str, token: str) -> str:
    base = f"{API}/repos/{repo}/releases"
    payload = {"tag_name": tag, "name": tag, "body": notes, "target_commitish": branch}
    status, body = _api("POST", base, token, json.dumps(payload).encode(), "application/json")
    if status == 422:
        # teg/release allaqachon bor — izohni yangilaymiz, eski aktivni o'chiramiz
        existing = _expect(*_api("GET", f"{base}/tags/{tag}", token), 200, step="release topish")
        patch = json.dumps({"name": tag, "body": notes}).encode()
        body = _expect(*_api("PATCH", f"{base}/{existing['id']}", token, patch, "application/json"),
                       200, step="izohni yangilash")
        for asset in existing.get("assets", []):
            if asset.get("name") == zip_path.name:
                _expect(*_api("DELETE", f"{base}/assets/{asset['id']}", token), 204, step="eski aktivni o'chirish")
    else:
        body = _expect(status, body, 201, step="release yaratish")
    upload_url = body["upload_url"].split("{", 1)[0] + "?name=" + urllib.parse.quote(zip_path.name)
    _expect(*_api("POST", upload_url, token, zip_path.read_bytes(), "application/zip"), 201, step="aktiv yuklash")
    return body["html_url"]


def publish_release(version: str, zip_path: Path, notes_path: Path, repo_arg: str | None, branch: str) -> int:
    repo = _resolve_repo(repo_arg)
    if not repo:
        print("❌ GitHub repo aniqlanmadi — `--repo owner/name` bering yoki GITHUB_REPO ni muhitga qo'ying "
              "(yoki git remote origin GitHub'ga ko'rsatsin).")
        return 1
    tag = f"v{version}"
    try:
        if _gh_available():
            url = _publish_gh(repo, tag, zip_path, notes_path, branch)
        else:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if not token:
                print("❌ GitHub token yo'q: `gh auth login` qiling yoki GITHUB_TOKEN muhit o'zgaruvchisini bering "
                      f"(fine-grained token, {repo} uchun Contents: Read and write).")
                return 1
            url = _publish_rest(repo, tag, zip_path, notes_path.read_text(encoding="utf-8"), branch, token)
    except PublishError as exc:
        print(f"❌ {exc}")
        return 1
    print(f"✅ GitHub Release: {url}")
    print("Keyingi qadam: botga /yangilash github yozing (avval sinov botida).")
    return 0


# ----------------------------------------------------------------------------- arxiv

def _display(path: Path) -> str:
    """Chop etish uchun yo'l: loyiha ichida — nisbiy, tashqarida (masalan --out /tmp/..) — to'liq."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist")
    parser.add_argument("--notes", default="UPDATE_NOTES.md")
    parser.add_argument("--publish", action="store_true", help="GitHub Release yaratish/yangilash")
    parser.add_argument("--repo", help="owner/name (standart: GITHUB_REPO yoki git remote origin)")
    parser.add_argument("--branch", default="main", help="release nishon tarmog'i")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text().strip()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{PREFIX}-v{version}.zip"
    notes_path = ROOT / args.notes
    if not notes_path.exists():
        print(f"❌ {args.notes} topilmadi — chiqarish izohini yozing (5–8 qator, o'zbekcha).")
        return 1
    problems: list[str] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files():
            data = path.read_bytes()
            found = scan_secrets(path, data)
            if found:
                problems.append(f"{path.relative_to(ROOT)}: {found}")
                continue
            archive.write(path, f"{PREFIX}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(f"{PREFIX}/UPDATE_NOTES.md", notes_path.read_text(encoding="utf-8"))
    if problems:
        target.unlink(missing_ok=True)
        print("❌ Maxfiy ma'lumotga o'xshash satrlar topildi, arxiv yig'ilmadi:\n  " + "\n  ".join(problems))
        return 1
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    assert not any(n.endswith("/.env") or "/data/" in n for n in names)
    print(f"✅ {_display(target)} — {len(names)} fayl, {target.stat().st_size // 1024} KB (v{version})")
    if args.publish:
        return publish_release(version, target, notes_path, args.repo, args.branch)
    print("Keyingi qadam: botga /yangilash yozib, shu faylni yuboring (avval sinov botiga).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

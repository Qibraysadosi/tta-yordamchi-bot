#!/usr/bin/env python3
"""Chiqarish arxivini yig'adi: dist/claude-telegram-bot-v<VERSION>.zip (/yangilash uchun).

Arxivga faqat serverga kerakli fayllar kiradi (bot/, prompts/, requirements.txt, VERSION, README.md,
.env.example, install*.sh, docker-compose.yml, launcher.py, Dockerfile, .dockerignore) + UPDATE_NOTES.md.
Har fayl maxfiy satrlar (API kaliti, bot tokeni) bo'yicha tekshiriladi — topilsa arxiv yig'ilmaydi.

Ishlatish: python scripts/build_release.py [--out dist] [--notes UPDATE_NOTES.md]
"""
from __future__ import annotations

import argparse
import re
import sys
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
    re.compile(rb"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),  # GitHub tokeni
    re.compile(rb"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist")
    parser.add_argument("--notes", default="UPDATE_NOTES.md")
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
    print(f"✅ {target.relative_to(ROOT)} — {len(names)} fayl, {target.stat().st_size // 1024} KB (v{version})")
    print("Keyingi qadam: botga /yangilash yozib, shu faylni yuboring (avval sinov botiga).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

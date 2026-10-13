"""Bot ishga tushiruvchisi (konteynerda doimiy ishlaydi, /yangilash bilan almashtirilmaydi).

- Botni ishga tushiradi; bot 75 kodi bilan chiqsa (yangilanish/qayta ishga tushirish) — darhol qayta ishga tushiradi.
- requirements.txt o'zgargan bo'lsa, ishga tushirishdan oldin yangi kutubxonalarni o'rnatadi.
- Yangi versiya Telegram'ga ulanishidan oldin yiqilsa (data/update/pending.json hali turgan bo'lsa),
  zaxiradan eski versiyani tiklaydi va data/update/result.json ga xatoni yozadi.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from collections import deque
from pathlib import Path

RESTART_CODE = 75
RELEASE_ITEMS = ("bot", "prompts", "requirements.txt", "VERSION")

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parent))
UPDATE_DIR = APP / "data" / "update"
PENDING = UPDATE_DIR / "pending.json"
RESULT = UPDATE_DIR / "result.json"
REQ_STAMP = Path(os.environ.get("REQ_STAMP", "/usr/local/share/claude-bot/requirements.sha256"))
PIP_ERROR_NAME = "pip_error.json"  # pip xatosi: bot (UpdateManager.finalize) egasiga aytadi
BOT_COMMAND = os.environ.get("BOT_COMMAND", f"{sys.executable} -m bot").split()

child: subprocess.Popen | None = None
stopping = False


def log(message: str) -> None:
    print(f"[launcher] {message}", flush=True)


def ensure_requirements() -> None:
    requirements = APP / "requirements.txt"
    if not requirements.exists():
        return
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    installed = REQ_STAMP.read_text().strip() if REQ_STAMP.exists() else ""
    pip_error = UPDATE_DIR / PIP_ERROR_NAME
    if digest == installed:
        pip_error.unlink(missing_ok=True)
        return
    log("requirements.txt o'zgargan — kutubxonalar o'rnatilmoqda...")
    code = subprocess.call(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", "-r", str(requirements)]
    )
    if code == 0:
        REQ_STAMP.parent.mkdir(parents=True, exist_ok=True)
        REQ_STAMP.write_text(digest)
        pip_error.unlink(missing_ok=True)
    else:
        log(f"pip install xato bilan tugadi (kod {code})")
        try:
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            pip_error.write_text(json.dumps({"code": code, "sha256": digest}), encoding="utf-8")
        except OSError as exc:
            log(f"pip xatosini yozib bo'lmadi: {exc}")


def run_bot() -> tuple[int, list[str]]:
    global child
    tail: deque[str] = deque(maxlen=25)
    child = subprocess.Popen(
        BOT_COMMAND,
        cwd=APP,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert child.stdout is not None
    for line in child.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line.rstrip("\n"))
    code = child.wait()
    child = None
    return code, list(tail)


def restore(backup: Path) -> None:
    for item in RELEASE_ITEMS:
        source = backup / item
        target = APP / item
        if not source.exists():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def rollback(tail: list[str]) -> bool:
    """Yangi versiya ishga tushmadi: zaxiradan tiklaydi. Tiklanmasa False."""
    try:
        pending = json.loads(PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pending = {}
    backup = Path(pending.get("backup") or "/nonexistent").resolve()
    backups_dir = (APP / "data" / "backups").resolve()
    if backups_dir not in backup.parents or not backup.is_dir():
        log("Zaxira topilmadi — tiklab bo'lmadi")
        PENDING.unlink(missing_ok=True)
        return False
    log(f"Yangi versiya ishga tushmadi — zaxiradan tiklanmoqda: {backup.name}")
    try:
        restore(backup)
    except OSError as exc:
        log(f"Tiklashda xato: {exc}")
        PENDING.unlink(missing_ok=True)
        return False
    shutil.rmtree(backup, ignore_errors=True)  # tiklangan versiya endi joriy — bu zaxira kerak emas
    RESULT.write_text(
        json.dumps(
            {
                "status": "rolled_back",
                "version": pending.get("version"),
                "previous": pending.get("previous"),
                "chat_id": pending.get("chat_id"),
                "error": "\n".join(tail[-15:]),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    PENDING.unlink(missing_ok=True)
    return True


def handle_signal(signum, _frame) -> None:
    global stopping
    stopping = True
    if child is not None and child.poll() is None:
        child.send_signal(signum)


def main() -> int:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        ensure_requirements()
        if stopping:
            return 0
        code, tail = run_bot()
        if stopping:
            return 0
        if code == RESTART_CODE:
            log("Bot qayta ishga tushirilmoqda...")
            continue
        if PENDING.exists() and rollback(tail):
            continue
        log(f"Bot {code} kodi bilan to'xtadi")
        return code


if __name__ == "__main__":
    sys.exit(main())

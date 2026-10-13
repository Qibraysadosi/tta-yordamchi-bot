"""Botni Telegram orqali yangilash.

Tartib:
1. Egasi /yangilash dan keyin .zip yuboradi → stage(): arxiv vaqtinchalik papkaga ochiladi va tekshiriladi
   (fayl yo'llari, tuzilma, Python sintaksisi).
2. Egasi tasdiqlasa → apply(): joriy versiya data/backups/ ga saqlanadi, yangi fayllar joyiga qo'yiladi,
   data/update/pending.json yoziladi va bot qayta ishga tushadi (chiqish kodi 75).
3. launcher.py yangi versiyani ishga tushiradi. Yangi versiya Telegram'ga ulangach finalize() egasiga
   «yangilandi» deb yozadi. Yangi versiya ulanishdan oldin yiqilsa, launcher zaxiradan tiklaydi va
   result.json yozadi — eski versiya ishga tushgach, egasiga xato haqida xabar beradi.

Zaxiralar «bekor qilish» navbati kabi ishlaydi: har yangilanishdan oldin bitta zaxira qo'shiladi,
/qaytarish eng oxirgisini tiklaydi va uni navbatdan oladi (ketma-ket bosilsa, yanada oldingiga qaytadi).

GitHub'dan yangilash (/yangilash github): fetch_release() so'nggi Release aktivini (yoki tarmoq arxivini)
yuklaydi, keyin arxiv xuddi .zip yuborilgandek stage() → tasdiq → apply() yo'lidan o'tadi.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .github import GitHubClient, Release

RESTART_CODE = 75
# Inline tugmalar (handlers va scheduler bir xil qiymatni ishlatadi)
CALLBACK_GITHUB_INSTALL = "gh:install"
CALLBACK_GITHUB_CHECK = "gh:check"
MAX_CHANGED_SHOWN = 8
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
KEEP_BACKUPS = 5
STAMP_FORMAT = "%Y%m%d-%H%M%S"
# launcher.py: o'rnatilgan requirements.txt belgisi va pip xatosi fayli (data/update ichida)
REQ_STAMP_DEFAULT = "/usr/local/share/claude-bot/requirements.sha256"
PIP_ERROR_FILE = "pip_error.json"

# Zaxiraga olinadigan va tiklanadigan qismlar (launcher.py da ham xuddi shu ro'yxat)
RELEASE_ITEMS = ("bot", "prompts", "requirements.txt", "VERSION")
# Yangilanish arxivida ruxsat etilgan narsalar; qolganlari (.env, data/, Dockerfile, launcher.py ...) e'tiborsiz qoladi
ALLOWED_TOP = frozenset(
    {
        "bot", "prompts", "requirements.txt", "VERSION", "UPDATE_NOTES.md", "README.md", ".env.example",
        "install.sh", "install-test.sh", "install-miniapp.sh", "docker-compose.yml",
    }
)
REPLACED_DIRS = frozenset({"bot"})  # butunlay almashtiriladi — eski versiyadagi ortiqcha fayllar qolmaydi
NOT_INSTALLED = frozenset({"UPDATE_NOTES.md"})
# /kod: botni qayta yig'ish uchun kerak bo'lgan hamma narsa (.env va data/ — kirmaydi)
EXPORT_ITEMS = (
    "bot", "prompts", "requirements.txt", "VERSION", "launcher.py", "Dockerfile", "docker-compose.yml",
    ".dockerignore", ".env.example", "install.sh", "install-test.sh", "README.md",
)
EXPORT_ROOT = "claude-telegram-bot"


class UpdateError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato."""


@dataclass
class StagedUpdate:
    id: str
    path: Path
    version: str
    current_version: str
    notes: str
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # joriy versiyaga nisbatan o'zgaradigan fayllar
    requirements_changed: bool = False
    restore_from: Path | None = None  # /qaytarish: shu zaxira nusxadan tiklanadi
    source: str = ""  # qayerdan kelgan: «GitHub Release v1.6.0 (…)»; bo'sh — Telegram'dan yuborilgan .zip

    @property
    def is_restore(self) -> bool:
        return self.restore_from is not None


@dataclass
class UpdateNotice:
    chat_id: int | None
    ok: bool
    text: str


def read_version(folder: Path) -> str:
    try:
        text = (folder / "VERSION").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "1.0.0"
    return text.splitlines()[0].strip()[:32] if text else "1.0.0"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime(STAMP_FORMAT)


def _skip(path: Path | PurePosixPath) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


class UpdateManager:
    def __init__(self, app_dir: Path, data_dir: Path) -> None:
        self.app_dir = app_dir
        self.data_dir = data_dir
        self.update_dir = data_dir / "update"
        self.backups_dir = data_dir / "backups"
        self.pending_file = self.update_dir / "pending.json"
        self.result_file = self.update_dir / "result.json"

    @property
    def version(self) -> str:
        return read_version(self.app_dir)

    # ------------------------------------------------------------------ 1. tekshirish
    def stage(self, archive: bytes) -> StagedUpdate:
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise UpdateError("Arxiv juda katta (20 MB dan oshmasin).")
        try:
            zf = zipfile.ZipFile(io.BytesIO(archive))
        except zipfile.BadZipFile as exc:
            raise UpdateError("Bu .zip arxiv emas yoki buzilgan.") from exc

        staging = self._new_staging()
        try:
            files, skipped = self._extract(zf, staging)
            self._check_structure(staging)
            self._check_syntax(staging)
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise UpdateError("Arxiv buzilgan — qayta yuklab, yana yuboring.") from exc
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            zf.close()

        notes_file = staging / "UPDATE_NOTES.md"
        notes = notes_file.read_text(encoding="utf-8", errors="replace").strip() if notes_file.exists() else ""
        return StagedUpdate(
            id=staging.name.removeprefix("staging-"),
            path=staging,
            version=read_version(staging),
            current_version=self.version,
            notes=notes[:1500],
            files=files,
            skipped=skipped,
            changed=self._changed_files(staging),
            requirements_changed=self._requirements_differ(staging),
        )

    def _changed_files(self, staging: Path) -> list[str]:
        """Joriy versiyaga nisbatan yangi yoki o'zgargan fayllar; bot/ dan yo'qoladiganlar «(o'chiriladi)» bilan."""
        changed: list[str] = []
        for file in sorted(staging.rglob("*")):
            if not file.is_file():
                continue
            relative = file.relative_to(staging)
            if relative.parts[0] in NOT_INSTALLED or _skip(relative):
                continue
            current = self.app_dir / relative
            try:
                same = current.is_file() and current.read_bytes() == file.read_bytes()
            except OSError:
                same = False
            if not same:
                changed.append(relative.as_posix())
        for name in REPLACED_DIRS:
            current_dir = self.app_dir / name
            if not current_dir.is_dir() or not (staging / name).is_dir():
                continue
            for file in sorted(current_dir.rglob("*")):
                relative = file.relative_to(self.app_dir)
                if file.is_file() and not _skip(relative) and not (staging / relative).exists():
                    changed.append(f"{relative.as_posix()} (o'chiriladi)")
        return changed

    def _extract(self, zf: zipfile.ZipFile, staging: Path) -> tuple[list[str], list[str]]:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if not infos:
            raise UpdateError("Arxiv bo'sh.")
        if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
            raise UpdateError("Arxiv ochilganda juda katta.")
        for info in infos:
            name = info.filename
            if "\\" in name:
                raise UpdateError(f"Noto'g'ri fayl yo'li: {name}")
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise UpdateError(f"Xavfli fayl yo'li: {name}")
        prefix = self._common_prefix(infos)
        files: list[str] = []
        skipped: list[str] = []
        for info in infos:
            name = info.filename
            relative = name[len(prefix):]
            path = PurePosixPath(relative)
            if not relative:
                continue
            if path.parts[0] not in ALLOWED_TOP or _skip(path):
                skipped.append(relative)
                continue
            target = staging.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if (info.external_attr >> 16) & 0o100:  # bajariladigan fayl (masalan, install.sh)
                target.chmod(0o755)
            files.append(relative)
        return files, skipped

    @staticmethod
    def _common_prefix(infos: list[zipfile.ZipInfo]) -> str:
        """Arxiv ichida bitta umumiy papka bo'lsa (masalan, claude-telegram-bot/), uni olib tashlaymiz."""
        firsts = {info.filename.split("/", 1)[0] for info in infos}
        if len(firsts) == 1:
            first = next(iter(firsts))
            if first not in ALLOWED_TOP and all("/" in info.filename for info in infos):
                return first + "/"
        return ""

    @staticmethod
    def _check_structure(staging: Path) -> None:
        required = ("__init__.py", "__main__.py", "app.py")
        if not all((staging / "bot" / name).is_file() for name in required):
            raise UpdateError("Arxivda bot/ papkasi to'liq emas (bot/__init__.py, bot/__main__.py, bot/app.py kerak).")
        if not (staging / "VERSION").is_file():
            raise UpdateError("Arxivda VERSION fayli yo'q.")

    @staticmethod
    def _check_syntax(staging: Path) -> None:
        for py in sorted(staging.rglob("*.py")):
            relative = py.relative_to(staging).as_posix()
            try:
                compile(py.read_text(encoding="utf-8"), relative, "exec")
            except SyntaxError as exc:
                raise UpdateError(f"Sintaksis xatosi: {relative}, {exc.lineno}-qator: {exc.msg}") from exc
            except UnicodeDecodeError as exc:
                raise UpdateError(f"Fayl UTF-8 emas: {relative}") from exc

    def _requirements_differ(self, folder: Path) -> bool:
        new_req = folder / "requirements.txt"
        old_req = self.app_dir / "requirements.txt"
        return new_req.exists() and (not old_req.exists() or new_req.read_bytes() != old_req.read_bytes())

    def discard(self, staged: StagedUpdate) -> None:
        shutil.rmtree(staged.path, ignore_errors=True)

    def _new_staging(self, suffix: str = "") -> Path:
        """Har tayyorlov uchun alohida papka: boshqa (yoki shu egasining oldingi) tayyorlovi o'chmaydi."""
        self.update_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f"staging-{_now_stamp()}-", suffix=suffix, dir=self.update_dir))

    def _clear_staging(self) -> None:
        if self.update_dir.exists():
            for old in self.update_dir.glob("staging-*"):
                shutil.rmtree(old, ignore_errors=True)

    # ------------------------------------------------------------------ 2. o'rnatish
    def apply(self, staged: StagedUpdate, *, chat_id: int) -> Path:
        """Yangi fayllarni joyiga qo'yadi. Xato bo'lsa, joriy versiya zaxiradan to'liq tiklanadi."""
        backup = self.backup()
        try:
            self._install(staged.path)
            self.update_dir.mkdir(parents=True, exist_ok=True)
            self.pending_file.write_text(
                json.dumps(
                    {
                        "version": staged.version,
                        "previous": staged.current_version,
                        "notes": staged.notes,
                        "backup": str(backup),
                        "restore_from": str(staged.restore_from) if staged.restore_from else None,
                        "chat_id": chat_id,
                        "requirements_changed": staged.requirements_changed,
                        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            self.restore_backup(backup)
            self.pending_file.unlink(missing_ok=True)
            shutil.rmtree(backup, ignore_errors=True)
            raise
        self.discard(staged)
        return backup  # eski zaxiralar finalize() da tozalanadi — /qaytarish yoki tiklash zaxirani yo'qotmasin

    def _install(self, source_dir: Path) -> None:
        for source in sorted(source_dir.iterdir()):
            if source.name in NOT_INSTALLED:
                continue
            target = self.app_dir / source.name
            if source.is_dir():
                if source.name in REPLACED_DIRS:
                    self._replace_dir(source, target)
                else:
                    self._merge_dir(source, target)
            else:
                shutil.copy2(source, target)

    def restore_backup(self, backup: Path) -> None:
        for item in RELEASE_ITEMS:
            source = backup / item
            target = self.app_dir / item
            if source.is_dir():
                self._replace_dir(source, target)
            elif source.is_file():
                shutil.copy2(source, target)

    @staticmethod
    def _replace_dir(source: Path, target: Path) -> None:
        fresh = target.with_name(target.name + ".new")
        old = target.with_name(target.name + ".old")
        for leftover in (fresh, old):
            if leftover.exists():
                shutil.rmtree(leftover)
        shutil.copytree(source, fresh, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if target.exists():
            os.replace(target, old)
        os.replace(fresh, target)
        shutil.rmtree(old, ignore_errors=True)

    @staticmethod
    def _merge_dir(source: Path, target: Path) -> None:
        for item in source.rglob("*"):
            if item.is_file():
                destination = target / item.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)

    # ------------------------------------------------------------------ zaxira nusxalar
    def backup(self) -> Path:
        version = self.version
        safe_version = re.sub(r"[^0-9A-Za-z._-]", "_", version)
        stamp = _now_stamp()
        target = self.backups_dir / f"{stamp}-v{safe_version}"
        suffix = 1
        while target.exists():
            target = self.backups_dir / f"{stamp}-v{safe_version}-{suffix}"
            suffix += 1
        target.mkdir(parents=True)
        for item in RELEASE_ITEMS:
            source = self.app_dir / item
            if source.is_dir():
                shutil.copytree(source, target / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            elif source.is_file():
                shutil.copy2(source, target / item)
        if not (target / "VERSION").exists():
            (target / "VERSION").write_text(version + "\n", encoding="utf-8")
        return target

    def backups(self) -> list[Path]:
        if not self.backups_dir.exists():
            return []
        return sorted((p for p in self.backups_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)

    def backup_versions(self) -> list[tuple[str, datetime | None]]:
        """/versiya uchun: (versiya, yaratilgan vaqt UTC) — eng yangisi birinchi."""
        result = []
        for path in self.backups():
            try:
                created = datetime.strptime(path.name[:15], STAMP_FORMAT).replace(tzinfo=timezone.utc)
            except ValueError:
                created = None
            result.append((read_version(path), created))
        return result

    def prune_backups(self, keep: int = KEEP_BACKUPS) -> None:
        for old in self.backups()[keep:]:
            shutil.rmtree(old, ignore_errors=True)

    def _drop_backup(self, raw: str | None) -> None:
        """Faqat data/backups ichidagi papkani o'chiradi (pending.json buzilgan bo'lsa ham xavfsiz)."""
        if not raw:
            return
        path = Path(raw).resolve()
        if self.backups_dir.resolve() in path.parents:
            shutil.rmtree(path, ignore_errors=True)

    def stage_restore(self) -> StagedUpdate:
        """Oxirgi zaxira nusxani o'rnatishga tayyorlaydi (/qaytarish)."""
        backups = self.backups()
        if not backups:
            raise UpdateError("Qaytish uchun zaxira nusxa yo'q — oldingi versiya saqlanmagan.")
        latest = backups[0]
        staging = self._new_staging("-r")
        for item in RELEASE_ITEMS:
            source = latest / item
            if source.is_dir():
                shutil.copytree(source, staging / item)
            elif source.is_file():
                shutil.copy2(source, staging / item)
        return StagedUpdate(
            id=staging.name.removeprefix("staging-"),
            path=staging,
            version=read_version(staging),
            current_version=self.version,
            notes="",
            files=[item for item in RELEASE_ITEMS if (staging / item).exists()],
            requirements_changed=self._requirements_differ(staging),
            restore_from=latest,
        )

    # ------------------------------------------------------------------ 3. natija
    def finalize(self) -> UpdateNotice | None:
        """Bot Telegram'ga ulangach chaqiriladi: yangilanish natijasini egasiga yetkazish uchun."""
        self._clear_staging()
        if self.pending_file.exists():
            data = self._read_json(self.pending_file)
            self.pending_file.unlink(missing_ok=True)
            previous, version = data.get("previous", "?"), data.get("version", "?")
            if data.get("restore_from"):
                # Qaytish muvaffaqiyatli: ishlatilgan zaxira endi joriy versiya, tashlab ketilgani kerak emas
                self._drop_backup(data.get("restore_from"))
                self._drop_backup(data.get("backup"))
                text = f"↩️ Oldingi versiya tiklandi: v{previous} → v{version}"
            else:
                text = f"✅ Bot yangilandi: v{previous} → v{version}"
            self.prune_backups()
            if data.get("requirements_changed"):
                installed = self._requirements_installed()
                if installed is True:
                    text += "\n📦 Kerakli kutubxonalar ham o'rnatildi."
                elif installed is False:
                    text += (
                        "\n⚠️ Yangi kutubxonalar o'rnatilmadi (pip xato) — yangi funksiyalar ishlamasligi mumkin. "
                        "Bot har qayta ishga tushganda yana urinadi; serverda: docker compose up -d --build"
                    )
            if data.get("notes"):
                text += f"\n\n{data['notes']}"
            return UpdateNotice(chat_id=data.get("chat_id"), ok=True, text=text)
        if self.result_file.exists():
            data = self._read_json(self.result_file)
            self.result_file.unlink(missing_ok=True)
            text = (
                f"❌ v{data.get('version', '?')} ishga tushmadi — v{data.get('previous', '?')} avtomatik tiklandi, "
                "bot ishlayapti."
            )
            if data.get("error"):
                text += f"\n\nXato (oxirgi qatorlar):\n{data['error'][-1500:]}"
            return UpdateNotice(chat_id=data.get("chat_id"), ok=False, text=text)
        return None

    def _requirements_installed(self) -> bool | None:
        """Joriy requirements.txt o'rnatilganmi (launcher belgisi bo'yicha); bilib bo'lmasa — None."""
        requirements = self.app_dir / "requirements.txt"
        if not requirements.is_file():
            return None
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        error = self._read_json(self.update_dir / PIP_ERROR_FILE)
        if error.get("sha256") == digest:
            return False
        stamp = Path(os.environ.get("REQ_STAMP", REQ_STAMP_DEFAULT))
        try:
            installed = stamp.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return installed == digest

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ /kod
    def export_source(self) -> bytes:
        """Joriy kod (.env va data/ siz) — keyingi o'zgartirishlar uchun Claude'ga yuborish mumkin."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in EXPORT_ITEMS:
                path = self.app_dir / item
                if path.is_dir():
                    for file in sorted(path.rglob("*")):
                        if file.is_file() and not _skip(file.relative_to(self.app_dir)):
                            zf.write(file, f"{EXPORT_ROOT}/{file.relative_to(self.app_dir).as_posix()}")
                elif path.is_file():
                    zf.write(path, f"{EXPORT_ROOT}/{item}")
        return buffer.getvalue()


# ---------------------------------------------------------------------- GitHub
async def fetch_release(github: GitHubClient) -> tuple[Release, bytes]:
    """GitHub'dagi eng so'nggi versiya arxivini yuklaydi: Release aktivi, u bo'lmasa tarmoq manbasi."""
    release = await github.latest()
    data = await github.download(release)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise UpdateError("Arxiv juda katta (20 MB dan oshmasin).")
    return release, data


def describe_release(staged: StagedUpdate, release: Release) -> StagedUpdate:
    """GitHub'dan kelgan arxivga manba va (arxivda izoh bo'lmasa) release izohini qo'shadi."""
    staged.source = release.label
    if not staged.notes and release.notes:
        staged.notes = release.notes[:1500]
    return staged

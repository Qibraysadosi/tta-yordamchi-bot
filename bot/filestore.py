"""Bot yaratgan fayllarning nusxasi (data/files/) — Mini App galereyasi va qayta yuklab olish uchun.

Har fayl Telegram'ga yuborilgach shu yerga ham yoziladi: bazada yozuv (nom, tur, hajm, Telegram file_id),
diskda nusxa. Foydalanuvchi uchun hajm chegarasidan oshsa, eng eski nusxalar diskdan o'chiriladi
(yozuv va file_id qoladi — Telegram orqali qayta yuborish mumkin)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .db import Database, StoredFile

log = logging.getLogger(__name__)

KINDS = {
    ".docx": "docx", ".pdf": "pdf", ".pptx": "pptx", ".xlsx": "xlsx", ".csv": "csv", ".md": "md", ".txt": "txt",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".zip": "zip",
}


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    return KINDS.get(ext, "file")


class FileStore:
    def __init__(self, db: Database, root: Path, max_bytes: int) -> None:
        self.db = db
        self.root = root
        self.max_bytes = max_bytes

    def _path_for(self, user_id: int, file_pk: int, name: str) -> Path:
        safe = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._") or "fayl"
        return self.root / str(user_id) / f"{file_pk}-{safe[:100]}"

    async def save(
        self, user_id: int, conv_id: int, name: str, data: bytes, file_id: str | None, kind: str | None = None
    ) -> int:
        file_pk = await self.db.add_file(user_id, conv_id, name, kind or kind_of(name), len(data), file_id, None)
        path = self._path_for(user_id, file_pk, name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            await self.db.set_file_path(file_pk, str(path.relative_to(self.root)))
        except OSError as exc:
            log.warning("Fayl nusxasi yozilmadi (%s): %s", name, exc)
        await self._prune(user_id)
        return file_pk

    async def _prune(self, user_id: int) -> None:
        items = await self.db.files_on_disk(user_id)
        total = sum(item.size for item in items)
        for item in items:
            if total <= self.max_bytes:
                break
            self._unlink(item)
            await self.db.set_file_path(item.id, None)
            total -= item.size

    def read(self, item: StoredFile) -> bytes | None:
        if not item.path:
            return None
        path = self.root / item.path
        try:
            return path.read_bytes()
        except OSError:
            return None

    def _unlink(self, item: StoredFile) -> None:
        if item.path:
            try:
                (self.root / item.path).unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Fayl o'chirilmadi (%s): %s", item.path, exc)

    async def delete(self, item: StoredFile) -> None:
        self._unlink(item)
        await self.db.delete_file(item.id)

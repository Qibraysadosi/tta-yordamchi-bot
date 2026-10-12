"""Parol bilan kirish: parolning xeshi va xato urinishlar cheklovi."""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Callable

ITERATIONS = 200_000
MAX_ATTEMPTS = 5
LOCK_SECONDS = 30 * 60
MIN_PASSWORD_LENGTH = 6


def hash_password(password: str) -> str:
    """Parol ochiq holda saqlanmaydi — faqat tuzli PBKDF2 xeshi."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


class AttemptLimiter:
    """Ketma-ket MAX_ATTEMPTS marta xato urinishdan keyin LOCK_SECONDS ga bloklaydi."""

    def __init__(
        self,
        max_attempts: int = MAX_ATTEMPTS,
        lock_seconds: int = LOCK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.lock_seconds = lock_seconds
        self.clock = clock
        self._fails: dict[int, int] = {}
        self._locked_until: dict[int, float] = {}

    def locked_for(self, user_id: int) -> int:
        """Blok tugashiga qolgan soniyalar (0 — bloklanmagan)."""
        remaining = self._locked_until.get(user_id, 0.0) - self.clock()
        if remaining <= 0:
            self._locked_until.pop(user_id, None)
            return 0
        return int(remaining) + 1

    def fail(self, user_id: int) -> int:
        """Xato urinishni qayd qiladi va qolgan urinishlar sonini qaytaradi (0 — bloklandi)."""
        count = self._fails.get(user_id, 0) + 1
        if count >= self.max_attempts:
            self._fails.pop(user_id, None)
            self._locked_until[user_id] = self.clock() + self.lock_seconds
            return 0
        self._fails[user_id] = count
        return self.max_attempts - count

    def reset(self, user_id: int) -> None:
        self._fails.pop(user_id, None)
        self._locked_until.pop(user_id, None)

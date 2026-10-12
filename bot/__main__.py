"""Ishga tushirish: python -m bot (konteynerda uni launcher.py ishga tushiradi)."""
import asyncio
import sys

from .app import run
from .config import ConfigError
from .updater import RESTART_CODE


def main() -> None:
    try:
        restart = asyncio.run(run())
    except ConfigError as exc:
        print(f"❌ Sozlamada xato: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        return
    if restart:
        sys.exit(RESTART_CODE)  # launcher.py yangi versiyani ishga tushiradi


if __name__ == "__main__":
    main()

"""`python -m bot` ni soxta Telegram serveriga ulab ishga tushiradi (faqat e2e sinov uchun)."""
import os
import runpy
import sys

from aiogram.client.session import base
from aiogram.client.telegram import TelegramAPIServer

sys.path.insert(0, os.getcwd())
SERVER = TelegramAPIServer.from_base(os.environ["FAKE_TG"])
_original_init = base.BaseSession.__init__


def _init(self, *args, **kwargs):
    kwargs.setdefault("api", SERVER)
    _original_init(self, *args, **kwargs)


base.BaseSession.__init__ = _init
runpy.run_module("bot", run_name="__main__", alter_sys=True)

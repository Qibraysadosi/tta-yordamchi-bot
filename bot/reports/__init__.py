"""Call center hisobotlari (v1.7): lidlar guruhidagi statuslar va izohlar, kunlik hisobot, Mini App bo'limi.

- `store.py` — jadvallar bilan ishlash (rp_*), `leads.py` — lid kartochkasi va raqamlar
- `stats.py` — hisobot davri va hisob-kitob, `render.py` — Telegram matni
- `analysis.py` — izohlar tahlili (Claude), `service.py` — yozib borish, jadval, yuborish
- `handlers.py` — guruh yangilanishlari va /hisobot, /guruhlar buyruqlari"""
from .handlers import build_router
from .service import ReportService

__all__ = ["ReportService", "build_router"]

# TTA Yordamchi — Claude Code uchun loyiha qo'llanmasi

Shaxsiy Telegram bot (Claude API, aiogram 3, Python 3.13), Hostinger VPS'da Docker'da ishlaydi. Egasi botni
Telegram orqali yangilaydi: `.zip` arxiv yuborib (`/yangilash`) yoki GitHub Release'dan (`/yangilash github`), shuning uchun
har ish paketi tayyor arxiv bo'lib chiqadi: `VERSION` + `UPDATE_NOTES.md` → testlar → push → `build_release.py --publish`.
Texnik topshiriq (32 ta paket): `docs/TZ.md`. Mini App ekranlari: `docs/screens/`.

## Buyruqlar

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                      # barcha testlar (tests/, ~30 s)
python -m pyflakes bot scripts tests  # lint
python tests/e2e/e2e.py               # haqiqiy launcher + soxta Telegram (~2 daqiqa)
python scripts/build_release.py       # dist/claude-telegram-bot-v<VERSION>.zip + maxfiy satr tekshiruvi
python scripts/build_release.py --publish   # + GitHub Release (tag v<VERSION>) — egasi botda /yangilash github
python scripts/miniapp_shots.py       # Mini App skrinshotlari (playwright kerak)
```

## Kod xaritasi

- `bot/app.py` — ishga tushirish; `bot/handlers.py` — buyruqlar, tugmalar, suhbat (`_chat` → `_generate` → `ClaudeService.run_turn`)
- `bot/claude.py` — API oqimi, vositalar sikli (`tools` + `tool_runner`), narx; `bot/models.py` — modellar va narxlar
- `bot/tools.py` — Claude vositalari: JSON schema (`specs()`) + bajaruvchi (`run`) + `TOOLS_GUIDE`; yangi vosita shu yerga
- `bot/files.py`, `bot/pdfs.py`, `bot/doctrans.py`, `bot/export.py` — fayl yaratish/amallar; `bot/media.py` — kiruvchi fayllar
- `bot/db.py` — SQLite (`SCHEMA` + `MIGRATIONS`), `bot/filestore.py` — yaratilgan fayllar galereyasi
- `bot/webapp.py` + `bot/miniapp/` — Mini App API (Telegram `initData` imzosi) va frontend (framework'siz JS, `app.css` tokenlar, `icons.js`); ikki bo'lim: AI (`app.js`) va Hisobotlar (`report.js`, `window.TTAReport`), kirish `request[ACCESS]` = {ai, report, owner}
- `bot/reports/` — call center hisobotlari: `service.py` (guruhlarni jim yozib borish, kunlik hisobot, eslatmalar, 30 s sikl), `store.py` (`rp_*` jadvallari), `stats.py` (davr va `build_overview`), `analysis.py` (izoh tahlili, `REPORT_MODEL`, xarajat `user_id=0`), `render.py` (hisobot matni), `tasks.py` (v1.8 vazifalar: `#vazifa` belgisi, AI muddat, «Bajardingizmi?» holat mashinasi, `rp_tasks`), `api.py` (`/api/report/*`), `handlers.py` (guruh yangilanishlari, `/hisobot`, `/guruhlar`, `/vazifa`, `/vazifalar`, `tq:`/`tk:` tugmalar); testlar `tests/test_reports*.py`, `tests/test_tasks.py`
- `bot/updater.py`, `launcher.py` — yangilanish, zaxira, orqaga qaytish; `bot/github.py` — GitHub REST mijozi (`GitHubClient`: so'nggi release/aktiv yoki tarmoq arxivi)
- `bot/scheduler.py` — kunlik ishlar: zaxira, yangi versiya tekshiruvi (`UPDATE_CHECK`)
- `bot/config.py` — `.env` sozlamalari (hujjati `.env.example`); `prompts/system.md`, `prompts/modes/*.md` — ko'rsatmalar
- `tests/fakes.py` — soxta Anthropic SSE (`simple_answer`, `tool_answer`, `json_answer`) va soxta Telegram sessiyasi; `tests/test_flow.py` — `Env` yordamchisi

## Qoidalar

1. Foydalanuvchi ko'radigan matn va izohlar — o'zbek (lotin), qisqa. Buyruqlar o'zbekcha + inglizcha taxallus.
2. Maxfiy ma'lumot (token, kalit, parol) faqat `.env` da. Kodga, testga, logga, commit'ga tushmasin. Yangi sozlama = `config.py` + `.env.example` + README jumla.
3. Har paket: `VERSION` ko'tariladi (yangi funksiya — minor), `UPDATE_NOTES.md` qayta yoziladi (5–8 qator), README va `/yordam` yangilanadi, testlar yashil, `scripts/build_release.py` muvaffaqiyatli, commit `vX.Y.Z: …` ko'rinishida, push.
4. Bazaga o'zgarish — `SCHEMA` (`CREATE TABLE IF NOT EXISTS`) yoki `MIGRATIONS`; ustun o'chirilmaydi (orqaga qaytish eski kod bilan ishlashi kerak).
5. Har Claude chaqiruvi `usage_log` ga yoziladi; og'ir ishlar `asyncio.to_thread`; og'ir kutubxonalar funksiya ichida import qilinadi (`bot/app.py` → `OPTIONAL_LIBRARIES`).
6. Tashqi xizmatlar (STT, Google, Notion …) `bot/<xizmat>.py` da provayder abstraksiyasi bilan, testda soxta mijoz.
7. Arxivga faqat `bot/`, `prompts/`, `requirements.txt`, `VERSION`, `README.md`, `.env.example`, `install*.sh`, `docker-compose.yml` kiradi — `tests/`, `scripts/`, `docs/` serverga bormaydi.
8. Tushunarsiz talab bo'lsa — ishni boshlashdan oldin so'rash.

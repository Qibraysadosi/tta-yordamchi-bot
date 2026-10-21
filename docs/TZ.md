# TTA Yordamchi — Texnik topshiriq (v2, Claude Code uchun)

2026-10-12 · Shohrux Yerjanov

Bu hujjat `Qibraysadosi/tta-yordamchi-bot` reposidagi botni (hozir v1.5.0) «super bot»ga aylantirish uchun 32 ta ish paketini belgilaydi. Har paket Claude Code'ga alohida topshiriq sifatida beriladi: «docs/TZ.md dagi TZ-04 ni bajar». Loyiha qoidalari — `CLAUDE.md`.

## 1. Hozirgi holat (v1.5.0)

Bot Python 3.13 + aiogram 3.31 + anthropic SDK 1.13 da yozilgan, Hostinger VPS'da Docker'da ishlaydi (`/root/claude-telegram-bot`, `launcher.py` nazoratchi), bazasi SQLite (`data/bot.db`). Yangilanish `.zip` orqali (`/yangilash`), xato bo'lsa avtomatik orqaga qaytadi.

Bor funksiyalar: Fable/Opus/Sonnet/Haiku + ✨ Avto model; jonli oqimli javob va ⏹; internet qidiruvi; rasm/PDF/Word/Excel o'qish; havola o'qish; ish rejimlari (`prompts/modes/*.md`); javob tugmalari (qayta, qisqa, tarjima, Word, kirill); `/tarix`, `/shablon`, `/kirill`; byudjet nazorati va tejamkor rejim; kunlik zaxira; parol bilan kirish; vositalar tizimi (`bot/tools.py`: Word/Excel/PowerPoint/PDF yaratish, grafik, PDF amallari, format saqlangan tarjima, solishtirish); Mini App (`bot/webapp.py`, `bot/miniapp/`).

Kod tuzilmasi (har modul bitta vazifa):

| Fayl | Vazifasi |
| --- | --- |
| `bot/app.py` | yig'ish va ishga tushirish, menyu tugmasi, zaxira ishi, Mini App serveri |
| `bot/handlers.py` | Telegram buyruqlari, tugmalar, suhbat jarayoni (`_chat`, `_generate`, `run_chat_for`) |
| `bot/claude.py` | `ClaudeService.run_turn` — oqim, pause_turn, vositalar sikli, narx; `classify`, `translate_items` |
| `bot/tools.py` | Claude vositalari (JSON schema + bajaruvchi), `ToolContext`, `RawStore`, `TOOLS_GUIDE` |
| `bot/files.py`, `bot/pdfs.py`, `bot/doctrans.py`, `bot/export.py` | fayl yaratish va amallar |
| `bot/db.py` | SQLite: settings, messages, conversations, usage_log, members, templates, files, bot_config |
| `bot/webapp.py` + `bot/miniapp/` | Mini App API (initData imzosi) va frontend (framework'siz) |
| `bot/updater.py`, `launcher.py` | yangilanish, zaxira, orqaga qaytish |
| `bot/config.py` | barcha sozlamalar `.env` dan (`.env.example` — hujjat) |
| `bot/prompt.py`, `prompts/system.md`, `bot/modes.py` | tizim ko'rsatmasi va rejimlar |
| `bot/scheduler.py` | `DailyJob` — kunlik zaxira (rejalashtiruvchi asosi) |
| `tests/` | pytest (soxta Telegram + soxta Anthropic SSE), `tests/e2e/` — haqiqiy launcher bilan |
| `scripts/build_release.py` | chiqarish arxivi (.zip) + maxfiy ma'lumot tekshiruvi |

Ishga tushirish: `pip install -r requirements.txt -r requirements-dev.txt`, testlar `python -m pytest`, e2e `python tests/e2e/e2e.py`, arxiv `python scripts/build_release.py`.

## 2. Umumiy qoidalar (har paketga tegishli)

- Foydalanuvchiga ko'rinadigan barcha matnlar o'zbek tilida (lotin), qisqa va ishbilarmon; kod izohlari ham o'zbekcha. Buyruqlar nomi o'zbekcha, inglizcha taxallus bilan (`/eslatma`, `/reminder`).
- Maxfiy ma'lumot faqat `.env` da: tokenlar, kalitlar, parollar hech qachon kodga, testga, logga, arxivga yoki commit'ga tushmaydi. Yangi sozlama = `bot/config.py` da maydon + `.env.example` da izohli qator + README'da bir jumla.
- Har paket mustaqil `/yangilash` arxivi bo'lib chiqadi: `VERSION` ko'tariladi (yangi funksiya — minor: 1.6.0, tuzatish — patch), `UPDATE_NOTES.md` 5–8 qatorlik o'zbekcha izoh, `python scripts/build_release.py` arxivni yig'adi va maxfiy satrlarni tekshiradi. Arxivga faqat `bot/`, `prompts/`, `requirements.txt`, `VERSION`, `README.md`, `.env.example`, `install*.sh`, `docker-compose.yml` kiradi.
- Yangi kutubxona — faqat zarur bo'lsa, versiyasi mahkamlanadi (`paket==x.y.z`); og'ir kutubxonalar funksiya ichida import qilinadi, bo'lmasa bot ishga tushaveradi (`bot/app.py` → `OPTIONAL_LIBRARIES`).
- Bazaga o'zgarish — `SCHEMA` da `CREATE TABLE IF NOT EXISTS` yoki `MIGRATIONS` orqali, eski baza buzilmasdan yangilanadi; qaytarish (rollback) eski kod bilan ham ishlashi uchun ustunlar o'chirilmaydi.
- Claude chaqiruvlari: har so'rov `usage_log` ga yoziladi (`ctx.db.log_usage`), narx `bot/models.py` orqali; yangi vosita (tool) = `bot/tools.py` da schema + bajaruvchi + `TOOLS_GUIDE` ga 1 qator; uzoq ishlar `asyncio.to_thread`; foydalanuvchiga xato matni tushunarli, texnik tafsilot logga.
- Testlar: har paketga `tests/test_<paket>.py` — oqim testi (soxta Telegram + soxta Anthropic, `tests/fakes.py`) va birlik testlari; `python -m pytest` yashil, `python -m pyflakes bot` toza. Tashqi xizmatlar (STT, Google, Notion) testda soxta mijoz bilan almashtiriladi.
- Mini App'ga tegadigan paket: `bot/webapp.py` da API (JSON, `tma` imzosi) + `bot/miniapp/app.js` da ekran; dizayn tizimi `app.css` dagi tokenlar va `icons.js` ikonkalari — yangi ikonka shu to'plam uslubida (24×24, 1.75px chiziq).
- Commit xabari o'zbekcha, versiya bilan boshlanadi (`v1.6.0: eslatmalar va kunlik dayjest`), bitta paket — bitta commit (yoki PR).

**«Bajarildi» mezoni:** funksiya botda (va kerak bo'lsa Mini App'da) ishlaydi → testlar yashil → README va `/yordam` yangilangan → `VERSION` + `UPDATE_NOTES.md` → arxiv yig'ilgan va GitHub'ga push qilingan.

## 3. Ish paketlari

Ustuvorlik: P1 — birinchi navbatda, P2 — keyin, P3 — imkon bo'lganda. Hajm: S — yarim kun, M — 1–2 kun, L — 3–5 kun (Claude Code bilan). «Kalit» — siz olib `.env` ga yozadigan tashqi xizmat kaliti.

| ID | Paket | Ustuvorlik | Hajm | Kalit / shart |
| --- | --- | --- | --- | --- |
| TZ-01 | Xat blankalari kanaldan + rasmiy xat generatori | P1 | L | bot kanalga admin qilinadi |
| TZ-02 | Shartnoma va sertifikatlar (Excel ro'yxatdan PDF) | P1 | M | — |
| TZ-03 | Hisobot shablonlari (oylik hisobot) | P2 | M | — |
| TZ-04 | Uzoq muddatli xotira (`/xotira`) | P1 | M | — |
| TZ-05 | Bilimlar bazasi — hujjatlar bo'yicha javob | P1 | L | — |
| TZ-06 | Agent rejimi — ko'p qadamli vazifa | P2 | L | — |
| TZ-07 | Server vositalari: web_fetch va kod bajarish | P1 | M | Anthropic (kalit bor) |
| TZ-08 | Ovozli xabar → matn (STT) | P1 | M | OpenAI yoki Google STT |
| TZ-09 | Ovozli javob (TTS) | P3 | S | OpenAI TTS / ElevenLabs |
| TZ-10 | Rasm yaratish va tahrirlash | P2 | M | OpenAI Images yoki Replicate |
| TZ-11 | Eslatmalar va takroriy vazifalar | P1 | M | — |
| TZ-12 | Kunlik dayjest | P2 | M | — |
| TZ-13 | Monitoring (sahifa, narx, kurs) | P3 | M | — |
| TZ-14 | Kanalga avto-post (tasdiq bilan) | P2 | M | bot kanalga admin |
| TZ-15 | Google Sheets / Drive / Calendar | P1 | L | service account JSON |
| TZ-16 | Notion | P3 | M | Notion integration token |
| TZ-17 | Lidlar kanali tahlili → call center hisobotlari (v1.7.0 ✅, davomi pastda) | P1 | L | bot izohlar guruhiga admin |
| TZ-18 | Guruh va kanal rejimi | P2 | M | — |
| TZ-19 | Mini App: ilova ichida chat (oqim) | P2 | L | — |
| TZ-20 | Mini App: kontent-reja kalendari | P2 | M | — |
| TZ-21 | Mini App: vazifalar va eslatmalar | P2 | S | TZ-11 |
| TZ-22 | Mini App: bilimlar bazasi va shablonlar | P2 | M | TZ-05 |
| TZ-23 | Mini App: hujjat ustasi (forma) | P2 | M | TZ-01, TZ-02 |
| TZ-24 | Bo'lim bannerlari va xabar shablonlari | P2 | S | — |
| TZ-25 | Interfeys tili: o'zbek / rus | P3 | M | — |
| TZ-26 | GitHub'dan yangilash | P1 | M | GitHub token (private repo) |
| TZ-27 | Limitlar, rollar, audit jurnali | P2 | M | — |
| TZ-28 | Watchdog, salomatlik, loglar | P2 | M | — |
| TZ-29 | Evallar (prompt sifati sinovi) | P3 | M | — |
| TZ-30 | Tinglovchilar boti (FAQ, ro'yxat) | P2 | L | ikkinchi bot tokeni |
| TZ-31 | Tariflar va to'lov | P3 | L | Payme/Click yoki Stars |
| TZ-32 | Multi-tenant (bir kod — ko'p bot) | P3 | L | — |

## A. Hujjat generatorlari

### TZ-01. Xat blankalari kanaldan + rasmiy xat generatori

**Maqsad.** Egasi blankalarni (`.docx`) Telegram kanaliga tashlaydi, bot ularni o'zi oladi; foydalanuvchi «hokimiyatga bino so'rab xat yoz» desa, bot tanlangan blankada tayyor `.docx` (va PDF) yuboradi, chiquvchi raqam va sanani qo'yib, xatlar reestriga yozadi.

**Talablar.**
- Bot kanalga admin qilinganda `channel_post` yangilanishlarini oladi: `.docx` hujjat kelsa — yuklab, `data/templates/letters/` ga saqlaydi, `letter_templates` jadvaliga yozadi (id, nom = izoh yoki fayl nomi, file_id, maydonlar ro'yxati, yaratilgan sana). Rasm/PDF kelsa — «faqat .docx» deb egasiga xabar beradi. Eski postlar: egasi xabarni botga forward qilsa, xuddi shunday saqlanadi.
- Blankadagi o'rinbosarlar `{{raqam}}`, `{{sana}}`, `{{qabul_qiluvchi}}`, `{{mavzu}}`, `{{matn}}`, `{{imzo}}` (python-docx bilan run'lar ichida almashtiriladi; o'rinbosar yo'q bo'lsa, matn blankaning birinchi bo'sh abzatsidan keyin qo'yiladi). `/blanka` — ro'yxat, standartni tanlash, maydonlarni ko'rish, o'chirish (faqat egasi).
- Yangi vosita `write_letter` (`bot/tools.py`): `{template?, recipient, subject, body_markdown, signer?, attachments?}` → docx (+ `pdf: true` bo'lsa, LibreOffice bo'lmagan serverda PDF `files.build_pdf` orqali matn ko'rinishida, blankasiz — izohda aytiladi). Chiquvchi raqam `letters` jadvalidan: `YYYY/NNN` formati, yil boshida qaytadan 001; `/reestr` — oxirgi 20 xat, `/reestr excel` — butun yil Excel.
- `rasmiy_xat` rejimi bu vositani ishlatishga yo'naltiriladi (`prompts/modes/rasmiy_xat.md` yangilanadi): TKHTA uslubi, Sh. K. Yerjanov imzosi, sana o'zbekcha.

**Texnik.** `bot/letters.py` (shablon saqlash, o'rinbosarlar, raqamlash), handler'lar `bot/handlers.py` da (`on_channel_post`, `/blanka`, `/reestr`), `MIGRATIONS` ga `letter_templates`, `letters` jadvallari, `.env`: `LETTER_CHANNEL_ID` (ixtiyoriy — bo'lmasa har kanaldan, lekin faqat egasi admin qilgan).

**Qabul.** Kanalga tashlangan `.docx` 10 soniya ichida `/blanka` ro'yxatida; «xat yoz» so'rovi blankali `.docx` qaytaradi, raqam ketma-ket o'sadi, `/reestr` da ko'rinadi; blankasiz holatda bot oddiy docx beradi va buni aytadi. Testlar: kanal posti → saqlash; o'rinbosar almashtirish (run'lar bo'linganda ham); raqamlash yil almashganda.

### TZ-02. Shartnoma va sertifikatlar (Excel ro'yxatdan)

**Maqsad.** Tinglovchilar ro'yxati (Excel) + shablon (`.docx`) → har biriga alohida hujjat (shartnoma, sertifikat, ma'lumotnoma) va bitta umumiy PDF/zip.

**Talablar.** Hujjat shablonlari (`.docx`) kanal yoki forward orqali saqlanadi (`doc_templates`, turi: shartnoma | sertifikat | malumotnoma | hisobot | boshqa). Excel'da sarlavha qatori ustun nomlari o'rinbosarlarga mos keladi (`{{fio}}`, `{{kurs}}`, `{{sana}}`, `{{summa}}`). Vosita `mail_merge` `{template, rows_from_file?, rows?, output: docx|pdf|zip}` (500 qatorgacha): har qator uchun alohida docx. PDF uchun Docker image'ga LibreOffice headless qo'shiladi (`Dockerfile`: `libreoffice-writer-nogui`, ~300 MB; sozlama `PDF_ENGINE=libreoffice|none`, `none` bo'lsa faqat docx). Natija 10 tadan ko'p bo'lsa zip. Summa so'z bilan (`{{summa_sozda}}`) — `bot/numwords.py` (o'zbekcha).

**Qabul.** 50 qatorli Excel → 50 docx zip 60 soniyadan kam; `{{summa_sozda}}` to'g'ri (1 250 000 → «bir million ikki yuz ellik ming»); shablonda yo'q ustun — ogohlantirish, ishdan to'xtamaydi.

### TZ-03. Hisobot shablonlari

**Maqsad.** Bandlik bo'limi / hokimiyat uchun oylik hisobot: shablon (`.docx` yoki Excel) + ma'lumotlar (Excel yoki matn) → to'ldirilgan hisobot.

**Talablar.** `report_templates` (TZ-02 bilan bitta jadval, turi `hisobot`); vosita `fill_report` `{template, data_file?, period, notes?}`: Claude ma'lumotlarni jadval o'rinbosarlariga (`{{jadval:tinglovchilar}}` — python-docx jadvaliga qatorlar) va matn o'rinbosarlariga joylaydi; yakunda qisqa xulosa matni ham yoziladi. `/hisobot` buyrug'i: shablon tanlash → davr → fayl yuborish. Takroriy hisobotlar TZ-11 bilan rejalashtiriladi («har oyning 1-ida eslat»).

**Qabul.** Shablondagi jadval ustunlari saqlanib, qatorlar to'ldiriladi; davr sarlavhada o'zbekcha («2026-yil sentabr»); test: 3 qatorli ma'lumot → docx jadvalida 3 qator.

## B. Aqlli yadro

### TZ-04. Uzoq muddatli xotira

**Maqsad.** Bot foydalanuvchi haqida doimiy faktlarni eslab qoladi (tashkilot nomi, lavozim, imzo, tez-tez ishlatiladigan rekvizitlar, uslub talablari) va har javobda hisobga oladi — `/new` dan keyin ham.

**Talablar.** `memories` jadvali (user_id, kalit, matn, manba: `user|auto`, sana). `/xotira` — ro'yxat, `/xotira qo'sh <matn>`, `/xotira o'chir <raqam>`, `/xotira tozala`. Avtomatik yig'ish: har 5 ta savol-javobdan keyin (yoki `/new` da) arzon model (`QUICK_MODEL`) suhbatdan doimiy faktlarni JSON ro'yxat sifatida ajratadi (faqat foydalanuvchi aytgan faktlar; parol, karta, shaxsiy sezgir ma'lumotlar yozilmaydi), takrorlarini birlashtiradi, 40 tagacha saqlaydi. Tizim ko'rsatmasiga `Foydalanuvchi haqida` bo'limi sifatida qo'shiladi (keshni buzmaslik uchun blok o'zgarmas tartibda, oxirida). Mini App bosh sahifasida «Xotira» bo'limi (ko'rish, o'chirish).

**Qabul.** «Men TKHTA direktori Sh. K. Yerjanovman» deyilgandan keyin yangi suhbatda xat imzosi to'g'ri qo'yiladi; `/xotira o'chir` dan keyin fakt ishlatilmaydi; avtomatik yig'ish narxi `usage_log` da ko'rinadi. Test: soxta Anthropic JSON javobi → `memories` yozuvi; ko'rsatma matnida bo'lim.

### TZ-05. Bilimlar bazasi (hujjatlar bo'yicha javob)

**Maqsad.** Egasi nizom, kurs katalogi, narxlar, buyruqlar, qonun hujjatlarini bazaga yuklaydi; bot savollarga shu hujjatlardan iqtibos bilan javob beradi («kurs narxi qancha?» → narxlar jadvalidan).

**Talablar.** `/baza` (egasi): hujjat yuborish (`.pdf/.docx/.xlsx/.txt/.md`, 20 MB gacha) → matnga aylantiriladi (mavjud `bot/media.py`), 800–1200 belgili bo'laklarga bo'linadi, SQLite **FTS5** jadvali `kb_chunks` (doc_id, bo'lak, sarlavha) ga yoziladi; `kb_docs` (nom, hajm, bo'laklar soni, sana, kimga: hamma | faqat egasi). Qidiruv: so'rovdan kalit so'zlar (Claude emas, oddiy tokenizatsiya + o'zbekcha apostrof normallashtirish) → FTS5 `bm25` → eng yaxshi 8 bo'lak → Claude'ga `kb_search` vositasi natijasi sifatida. Vosita `kb_search {query, limit}` har suhbatda mavjud; `TOOLS_GUIDE`: «tashkilot hujjatlari haqida so'ralsa avval kb_search». Javobda manba: `📚 <hujjat nomi>`. Keyinroq embedding (Voyage) qo'shish uchun `kb_chunks.embedding BLOB` ustuni rezervda. Mini App: TZ-22.

**Qabul.** 5 ta hujjat yuklangach, ulardagi faktga savol → to'g'ri javob + manba; bazada yo'q savol → «hujjatlarda topilmadi» deb umumiy bilim bilan javob; 50 MB baza bilan qidiruv 200 ms dan kam. Test: FTS qidiruv o'zbekcha `o'`/`g'` variantlarida ishlaydi.

### TZ-06. Agent rejimi (ko'p qadamli vazifa)

**Maqsad.** «Keyingi hafta uchun 5 ta Instagram post tayyorla, har biriga rasm matni va Excel reja qil» kabi katta topshiriqni bot o'zi bosqichlarga bo'lib, vositalar bilan bajaradi va jarayonni ko'rsatadi.

**Talablar.** `/vazifa <matn>` yoki Claude o'zi `plan_task` vositasini chaqiradi: reja (3–8 qadam) chatga chiqadi, «▶ Boshlash / ✏ Tahrirlash» tugmalari; har qadam alohida Claude navbati (bir xil suhbat, qadam natijalari qisqa xulosa sifatida keyingi qadamga uzatiladi), jonli xabarda `☑ 1/5 Mavzular tanlandi` holati; fayllar oxirida bir to'plam qilib yuboriladi; umumiy limit: 12 qadam, $2 (sozlama `TASK_MAX_COST`), oshsa to'xtab so'raydi; `⏹` bilan to'xtatish; natija `tasks` jadvalida (TZ-21 ekrani uchun).

**Qabul.** 5 qadamli vazifa oxirigacha boradi, har qadam narxi jurnalda, to'xtatilgan vazifa `/vazifa davom` bilan davom etadi. Test: soxta Anthropic bilan 3 qadamli reja → 3 ta navbat → yakuniy xabar.

### TZ-07. Server vositalari: web_fetch va kod bajarish

**Maqsad.** Claude'ning o'zi sahifani o'qisin (havola berilmasa ham, qidiruv natijasidagi sahifani) va Excel/CSV tahlilini haqiqiy kod bilan (pandas) bajarsin: yig'indilar, guruhlash, grafiklar, formulali Excel.

**Talablar.** `ClaudeService.build_params` ga ikki server vositasi (sozlama bilan yoqiladi): `web_fetch` (`WEB_FETCH=on`, `max_uses=5`, `citations` yoqilgan) va `code_execution` (`CODE_EXECUTION=on`; so'rovga `anthropic-beta` sarlavhasi kerak — SDK'ning `extra_headers` orqali; rasmiy hujjatdagi joriy beta nomi `platform.claude.com/docs` dan tekshiriladi). Fayllar: foydalanuvchi yuborgan Excel/CSV Files API orqali yuklanadi (`client.beta.files.upload`), `file_id` `container_upload` bloki sifatida xabarga qo'shiladi; Claude yaratgan fayllar (`code_execution_tool_result` ichidagi `file_id`) yuklab olinib chatga yuboriladi va galereyaga yoziladi. Narx: kod bajarish seansi va fayllar `usage_log` ga alohida qator (`model='code_execution'`); `TOOLS_GUIDE`: «jadval hisob-kitobi, statistika, formulali Excel — code_execution; oddiy ro'yxat/jadval — create_document». Mavjud `create_chart`/`create_document` qoladi (arzon yo'l).

**Qabul.** 2000 qatorli Excel yuborilib «tumanlar bo'yicha yig'indi va diagramma» so'ralsa — to'g'ri Excel (formulalar bilan) va PNG keladi; web_fetch bilan «shu saytdagi narxlarni jadval qil» ishlaydi; vositalar o'chiq bo'lsa (`off`) hech narsa o'zgarmaydi. Test: soxta SSE'da `server_tool_use`/`code_execution_tool_result` bloklari → fayl yuborish va narx yozuvi.

## C. Ovoz va rasm

### TZ-08. Ovozli xabar → matn

**Maqsad.** Foydalanuvchi ovozli xabar (yoki audio fayl, video-nota) yuborsa, bot uni matnga o'girib, savol sifatida javob beradi; «faqat matnini yoz» desa — transkriptni beradi (uzun ovozlar: yig'ilish yozuvi → protokol).

**Talablar.** `bot/speech.py` — provayder abstraksiyasi `transcribe(data, mime, language_hint) -> (text, seconds, cost)`: `STT_PROVIDER=openai` (`whisper-1`/`gpt-4o-transcribe`, `STT_API_KEY`), `google` (Speech-to-Text v2, `uz-UZ`, service account — TZ-15 bilan bitta fayl), `off`. Telegram `voice` (ogg/opus) to'g'ridan-to'g'ri yuboriladi; 20 MB / 25 daqiqadan uzun bo'lsa — rad etish matni. Jonli xabarda `🎙 Ovoz matnga o'girilmoqda…`; transkript foydalanuvchiga `🎙 «…»` ko'rinishida qisqa ko'rsatiladi (200 belgigacha, davomi «to'liq matn» tugmasi), keyin odatdagi javob. Til: avto (o'zbek, rus, ingliz); `/ovoz til uz` bilan majburlash. Narx `usage_log` ga (`model='stt:openai'`). Guruhda (TZ-18) faqat `@bot` bilan javob qaytarilgan ovozlar. Kutubxona: faqat `httpx` (multipart) — SDK qo'shilmaydi.

**Qabul.** 30 soniyalik o'zbekcha ovoz → 10 soniyadan kam vaqtda matn + javob; kalit yo'q bo'lsa `/ovoz` bo'limida tushunarli xabar; test: soxta provayder bilan oqim, uzunlik chegarasi, narx yozuvi.

### TZ-09. Ovozli javob (TTS)

**Maqsad.** Foydalanuvchi xohlasa, javobni ovozda ham oladi (yo'lda eshitish, tinglovchilar uchun audio darslik).

**Talablar.** Javob ostidagi tugmalarga `🔊 Ovoz` qo'shiladi (`ans:voice:<id>`): matn `TTS_PROVIDER` (`openai` `gpt-4o-mini-tts` / `elevenlabs`) orqali `ogg/opus` ga aylantirilib `send_voice` qilinadi (4000 belgigacha; uzun matn — avval qisqartirish taklifi). `/ovoz` sozlamalari: ovoz turi, tezlik, avto-javob (har javob ovozda) yoqish/o'chirish; narx jurnalga.

**Qabul.** Tugma bosilganda 5 soniya ichida ovozli xabar keladi; kalit bo'lmasa tugma ko'rinmaydi.

### TZ-10. Rasm yaratish va tahrirlash

**Maqsad.** «Instagram post uchun rasm yasa», «shu rasm fonini olib tashla», «bannerga matn qo'sh».

**Talablar.** `bot/images.py` — provayder abstraksiyasi: `IMAGE_PROVIDER=openai` (`gpt-image-1`: generate + edit), `replicate` (Flux: `IMAGE_MODEL`), `off`. Vositalar: `generate_image {prompt, size: square|portrait|landscape, style?, count 1–2}`, `edit_image {instruction, mask?: none}` (oxirgi yuborilgan rasm `RawStore` dan). Claude promptni ingliz tiliga, brend uslubi (`prompts/brand.md`: TTA ranglari, matnsiz) bilan boyitadi. Natija `send_photo` + galereya; narx jurnalga (`model='image:openai'`); kunlik limit `IMAGE_DAILY_LIMIT` (standart 20). Instagram rejimi: post matni + rasm varianti birga. Fonni olib tashlash — `rembg` o'rnatilmaydi (og'ir); provayderning edit imkoniyati ishlatiladi.

**Qabul.** «Excel kursi uchun post rasmi» → 60 soniyada rasm; limit oshsa tushunarli rad; test: soxta provayder → `SendPhoto` + galereya yozuvi.

## D. Rejalashtiruvchi

Umumiy asos (TZ-11 da quriladi, qolganlari shundan foydalanadi): `bot/scheduler.py` kengaytiriladi — `jobs` jadvali (id, user_id, turi: `reminder|digest|monitor|post|task`, cron yoki bir martalik vaqt (mahalliy vaqt), payload JSON, keyingi ishga tushish, holat, oxirgi natija), har 30 soniyada tekshiruvchi sikl, o'tkazib yuborilgan ishlar bot qayta ishga tushganda bajariladi (zaxira ishi kabi), bir vaqtda bitta foydalanuvchi uchun bitta ish, xatolar 3 marta qayta uriniladi va egasiga yoziladi.

### TZ-11. Eslatmalar va takroriy vazifalar

**Maqsad.** «Ertaga 9:00 da hokimiyatga xat yuborishni eslat», «har dushanba 8:30 da haftalik rejani so'ra», «25-oktabr soat 15 da yig'ilish» — bot o'z vaqtida yozadi.

**Talablar.** Vosita `set_reminder {when: ISO yoki cron, repeat?: daily|weekly|monthly|cron, text, action?: remind|ask}` — Claude tabiiy tildan vaqtni ajratadi (bugungi sana va vaqt zonasi ko'rsatmada), noaniq bo'lsa so'raydi; `action=ask` bo'lsa bot vaqtida savolni o'zi Claude'ga beradi va javobni yuboradi (masalan, «har kuni 8 da bugungi valyuta kursini ayt»). `/eslatmalar` — ro'yxat (keyingi 10 ta), o'chirish/pauza tugmalari; eslatma xabarida `✅ Bajarildi`, `⏰ +1 soat`, `📅 Ertaga` tugmalari. Mini App: TZ-21.

**Qabul.** Bir martalik va takroriy eslatmalar 1 daqiqa aniqlikda keladi; bot 2 soat o'chiq turgan bo'lsa, qayta ishga tushganda o'tkazib yuborilganlar «kechikkan» belgisi bilan keladi; test: soxta vaqt bilan navbat, cron hisob-kitobi (`croniter` o'rniga o'z hisobi yoki `croniter==x` mahkamlangan).

### TZ-12. Kunlik dayjest

**Maqsad.** Har kuni belgilangan vaqtda bitta xabar: valyuta kurslari (CBU), ob-havo (Toshkent), tanlangan mavzular bo'yicha 3–5 yangilik (internet qidiruvi), bugungi eslatmalar va vazifalar, kechagi sarf.

**Talablar.** `/dayjest` — yoqish/o'chirish, vaqt, mavzular ro'yxati (`ta'lim, bandlik, AI`), manbalar (ixtiyoriy saytlar). Bo'limlar mustaqil: biri ishlamasa qolgani chiqadi. Kurslar `cbu.uz` JSON API dan (`https://cbu.uz/uz/arkhiv-kursov-valyut/json/`), ob-havo Open-Meteo (kalitsiz), yangiliklar Claude + web_search (Haiku/Sonnet, narx chegarasi `DIGEST_MAX_COST`). Xabar 1500 belgidan oshmaydi; HTML formatida; oxirida «Batafsil» tugmasi — to'liq variant.

**Qabul.** 7 kun davomida har kuni keladi, kurslar aniq, narxi $0.05 dan kam; test: soxta HTTP javoblar bilan matn yig'ilishi.

### TZ-13. Monitoring

**Maqsad.** «Shu sahifa o'zgarsa ayt» (tender e'lonlari, buyruqlar), «dollar 13 000 dan oshsa ayt», «sayt ishlamay qolsa ayt».

**Talablar.** Vosita `watch {url|rate|site, condition?, interval: 15m..24h}`; sahifa matni `bot/links.py` bilan olinadi, hash va muhim bo'lak saqlanadi; o'zgarganda Claude (Haiku) farqni 3 jumlada aytadi; valyuta sharti cbu.uz dan; sayt — HTTP status. `/monitoring` ro'yxati; 20 tadan ko'p kuzatuv — rad.

**Qabul.** Sahifa o'zgarganda 1 interval ichida xabar; o'zgarmasa xabar yo'q; test: ikki xil soxta sahifa → farq xabari.

### TZ-14. Kanalga avto-post

**Maqsad.** Instagram/Telegram postlarini bot kanalga belgilangan vaqtda joylaydi (tasdiq bilan); kontent-reja (TZ-20) bilan bog'lanadi.

**Talablar.** `/kanal` — egasi kanalni ulaydi (bot admin, `post_messages` huquqi; kanal ID forward orqali aniqlanadi), bir nechta kanal mumkin. Vosita `schedule_post {channel, text_html, image?: file_id, when}` — avval egasiga ko'rib chiqish xabari (`✅ Tasdiqlash / ✏ Tahrirlash / ❌`), tasdiqlangach `jobs` ga; vaqtida `send_message/send_photo` kanalga; natija (post havolasi) egasiga. Tahrirlash: tasdiqlashdan oldin matnni qayta yozdirish. Rasm — galereyadan yoki TZ-10.

**Qabul.** Rejalashtirilgan post aniq vaqtda kanalga chiqadi; tasdiqlanmagan post chiqmaydi; test: soxta kanalga yuborish, ruxsat yo'q xatosi egasiga tushunarli.

## E. Integratsiyalar

### TZ-15. Google Sheets, Drive, Calendar

**Maqsad.** «Kursga yozilganlar jadvaliga qo'sh», «shu oy to'lovlarini hisobla» (Google Sheets), «hisobotni Drive'ga saqla», «ertaga 15:00 ga yig'ilish qo'y» (Calendar).

**Talablar.** Kirish usuli — **service account** (OAuth oqimisiz): egasi Google Cloud'da service account yaratib JSON kalitni serverga `data/secrets/google.json` qilib qo'yadi (`.env`: `GOOGLE_SERVICE_ACCOUNT_FILE`), kerakli jadval/papka/kalendarni service account emailiga ulashadi. Kutubxona: `google-auth` + `httpx` bilan REST (og'ir `google-api-python-client` emas). Vositalar: `sheets_read {spreadsheet, range}` (ulangan jadvallar `/jadvallar` da nom bilan ro'yxatga olinadi, Claude nom bilan chaqiradi), `sheets_append {spreadsheet, sheet, rows}`, `sheets_update {range, values}` (o'chirish yo'q), `drive_upload {name, from: last_file|file_id}` → havola, `calendar_create {title, start, end, description, attendees?}`, `calendar_list {from, to}`. Har yozuv amali oldidan foydalanuvchiga nima yozilishi ko'rsatiladi va tasdiq so'raladi (inline tugma), `AUTO_CONFIRM_SHEETS=on` bo'lsa so'ralmaydi. Mini App'da ulangan manbalar ro'yxati.

**Qabul.** Ulashilgan jadvaldan ma'lumot o'qib savolga javob; qator qo'shish tasdiqdan keyin jadvalda paydo bo'ladi; kalendar hodisasi Google Calendar'da ko'rinadi; kalit yo'q — vositalar yuborilmaydi. Test: soxta Google REST javoblari.

### TZ-16. Notion

**Maqsad.** Notion'dagi sahifalar/bazalarda qidirish, sahifa yaratish (eslatmalar, kontent reja), bazaga yozuv qo'shish.

**Talablar.** `NOTION_TOKEN` (ichki integratsiya); vositalar `notion_search {query}`, `notion_page {id}` (matn), `notion_create_page {parent, title, markdown}`, `notion_add_row {database, properties}`; markdown → Notion bloklari konvertori (sarlavha, ro'yxat, jadval). Yozuv amallari tasdiq bilan.

**Qabul.** «Notion'dagi kurslar bazasidan Excel kursini top» ishlaydi; yaratilgan sahifa havolasi keladi.

### TZ-17. Lidlar kanali tahlili

**Maqsad.** TA lidlar kanaliga tushayotgan arizalarni bot o'qib, kunlik/haftalik statistika (kurslar, tumanlar, soat bo'yicha), javob berilmaganlar ro'yxati va Excel eksportini beradi; «bugun nechta ariza?» savoliga javob.

**Talablar.** Bot kanalga admin qilinadi (`LEADS_CHANNEL_ID`); har post (ikki format: «Ism/Telefon/Yoshi/tuman/kurs» va «YANGI ARIZA: …») Claude Haiku bilan JSON'ga ajratiladi (ism, telefonlar, yosh, tuman, kurs, manba) → `leads` jadvali (telefon — faqat oxirgi 4 raqami ochiq ko'rsatiladi, eksportda to'liq, faqat egasiga). Reaksiya statuslari (👍 👎 💔 🆒) `message_reaction` yangilanishidan o'qiladi (bot kanal admini bo'lsa). Vosita `leads_stats {period, group_by}`, `/lidlar` — bugun/hafta/oy, `/lidlar excel`. TZ-12 dayjestiga «kecha N ta ariza» qatori. Bot kanalga hech narsa yozmaydi.

**Qabul.** 100 ta eski post forward qilinganda 95% to'g'ri ajratiladi (test to'plami `tests/data/leads.txt`); kunlik statistika 1 soniyada; Excel eksport ustunlari: sana, ism, telefon, yosh, tuman, kurs, status.

### TZ-17 (yangilangan). Call center hisobotlari — egasi bilan kelishilgan reja (2026-10-21)

**Maqsad.** O'quv markazining call center ishini kunlik nazorat qilish: qaysi operator nechta lidga qo'ng'iroq qildi, nechtasi keladi / ko'tarmadi / kelmaydi; kunlik hisobot rahbarlar guruhiga; bajarilmagan vazifalarni eslatish; kundalik. Hammasi TTA Yordamchi ichida (alohida bot emas).

**TZ-17 dan farqlar.** Kanal reaksiyalari anonim (faqat sonlar) — shuning uchun statuslar kanalning **izohlar guruhidan** olinadi: kanal posti u yerga avtomatik forward bo'ladi, operatorlar shu nusxaga reaksiya va izoh qoldiradi (bot guruhda admin, reaksiyalar `message_reaction` bilan keladi). Lid kartochkasi Claude'siz, qoidalar bilan ajratiladi (`bot/reports/leads.py`); Claude faqat izohlarni tahlil qiladi (Haiku, `REPORT_MODEL`, telefonlar yashiriladi). TZ-18 (guruhda savol-javob) bilan aloqasi yo'q: hisobot uchun guruhlarda bot jim.

**Kelishilgan qarorlar.** Status manbai — reaksiya va izoh; mos kelmasa eng so'nggisi olinadi va «⚠️ ziddiyat» sanaladi. Operatorlar — egasi kiritgan username'lar; ro'yxatda yo'q odam qo'ng'iroq qilsa «➕ ro'yxatda yo'q» qatorida. Bir odam bir lidni bir marta sanaydi. Hisobot 19:00, du–sha, davr — oldingi hisobot vaqtidan (dam kuni keyingisiga qo'shiladi), kurslar kesimida; rahbarlar guruhiga (savol-javobsiz). Belgilanmagan lid — 30 daqiqadan keyin operatorlarga lichkada eslatma (ish vaqti 09:00–19:00). «Keladi» → «keldi» kuzatuvi hozircha kerak emas. Barcha sozlamalar (operatorlar, xodimlar, guruhlar, statuslar, istisnolar) — Mini App'da. Mini App ikki bo'lim: AI va Hisobotlar (ochilganda tanlov); Hisobotlarga egasi va rahbarlar kiradi (rahbarlar faqat ko'radi). Operatorlarga AI berilmaydi.

**Bosqichlar.**

| Versiya | Tarkib | Holat |
| --- | --- | --- |
| v1.7.0 | Guruh rejimi (jim yozib borish), lid kartochkasi, reaksiya + izoh statuslari (AI tahlil), kunlik hisobot, belgilanmagan lid eslatmasi, `/hisobot`, `/guruhlar`; Mini App: bo'lim tanlovi, Hisobotlar → Bugun / Operatorlar / Sozlamalar (kirish ham) | ✅ |
| v1.8.0 | Vazifalar: egasi «#vazifa» / «Vazifa:» / /vazifa bilan guruhda yoki botga lichkada beradi (belgisiz xabar vazifa emas; muddat bo'lmasa tugma bilan so'raladi, ✍️ — qabul qilindi, takroriy vazifalar); muddatda «📋 Vazifalar» guruhida ijrochini @username bilan belgilab «Bajardingizmi?» — «Ha» → egasiga xabar, «Yo'q» → 3 soatdan keyin qayta, javob bo'lmasa 2 soatdan keyin; 3 marta javobsiz — egasiga xabar; faqat ish vaqtida; tugmalarni faqat ijrochi bosa oladi; Mini App «Vazifalar» tabi (rahbarlar ko'radi). Ovozli vazifa — keyin (STT kaliti kerak) | ✅ |
| v1.9.0 | Kundalik (20:00, faqat egasiga lichkada; tarkibi — call center raqamlari va vazifalar, AI xulosasiz); haftalik (dushanba 09:00) va oylik (1-kun 09:00) Excel hisobot (Umumiy / Kunlar / Lidlar / Vazifalar) — rahbarlar guruhiga telefonlari yashirilgan, egasiga to'liq nusxa; `/kundalik`, `/excel`; Mini App «Arxiv» (kunlik, haftalik, oylik, kundalik) va Sozlamalar → «Kundalik va Excel» | ✅ |
| keyin | Mini App'da grafikli dashboard | — |

### TZ-18. Guruh va kanal rejimi

**Maqsad.** Botni ish guruhiga qo'shish: `@TTA_yordamchi_bot savol` yoki bot xabariga reply bilan javob oladi; kanal izohlarida (muhokama guruhi) belgilangan mavzularga javob.

**Talablar.** `AccessMiddleware` guruhlar uchun: faqat egasi qo'shgan guruhlar (`/guruh ruxsat` egasi buyrug'i, `groups` jadvali), guruhda faqat a'zolar yoki `GROUP_OPEN=on` bo'lsa hamma; javob faqat `@mention`, reply yoki `/savol` buyrug'ida; guruh suhbati alohida `conv_id` (guruh bo'yicha), tarix 20 xabar; fayllar guruhda ham; xarajat guruhni qo'shgan egasiga yoziladi; jonli oqim guruhda 3 soniyada bir tahrirlanadi (flood limit). Shaxsiy buyruqlar (`/parol`, `/yangilash`) guruhda ishlamaydi.

**Qabul.** Ruxsat berilmagan guruhda bot jim; ruxsat berilganda `@mention` ga javob; test: guruh yangilanishi (`chat.type=supergroup`) oqimi.

## F. Mini App v2

Umumiy: har ekran `bot/miniapp/app.js` da alohida `renderX()` funksiyasi, API `bot/webapp.py` da (`tma` imzosi, faqat egasi uchun bo'lganlar `/api/admin/...` kabi tekshiriladi), dizayn tokenlari `app.css` da, ikonkalar `icons.js` da. Har yangi ekran uchun Playwright skrinshoti (`scripts/miniapp_shots.py`) yorug' va qorong'i rejimda olinadi va `docs/screens/` ga qo'yiladi.

### TZ-19. Ilova ichida chat (oqimli javob)

**Maqsad.** Mini App'ning o'zida suhbatlashish: savol → javob o'sha yerda oqim bo'lib yoziladi, fayl biriktirish, javob tugmalari — Telegram chatiga qaytmasdan.

**Talablar.** `POST /api/chat` (SSE, `text/event-stream`): hodisalar `status`, `text` (to'plangan matn), `tool`, `file`, `done` (narx, javob id), `error`; server tomonida `run_chat_for` ning jonli xabar o'rniga `StreamListener` ni SSE ga yo'naltiradigan `WebListener` ishlatiladi (chatga ham yakuniy javob yuboriladi — sozlama `CHAT_MIRROR=on`, shunda tarix bir xil). Fayl yuklash: `POST /api/upload` (20 MB, `RawStore` + bloklar), rasm kamera orqali. Ekran: xabarlar ro'yxati (Markdown → HTML, kod bloklari), pastda kiritish maydoni va 📎, javob ostida «Word / Qisqa / Tarjima» tugmalari (mavjud API). `⏹` — `DELETE /api/chat/active`. Telegram'da ochilgan oqim bilan bir vaqtda ishlatilsa — «chatda javob yozilmoqda» xabari.

**Qabul.** 3 xabarli suhbat ilovada oqim bilan ko'rinadi, tarix `/tarix` da ham bor, narx yozilgan; aloqa uzilsa javob chatga baribir keladi. Test: aiohttp SSE mijozi bilan hodisalar ketma-ketligi.

### TZ-20. Kontent-reja kalendari

**Maqsad.** Instagram/Telegram postlarini oy bo'yicha rejalashtirish: kun, mavzu, matn holati, rasm, chop etish holati; Claude mavzular va matnlarni to'ldirishga yordam beradi.

**Talablar.** `content_plan` jadvali (user_id, sana, kanal, mavzu, matn, rasm file_id, holat: g'oya|yozilgan|tasdiqlangan|chop etilgan, job_id). Ekran: oy ko'rinishi (kunlarda nuqta), kun ro'yxati, element kartasi (tahrir, «Claude bilan yoz» → `POST /api/plan/{id}/generate` instagram rejimi bilan, «Rejalashtirish» → TZ-14 `schedule_post`). Botda `/kontent` — shu haftaning rejasi matn ko'rinishida; vosita `plan_content {month, topics?}` — Claude bir oylik g'oyalar ro'yxatini jadvalga qo'yadi.

**Qabul.** Oylik reja 20 ta g'oya bilan 1 daqiqada tuziladi, har bir g'oya alohida matnga aylanadi, tasdiqlangani TZ-14 orqali kanalga chiqadi.

### TZ-21. Vazifalar va eslatmalar ekrani

**Talablar.** TZ-11 `jobs` va TZ-06 `tasks` ustida: ro'yxat (bugun / hafta / barchasi), yangi eslatma formasi (sana-vaqt tanlagich, takror), pauza/o'chirish, bajarildi belgisi; agent vazifalari jarayoni (qadam holatlari). API: `GET/POST/PATCH/DELETE /api/jobs`.

**Qabul.** Ilovada qo'yilgan eslatma chatga vaqtida keladi; botdagi `/eslatmalar` bilan bir xil ro'yxat.

### TZ-22. Bilimlar bazasi va shablonlar boshqaruvi

**Talablar.** TZ-05 ustida: hujjatlar ro'yxati (nom, hajm, bo'laklar, kimga), yuklash (`POST /api/kb/upload`), o'chirish, qidiruvni sinash maydoni (natija bo'laklari); shablonlar (javob shablonlari + TZ-01/02 hujjat shablonlari) bitta ekranda: ko'rish, qayta nomlash, o'chirish, o'rinbosarlar ro'yxati.

**Qabul.** Yuklangan hujjat 10 soniyada qidiruvda topiladi; o'chirilgani topilmaydi.

### TZ-23. Hujjat ustasi (forma)

**Talablar.** TZ-01/02/03 vositalarini formasiz ishlatish: ekranda hujjat turi (xat / shartnoma / ma'lumotnoma / hisobot) → shablon → maydonlar (qabul qiluvchi, mavzu, matn yoki Excel fayl) → «Tayyorlash» → natija chatga va galereyaga; xat uchun matnni Claude yozib beradi (`POST /api/docs/draft` — mavzu va 2–3 ta kalit fakt bo'yicha rasmiy xat matni), reestr raqami avtomatik.

**Qabul.** Forma orqali 1 daqiqada tayyor xat; reestrda ko'rinadi; majburiy maydon bo'sh bo'lsa aniq xato.

## G. Dizayn va til

### TZ-24. Bo'lim bannerlari va xabar shablonlari

**Maqsad.** Bot bo'limlari (model, rejim, xarajat, tarix, admin, yangilanish, fayllar) bir uslubdagi rasmli sarlavha bilan ochiladi; xato, xarajat va tizim xabarlari bir xil shablonda (ikonka + sarlavha + 1–2 qator), har joyda bir xil ikonkalar.

**Talablar.** `bot/assets/` ga 6 ta banner (1280×640 JPEG, 150 KB gacha): mavjud `welcome.jpg` uslubida (indigo–binafsha gradient, `scripts/render_assets.py` — Playwright bilan HTML/SVG dan chiziladi, manbasi repoda); `branding.send_banner` orqali `file_id` keshi bilan yuboriladi; `/model`, `/rejim`, `/xarajat`, `/tarix`, admin panel, yangilanish natijasi shu bannerlar bilan. `bot/texts.py` — barcha foydalanuvchi matnlari bir joyda (`ERROR`, `INFO`, `COST` shablonlari: `{icon} <b>{title}</b>\n{body}`), `handlers.py` dagi tarqoq satrlar shu modulga ko'chiriladi (xatti-harakat o'zgarmaydi). Telegram emoji va Mini App ikonkalari mosligi jadvali README'da.

**Qabul.** Har bo'lim banner bilan ochiladi, ikkinchi marta rasm qayta yuklanmaydi (kesh testi mavjud); 100% matnlar `texts.py` dan; mavjud testlar o'zgarmay o'tadi.

### TZ-25. Interfeys tili: o'zbek / rus

**Maqsad.** Rus tilida ishlaydigan a'zolar uchun tugmalar, menyular va tizim xabarlari ruscha; Claude javobi — foydalanuvchi tilida (allaqachon shunday).

**Talablar.** `bot/texts.py` ikki lug'at (`uz`, `ru`), `settings.lang` (standart `uz`, `/til` buyrug'i va Mini App'da tanlov); `t(key, lang, **kw)` yordamchisi; buyruqlar menyusi (`set_my_commands`) tilga qarab `language_code` scope bilan; Mini App `app.js` da `I18N` lug'ati, `me.settings.lang` bo'yicha. Tizim ko'rsatmasiga «foydalanuvchi interfeys tili: ru» qatori. Barcha yangi paketlar matnlarni faqat `texts.py` orqali qo'shadi.

**Qabul.** `/til ru` dan keyin barcha tugma va xabarlar ruscha, `uz` ga qaytish ishlaydi; test: ikki tilda `/start`, lug'atlarda kalitlar to'plami bir xil (avtomatik tekshiruv).

## H. Xavfsizlik va ekspluatatsiya

### TZ-26. GitHub'dan yangilash

**Maqsad.** Yangi versiya GitHub'ga push qilingach, egasi botga `/yangilash github` deb yozadi (yoki tugmani bosadi) — bot arxivni o'zi yuklab, odatdagi tekshiruv va tasdiq bilan o'rnatadi; zip ko'chirish shart emas.

**Talablar.** `.env`: `GITHUB_REPO=Qibraysadosi/tta-yordamchi-bot`, `GITHUB_TOKEN` (faqat o'qish huquqi, egasi o'zi yozadi). `bot/updater.py`: `fetch_release()` — GitHub Releases'dan eng so'nggi `claude-telegram-bot-vX.Y.Z.zip` aktivi (release bo'lmasa `main` tarmog'i zipball'i: `VERSION` fayli orqali tekshiriladi); `/yangilash github` → «v1.5.0 → v1.6.0, o'zgargan fayllar: …» → `✅ O'rnatish`; `/yangilash tekshir` — faqat yangi versiya bormi; har kuni (zaxira vaqtida) avtomatik tekshirib, yangisi bo'lsa egasiga bir marta xabar (`UPDATE_CHECK=on`). `scripts/build_release.py` GitHub Release yaratishni ham biladi (`--publish`, `gh` yoki REST, token muhitdan). Claude Code ish tartibi: paket tayyor → `VERSION` + notes → push → release → egasi botda `/yangilash github`.

**Qabul.** Release chiqqach 1 daqiqada botdan o'rnatish mumkin; token yo'q — tushunarli xabar; buzuq release avtomatik orqaga qaytadi (mavjud mexanizm). Test: soxta GitHub API bilan oqim.

### TZ-27. Limitlar, rollar, audit jurnali

**Maqsad.** Kredit umumiy bo'lgani uchun a'zolarga kunlik/oylik limit, rollar (egasi / admin / a'zo / faqat o'qish), kim nima qilganini ko'rish.

**Talablar.** `members.role`, `members.daily_limit`, `members.monthly_limit` (standart `.env`: `MEMBER_DAILY_LIMIT=2`, `MEMBER_MONTHLY_LIMIT=20` dollar); limitga yetganda a'zo tejamkor modelga o'tadi yoki rad etiladi (`LIMIT_MODE=economy|block`); `/foydalanuvchilar` va Mini App admin ekranida limit va rol tahriri; `admin` roli: foydalanuvchilarni boshqaradi, lekin `/yangilash` va `/parol` qila olmaydi. `audit_log` jadvali: vaqt, kim, hodisa (kirish, parol, chiqarish, yangilash, fayl o'chirish, limit), tafsilot; `/audit` (egasi) — oxirgi 30, Mini App admin'da jadval. Shaxsiy ma'lumot (xabar matni) auditga yozilmaydi.

**Qabul.** A'zo kunlik $2 dan oshganda keyingi javob Haiku bilan va ogohlantirish; admin roli `/yangilash` ga kira olmaydi; audit yozuvlari to'g'ri.

### TZ-28. Watchdog, salomatlik, loglar

**Maqsad.** Bot yoki Mini App to'xtab qolsa egasi darhol biladi; loglarni serverga kirmasdan ko'radi.

**Talablar.** (1) Konteyner ichida `launcher.py` bolani kuzatadi (bor); (2) `docker-compose.yml` ga `healthcheck` (`python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${WEBAPP_PORT}/health')"` yoki Mini App o'chiq bo'lsa `data/heartbeat` fayli yangilanishi); (3) alohida yengil `watchdog` servisi (`watchdog.py`, 20 MB image): har daqiqa `getMe` va `health` ni tekshiradi, 3 marta ketma-ket xato bo'lsa egasiga Telegram orqali xabar (`WATCHDOG_BOT_TOKEN` — xuddi shu bot tokeni, alohida jarayon), tiklanganda ham xabar; (4) Mini App admin ekranida «Loglar»: oxirgi 200 qator (`docker` emas — bot o'z logini `data/logs/bot.log` ga aylanma (`RotatingFileHandler`, 5×5 MB) yozadi), filtr: xato/ogohlantirish; `/log` buyrug'i oxirgi 30 qator (egasi). Loglarda tokenlar va foydalanuvchi matni bo'lmaydi.

**Qabul.** Bot jarayoni o'ldirilganda 3 daqiqa ichida egasiga xabar keladi; `/log` da oxirgi xatolar; healthcheck `docker ps` da `healthy`.

### TZ-29. Evallar (prompt sifati sinovi)

**Maqsad.** Tizim ko'rsatmasi yoki rejim matnini o'zgartirganda sifat tushib ketmaganini avtomatik tekshirish.

**Talablar.** `evals/` papkasi: 30–50 ta namuna (`cases.yaml`: savol, rejim, kutilgan belgilar — til lotin, uzunlik chegarasi, kerakli so'zlar, fayl vositasi chaqirilishi kerakmi); `scripts/run_evals.py` haqiqiy API bilan (Haiku/Sonnet, narx ≈ $0.5) ishga tushadi, natija jadval (`o'tdi/yiqildi`, narx, vaqt) va `evals/results/<sana>.md`; baholovchi — Claude (Haiku) 1–5 ball + qoidalar. GitHub Actions'da qo'lda ishga tushiriladigan workflow (`workflow_dispatch`, `ANTHROPIC_API_KEY` secret).

**Qabul.** `python scripts/run_evals.py --quick` 5 daqiqada tugaydi; prompt o'zgarishi PR'ida natija jadvali bor.

## I. Mahsulot

### TZ-30. Tinglovchilar boti

**Maqsad.** TTA tinglovchilari va abituriyentlar uchun alohida bot (ikkinchi token): kurslar, narxlar, jadval, hujjatlar ro'yxati, ro'yxatdan o'tish arizasi, dars materiallari — bilimlar bazasi (TZ-05) asosida, arzon model bilan; egasiga arizalar keladi.

**Talablar.** Bir kod bazasi, `BOT_PROFILE=assistant|students` (`.env`): `students` profilida parol yo'q (hamma kiradi), buyruqlar: `/kurslar`, `/narxlar`, `/ariza` (ism, telefon, kurs — bosqichma-bosqich, `applications` jadvali, egasiga xabar + Google Sheets TZ-15 bo'lsa qator), `/manzil`, `/savol` (bilimlar bazasidan, Haiku/Sonnet, kunlik limit har foydalanuvchiga `STUDENT_DAILY_LIMIT=20` so'rov), ovozli savol (TZ-08). Tizim ko'rsatmasi `prompts/students.md` (faqat TTA mavzusida, boshqa mavzuga muloyim rad). Admin: `/arizalar` (bugun/hafta, Excel), `/elon` — barcha foydalanuvchilarga xabar (tasdiq bilan, soatiga 20 ta). Mini App: kurslar katalogi va ariza formasi (alohida `miniapp/students/`).

**Qabul.** Yangi foydalanuvchi `/start` dan 1 daqiqada ariza qoldira oladi; egasiga xabar keladi; mavzudan tashqari savolga rad; kunlik limit ishlaydi.

### TZ-31. Tariflar va to'lov

**Maqsad.** Bot xizmatini boshqa tashkilotlarga/odamlarga pullik berish: tariflar (so'rovlar soni, modellar, fayl vositalari), to'lov Telegram Stars (eng oson) yoki Payme/Click (so'm).

**Talablar.** `plans` (nom, narx, oylik limit $, modellar ro'yxati, vositalar), `subscriptions` (user_id, plan, boshlanish, tugash, holat); `/tarif` — tariflar va sotib olish (`sendInvoice`, Stars uchun `currency=XTR`; Payme/Click — `PAYMENT_PROVIDER_TOKEN` orqali Telegram Payments); `pre_checkout_query` va `successful_payment` ishlovi; obuna tugashidan 3 kun oldin eslatma; limit TZ-27 mexanizmi bilan; Mini App'da tarif sahifasi. Chek va hisobotlar egasiga; `MONTHLY_BUDGET` dan tashqari pullik foydalanuvchilar sarfi alohida ko'rsatiladi.

**Qabul.** Sinov to'lovi (Stars test rejimi) obunani faollashtiradi; muddati tugaganda model/limit pasayadi; qaytarish (refund) buyrug'i egasida.

### TZ-32. Multi-tenant

**Maqsad.** Bitta server, bitta kod — bir nechta tashkilot uchun alohida botlar (har birida o'z tokeni, egasi, prompti, bazasi).

**Talablar.** `tenants/<nom>/.env` + `data/<nom>/` tuzilmasi; `launcher.py` bir nechta bot jarayonini boshqaradi (yoki docker-compose'da profil per tenant — tanlov hujjatlashtiriladi); umumiy kod yangilanishi hamma tenantga bir vaqtda (`/yangilash` super-egasi orqali); Mini App yo'li `WEBAPP_URL` tenantga qarab; xarajat hisoboti tenant bo'yicha; `scripts/new_tenant.sh`.

**Qabul.** Ikkita bot bir serverda mustaqil ishlaydi, biri yiqilsa ikkinchisiga ta'sir qilmaydi, yangilanish ikkalasiga tushadi.

## Bajarish tartibi

Tavsiya qilingan navbat (har qator — bitta chiqarish, v1.6.0 dan boshlab):

| Navbat | Paketlar | Nega shu tartib |
| --- | --- | --- |
| 1 | TZ-26, TZ-11, TZ-04, TZ-07 | GitHub'dan yangilash keyingi barcha chiqarishlarni osonlashtiradi; rejalashtiruvchi asosi va xotira ko'p paketlarga kerak; server vositalari arzon va tez natija |
| 2 | TZ-08, TZ-05, TZ-01, TZ-02 | ovozli xabarlar (kalit kerak), bilimlar bazasi va hujjat generatorlari — kundalik ish uchun eng foydali |
| 3 | TZ-15, TZ-12, TZ-14, TZ-17, TZ-18 | integratsiyalar va avtomatik xabarlar |
| 4 | TZ-21, TZ-22, TZ-20, TZ-19, TZ-23, TZ-24 | Mini App v2 va dizayn — oldingi paketlar ekranlari |
| 5 | TZ-06, TZ-10, TZ-27, TZ-28, TZ-03 | agent rejimi, rasmlar, nazorat va ishonchlilik |
| 6 | TZ-30, TZ-25, TZ-09, TZ-13, TZ-16, TZ-29, TZ-31, TZ-32 | mahsulot va qo'shimchalar |

**Egasidan kerak bo'ladigan narsalar** (paket boshlanishidan oldin, kalitlar faqat serverdagi `.env` ga yoziladi):

- GitHub token (faqat `repo` o'qish) — TZ-26.
- OpenAI API kaliti — TZ-08 (Whisper), TZ-09, TZ-10; yoki Google service account JSON — TZ-08 (Speech) va TZ-15 (Sheets/Drive/Calendar, jadvallarni service account emailiga ulashish).
- Botni kanallarga admin qilish: blankalar kanali (TZ-01), post kanali (TZ-14), lidlar kanali (TZ-17).
- Notion ichki integratsiya tokeni — TZ-16; ikkinchi bot tokeni — TZ-30; Payme/Click yoki Stars — TZ-31.

**Claude Code'ga topshiriq namunasi** (repo ochilgan holda):

```
CLAUDE.md va docs/TZ.md ni o'qi. TZ-26 paketini bajar: talablar va qabul mezonlariga to'liq rioya qil,
testlar yoz (tests/test_github_update.py), python -m pytest yashil bo'lsin, README va /yordam ni yangila,
VERSION=1.6.0, UPDATE_NOTES.md yoz, python scripts/build_release.py bilan arxiv yig', commit qilib push qil.
Tushunarsiz joy bo'lsa boshlashdan oldin so'ra.
```

Bir paket — bir suhbat; katta paketlar (L) ikki qismga bo'linishi mumkin (avval bot, keyin Mini App ekrani). Har chiqarishdan keyin arxivni avval sinov botiga (`install-test.sh`), keyin asosiy botga o'rnating.

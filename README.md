# Claude Telegram bot (shaxsiy yordamchi)

Claude API asosida ishlaydigan shaxsiy Telegram bot. Max tarifidagi oylik $100 API kredit bilan ishlaydi.

## Imkoniyatlar

- **Mini App (📱 /ilova):** Telegram ichidagi ilova — sozlamalar (model, fikrlash, qidiruv, rejim), suhbatlar tarixi va qidiruv, yaratilgan fayllar galereyasi, xarajat grafiklari, egasi uchun admin panel; Telegram mavzusiga mos yorug'/qorong'i ko'rinish. Chatning menyu tugmasidan yoki /ilova bilan ochiladi (sozlash: pastda «Mini App»)
- **Fayl yaratish:** «Word qilib ber», «Excel jadval tuz», «taqdimot tayyorla», «PDF qil» — bot Word (.docx), Excel (.xlsx), PowerPoint (.pptx), PDF, CSV, Markdown yoki matn faylni o'zi yasab chatga yuboradi; raqamli ma'lumot bo'yicha grafik (ustunli, chiziqli, doira) chizadi
- **Fayllar ustida amallar:** skaner rasmi yoki PDF → Word (matn ko'chiriladi); Word faylni formatini saqlab tarjima qilish; PDF'larni birlashtirish, sahifalarini olish/bo'lish/burish, rasmlarni PDF qilish; ikki hujjatni solishtirish — oddiy so'z bilan so'raladi
- **Modellar:** Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5 — `/model` bilan almashtiriladi; **✨ Avto** savol murakkabligiga qarab arzon yoki kuchli modelni o'zi tanlaydi
- **Ish rejimlari:** `/rejim` — rasmiy xat (TKHTA nomidan), Instagram post, Meta reklama, tarjimon, dasturchi
- **Javob ostidagi tugmalar:** 🔄 qayta, ✂️ qisqartirish, 🌐 tarjima (uz/ru/en), 📄 Word fayl, 🔡 kirill yozuvi
- **Suhbatlar tarixi:** `/tarix` — eski suhbatlar ro'yxati va qidiruv, istalganiga qaytib davom ettirish
- **Shablonlar:** `/shablon` — tez-tez yuboriladigan so'rovlar bir tugmada
- **Havolalarni o'qish:** maqola yoki PDF havolasi yuborilsa, bot uni o'qib chiqadi
- **Byudjet nazorati:** kredit 50/80/100% ga yetganda egasiga xabar; 90% dan keyin hamma tejamkor modelga o'tadi
- **Kunlik zaxira:** `data/bot.db` nusxasi har kuni egasiga yuboriladi (`/zaxira` — hozir)
- **Lotin ↔ kirill:** `/kirill`, `/lotin`
- **Fikrlash darajasi:** `/effort` (low … max)
- **Fayllar:** rasm, PDF, Word (.docx), Excel (.xlsx), matn/kod fayllari. Albom (bir nechta rasm) ham bo'ladi
- **Internet qidiruvi:** yangi ma'lumot kerak bo'lsa Claude o'zi qidiradi, manbalarni ko'rsatadi (`/qidiruv` bilan yoq/o'chir)
- **Xarajat hisoboti:** har javob ostida narx va bugungi sarf, `/xarajat` — kun/davr/model bo'yicha
- **Jonli javob:** javob yozilayotganini ko'rib turasiz, ⏹ tugmasi bilan to'xtatish mumkin
- **Suhbat xotirasi:** bot oldingi xabarlarni eslaydi, `/new` — yangi suhbat
- **Zaxira model:** Fable yoki Opus so'rovni rad etsa (kiberxavfsizlik, biologiya kabi mavzular), javobni avtomatik Opus 4.8 beradi
- **Chiroyli ko'rinish:** `/start` — rasmli bosh menyu va tez tugmalar (model, fikrlash, qidiruv, xarajat); bot profili (rasm, tavsif) yangilanishda avtomatik o'rnatiladi
- **Parol bilan himoya:** egasi (`OWNER_IDS`) har doim kiradi; boshqalar faqat siz o'rnatgan parolni yozib kiradi
- **Kompyutersiz yangilash:** yangi versiyani .zip qilib botga yuborasiz — bot o'zini yangilaydi, ishlamasa eski versiyani o'zi tiklaydi

## Buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `/new` | Yangi suhbat (kontekst tozalanadi — javoblar arzonlashadi) |
| `/model` | Modelni tanlash (✨ Avto, Fable, Opus, Sonnet, Haiku) |
| `/rejim` | Ish rejimi: rasmiy xat, Instagram post, Meta reklama, tarjimon, dasturchi |
| `/tarix` | Suhbatlar tarixi; `/tarix so'z` — qidiruv |
| `/shablon` | Shablonlar; `/shablon qo'sh Nomi` + keyingi qatorda matn |
| `/kirill`, `/lotin` | Yozuvni o'girish (matn yoki xabarga reply) |
| `/ilova` | Mini App: sozlamalar, tarix, fayllar, xarajat (egasi uchun admin panel ham) |
| `/effort` | Fikrlash darajasi |
| `/qidiruv` | Internet qidiruvini yoqish/o'chirish |
| `/xarajat` | Sarf hisoboti; har javob ostidagi narxni yoqish/o'chirish |
| `/yordam` | Yordam |

Faylni izohsiz yuborsangiz, bot uni keyingi savolingiz bilan birga ko'rib chiqadi. Yuborilgan fayllar 2 soat davomida fayl amallari (PDF, tarjima, solishtirish) uchun eslab turiladi.

**Fayl yaratish — misollar:**

- «Shu ma'lumotlar asosida Excel jadval tuz» · «Hisobotni Word qilib ber» · «5 slaydlik taqdimot tayyorla» · «PDF qilib yubor»
- «Oylar bo'yicha sotuv grafigini chiz» (Excel fayl yuborib)
- Skaner rasmini yuborib: «Word hujjat qilib ber»
- Word fayl yuborib: «rus tiliga tarjima qil, formatini saqla»
- Ikki PDF yuborib: «ikkalasini birlashtir» · «2–5 sahifalarini alohida PDF qil» · «har sahifani alohida fayl qil»
- Ikki hujjat yuborib: «farqlarini ko'rsat»

Bot fayl yaratganda javob ostida «📎 1» (yuborilgan fayllar soni) ko'rinadi. Word tarjimasi abzats uslubi, jadval va rasmlarni saqlaydi; abzats ichidagi aralash formatlash (bitta so'z qalin) birinchi so'z uslubiga tenglashadi.

**Faqat egasi uchun** (menyuda ham faqat sizga ko'rinadi):

| Buyruq | Vazifasi |
|---|---|
| `/parol YANGI_PAROL` | Kirish parolini o'rnatish yoki almashtirish (`/parol off` — o'chirish) |
| `/foydalanuvchilar` | Parol bilan kirganlar, ularni chiqarish yoki qaytarish |
| `/yangilash` | Botni yangilash: buyruqdan keyin .zip yuborasiz |
| `/versiya` | Joriy versiya va qaytish mumkin bo'lgan versiyalar |
| `/qaytarish` | Oldingi versiyaga qaytish |
| `/kod` | Botning joriy kodi (.zip) — yangi funksiya so'raganda Claude'ga yuborish uchun |
| `/zaxira` | Bazaning zaxira nusxasini hozir olish |

## Mini App (Telegram ichidagi ilova)

Ilova botning o'zida ishlaydi (alohida server kerak emas): konteyner ichida `WEBAPP_PORT` (8091) portida HTTP server turadi, docker-compose uni faqat `127.0.0.1` ga ochadi. Telegram ilovani faqat **HTTPS** manzildan ochadi, shuning uchun serverdagi Caddy (yoki Nginx) orqali tashqariga chiqariladi. Kirish: Telegram imzosi (initData) bot tokeni bilan tekshiriladi, ilovaga faqat egasi va parol bilan kirganlar kiradi.

**1. Manzil tanlang.** Eng oson yo'l — serverda allaqachon HTTPS bilan ishlayotgan domenga papka qo'shish, masalan `https://bepulkurs.uz/yordamchi/`. Caddyfile'ga (`/etc/caddy/Caddyfile`) shu domen blokiga qo'shing:

```
bepulkurs.uz {
    # ... mavjud sozlamalar o'z joyida qoladi ...
    redir /yordamchi /yordamchi/ 301
    handle_path /yordamchi/* {
        reverse_proxy 127.0.0.1:8091
    }
}
```

va `systemctl reload caddy`. Alohida subdomen istasangiz — DNS'da `A` yozuv (`yordamchi.bepulkurs.uz → server IP`) va Caddyfile'da:

```
yordamchi.bepulkurs.uz {
    reverse_proxy 127.0.0.1:8091
}
```

**2. `.env` ga yozing** (`/root/claude-telegram-bot/.env`):

```
WEBAPP_URL=https://bepulkurs.uz/yordamchi/
WEBAPP_PORT=8091
```

**3. Konteynerni qayta yarating** (port va `.env` o'zgargani uchun bir marta): `docker compose up -d --force-recreate`. Bot ishga tushganda chatning menyu tugmasini «Ilova» qilib qo'yadi; `/start` menyusida va `/ilova` da 📱 tugma chiqadi.

Tekshirish: serverda `curl -s http://127.0.0.1:8091/health` → `{"ok": true, ...}`; brauzerda `https://bepulkurs.uz/yordamchi/` sahifasi «Faqat Telegram ichida» deb ochilsa — hammasi joyida (ilovaning o'zi faqat Telegram'dan ishlaydi).

Ilova nimalarni qiladi: model/fikrlash/qidiruv/rejimni almashtiradi, chatga savol yuboradi (javob chatda yoziladi), eski suhbatni davom ettiradi, bot yaratgan fayllarni qayta yuboradi yoki yuklab beradi (nusxalar `data/files/` da, har foydalanuvchi uchun `FILES_MAX_MB`), 30 kunlik sarf grafigi va modellar bo'yicha taqsimot; egasi uchun — parol, foydalanuvchilar, zaxira nusxa va oldingi versiyaga qaytish (tasdiq chatga keladi).

## Sinov boti

Yangilanishni avval sinov botida tekshirish uchun:

1. @BotFather'da ikkinchi bot oching (masalan, `TTA_yordamchi_test_bot`) va tokenini oling.
2. Serverda: `cd /root/claude-telegram-bot && bash install-test.sh` — token so'raydi va `../claude-telegram-bot-test` papkasida alohida konteyner ishga tushiradi (API kalit asosiy `.env` dan olinadi, baza alohida).
3. Yangi `.zip` ni avval sinov botiga `/yangilash` bilan yuboring, ishlasa — asosiy botga.

## Parol bilan kirish

1. Botga `/parol SIZNING_PAROLINGIZ` yozing (kamida 6 belgi). Bot xabaringizni darhol o'chiradi, bazada parol emas, faqat uning xeshi saqlanadi.
2. Parolni kerakli odamga ayting. U botni ochib parolni yozadi — kiradi, sizga «👤 Yangi foydalanuvchi…» xabari keladi.
3. `/foydalanuvchilar` — kim kirganini va joriy oydagi sarfini ko'rasiz, ❌ tugmasi bilan chiqarasiz.

- Parolni almashtirsangiz, avval kirganlar qoladi (kerak bo'lsa, ularni ro'yxatdan chiqaring).
- 5 marta xato yozgan odam 30 daqiqaga bloklanadi.
- Kredit hammaga umumiy: `/xarajat` da siz barcha foydalanuvchilar sarfini ko'rasiz, ular — faqat o'zinikini.

## Yangilash (kompyutersiz, Telegram orqali)

1. Claude'dan yangi versiyani `.zip` qilib oling.
2. Botga `/yangilash` yozing, keyin shu `.zip` faylni yuboring (yoki faylni `/yangilash` izohi bilan yuboring).
3. Bot arxivni tekshiradi va nima o'zgarishini ko'rsatadi → **✅ O'rnatish** ni bosing.
4. Bot 10–30 soniyaga qayta ishga tushadi va «✅ Bot yangilandi: v… → v…» deb yozadi.

Xavfsizlik:
- Har yangilanishdan oldin joriy versiya zaxiraga olinadi (oxirgi 5 tasi saqlanadi).
- Yangi versiya ishga tushmasa, eski versiya **avtomatik** tiklanadi va sizga xato matni yuboriladi — shu matnni Claude'ga yuboring.
- `/qaytarish` — oxirgi yangilanishni bekor qiladi (ketma-ket bosilsa, yanada oldingi versiyaga qaytadi).
- Arxivdan `bot/`, `prompts/`, `requirements.txt`, `VERSION` (va `install*.sh`, `docker-compose.yml`, `README.md`) olinadi; `.env`, baza, `Dockerfile` va `launcher.py` ga tegilmaydi. `docker-compose.yml` o'zgargan bo'lsa (masalan, v1.5.0 da Mini App porti), bir marta `docker compose up -d` qiling.
- `requirements.txt` o'zgargan bo'lsa, yangi kutubxonalar avtomatik o'rnatiladi (v1.4.0 da PDF, PowerPoint va grafik kutubxonalari qo'shilgan — birinchi ishga tushish 1–3 daqiqa cho'zilishi mumkin).

Keyingi safar yangi funksiya so'raganingizda, avval botdan `/kod` bilan joriy kodni oling va uni Claude'ga yuboring.

## O'rnatish (Hostinger VPS, Docker)

**1. Telegram bot yarating.** Telegram'da @BotFather → `/newbot` → nom bering → tokenni oling.

**2. API kalit oling.** platform.claude.com → Settings → API keys → Create Key. Kalitni Max kredit ulangan tashkilotda yarating.

> Tavsiya: bot uchun alohida **workspace** oching va unga oylik **spend limit** qo'ying (masalan, $60). Tashkilotdagi barcha kalitlar bitta $100 balansdan foydalanadi — limit boshqa ilovalaringizga ham pul qoldiradi.

**3. Zip'ni serverga yuklang** (`/root` papkasiga). Kompyuterdan:

```bash
scp claude-telegram-bot.zip root@SERVER_IP:/root/
```

yoki odatdagi usulingiz: zip'ni hosting File Manager'ga yuklab, VPS terminalida `wget` bilan olish. Yuklab olgach, ommaviy joydagi zip'ni o'chirib qo'ying.

**4. Serverda ishga tushiring** (hPanel → VPS → Browser terminal):

```bash
cd /root
unzip -o claude-telegram-bot.zip
cd claude-telegram-bot
bash install.sh      # token va kalitni so'raydi, .env ga yozadi va botni ishga tushiradi
```

Skript oxirida «✅ TAYYOR» chiqadi. Qo'shimcha sozlamalar (`DEFAULT_MODEL`, `MONTHLY_BUDGET` va boshqalar) `.env` faylida.

**5. Telegram'da botingizga `/start` yozing.**

> Tokenlarni hech kimga (jumladan chatlarga) yubormang — ular faqat serverdagi `.env` faylida turadi.

## Boshqarish

| Vazifa | Buyruq (`/root/claude-telegram-bot` ichida) |
|---|---|
| Loglarni ko'rish | `docker compose logs -f --tail 100` |
| Qayta ishga tushirish (`.env` o'zgarganda) | `docker compose up -d --force-recreate` |
| Yangilash | Telegram'da `/yangilash` (yuqoriga qarang). Faqat `Dockerfile`, `docker-compose.yml` yoki `launcher.py` o'zgarsa: fayllarni almashtirib → `docker compose up -d --build` |
| To'xtatish | `docker compose down` |
| Zaxira nusxa | `data/` papkasini saqlab qo'ying (baza va kod zaxiralari) |

Botning xulq-atvori `prompts/system.md` faylida. Uni tahrirlasangiz, keyingi xabardan kuchga kiradi — qayta ishga tushirish shart emas. Konteyner loyiha papkasini to'liq ulaydi (`./:/app`), shuning uchun `/yangilash` yozgan kod konteyner qayta yig'ilganda ham saqlanadi.

## Narx va tejash

| Model | Narx, 1M token (kirish / chiqish) | Qachon |
|---|---|---|
| Fable 5.1 | $10 / $50 | Eng og'ir mantiqiy masalalar |
| Opus 5.5 | $4 / $20 | Ko'p ishlar uchun asosiy (standart) |
| Sonnet 5.5 | $2 / $10 | Tez va yetarlicha kuchli |
| Haiku 5.5 | $0.10 / $0.50 | Oddiy, tez ishlar |

- Fikrlash ham chiqish tokeni sifatida hisoblanadi — `/effort low` arzonlashtiradi.
- Suhbat uzaygan sari har javob qimmatlashadi: mavzu o'zgarganda `/new` bosing.
- Takrorlanuvchi suhbat tarixi avtomatik keshlanadi (10–40 baravar arzon o'qiladi).
- Internet qidiruvi: har qidiruv $0.01 + o'qilgan sahifalar tokeni.
- `/xarajat` — botning o'z hisobi. Aniq balans: platform.claude.com → Settings → Billing.

## Muammolar

| Belgi | Yechim |
|---|---|
| «⛔ Bu shaxsiy bot» | Parol o'rnatilmagan. Egasi `/parol ...` bilan o'rnatsin. Yangi egani qo'shish: ID'ni `.env` dagi `OWNER_IDS` ga yozing → `docker compose up -d --force-recreate` |
| Parol esdan chiqdi | Egasi parolsiz kiradi: `/parol YANGI_PAROL` bilan yangisini o'rnating |
| «❌ vX ishga tushmadi — … tiklandi» | Bot eski versiyada ishlayapti. Xabardagi xato matnini Claude'ga yuboring |
| «💳 API kredit tugadi» | Yangi oylik kreditni kuting yoki Console'da to'ldiring |
| «🔑 ANTHROPIC_API_KEY noto'g'ri» | `.env` dagi kalitni tekshiring |
| «💸 xarajat limitiga yetildi» | Console → workspace limitini oshiring |
| Bot umuman javob bermaydi | `docker compose logs`: token xato bo'lsa log aytadi; shu token bilan boshqa joyda bot ishlamayotganini tekshiring |

# Claude Telegram bot (shaxsiy yordamchi)

Claude API asosida ishlaydigan shaxsiy Telegram bot. Max tarifidagi oylik $100 API kredit bilan ishlaydi.

## Imkoniyatlar

- **Modellar:** Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5 — `/model` bilan almashtiriladi
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
| `/model` | Modelni tanlash |
| `/effort` | Fikrlash darajasi |
| `/qidiruv` | Internet qidiruvini yoqish/o'chirish |
| `/xarajat` | Sarf hisoboti; har javob ostidagi narxni yoqish/o'chirish |
| `/yordam` | Yordam |

Faylni izohsiz yuborsangiz, bot uni keyingi savolingiz bilan birga ko'rib chiqadi.

**Faqat egasi uchun** (menyuda ham faqat sizga ko'rinadi):

| Buyruq | Vazifasi |
|---|---|
| `/parol YANGI_PAROL` | Kirish parolini o'rnatish yoki almashtirish (`/parol off` — o'chirish) |
| `/foydalanuvchilar` | Parol bilan kirganlar, ularni chiqarish yoki qaytarish |
| `/yangilash` | Botni yangilash: buyruqdan keyin .zip yuborasiz |
| `/versiya` | Joriy versiya va qaytish mumkin bo'lgan versiyalar |
| `/qaytarish` | Oldingi versiyaga qaytish |
| `/kod` | Botning joriy kodi (.zip) — yangi funksiya so'raganda Claude'ga yuborish uchun |

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
- Arxivdan faqat `bot/`, `prompts/`, `requirements.txt`, `VERSION` olinadi; `.env`, baza va `launcher.py` ga tegilmaydi.
- `requirements.txt` o'zgargan bo'lsa, yangi kutubxonalar avtomatik o'rnatiladi.

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

# Claude Telegram bot (shaxsiy yordamchi)

Claude API asosida ishlaydigan shaxsiy Telegram bot. Max tarifidagi oylik $100 API kredit bilan ishlaydi.

## Imkoniyatlar

- **Mini App (📱 /ilova):** Telegram ichidagi ilova — sozlamalar (model, fikrlash, qidiruv, rejim), suhbatlar tarixi va qidiruv, yaratilgan fayllar galereyasi, xarajat grafiklari, egasi uchun admin panel; Telegram mavzusiga mos yorug'/qorong'i ko'rinish. Chatning menyu tugmasidan yoki /ilova bilan ochiladi (sozlash: pastda «Mini App»)
- **Call center hisobotlari (📊 /hisobot):** bot lidlar guruhida operatorlarning reaksiya va izohlarini o'qiydi (izohni AI tahlil qiladi) va har kuni 19:00 da rahbarlar guruhiga jadval yuboradi: kim nechta lidga qo'ng'iroq qildi, nechtasi keladi / ko'tarmadi / kelmaydi, kurslar va rad sabablari. Hech kim belgilamagan lid haqida operatorlarga eslatadi. Sozlash — Mini App → Hisobotlar (pastda «Call center hisobotlari»)
- **Vazifalar (📋 /vazifa):** «#vazifa @username nima qilish kerak, qachongacha» — guruhda yoki botga lichkada. Bot ✍️ qo'yadi, muddatida «📋 Vazifalar» guruhida ijrochini belgilab «Bajardingizmi?» deb so'raydi (Ha / Yo'q), javob bo'lmasa qayta so'raydi va sizga xabar beradi. Takroriy vazifalar («har dushanba») ham bor; ro'yxat — /vazifalar va Mini App
- **Fayl yaratish:** «Word qilib ber», «Excel jadval tuz», «taqdimot tayyorla», «PDF qil» — bot Word (.docx), Excel (.xlsx), PowerPoint (.pptx), PDF, CSV, Markdown yoki matn faylni o'zi yasab chatga yuboradi; raqamli ma'lumot bo'yicha grafik (ustunli, chiziqli, doira) chizadi
- **Fayllar ustida amallar:** skaner rasmi yoki PDF → Word (matn ko'chiriladi); Word faylni formatini saqlab tarjima qilish; PDF'larni birlashtirish, sahifalarini olish/bo'lish/burish, rasmlarni PDF qilish; ikki hujjatni solishtirish — oddiy so'z bilan so'raladi
- **Modellar:** Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5 — `/model` bilan almashtiriladi; **✨ Avto** savol murakkabligiga qarab arzon yoki kuchli modelni o'zi tanlaydi
- **Ish rejimlari:** `/rejim` — rasmiy xat (TKHTA nomidan), Instagram post, Meta reklama, tarjimon, dasturchi
- **Javob ostidagi tugmalar:** 🔄 qayta, ✂️ qisqartirish, 🌐 tarjima (uz/ru/en), 📄 Word fayl, 🔡 kirill yozuvi
- **Suhbatlar tarixi:** `/tarix` — eski suhbatlar ro'yxati va qidiruv, istalganiga qaytib davom ettirish
- **Shablonlar:** `/shablon` — tez-tez yuboriladigan so'rovlar bir tugmada
- **Havolalarni o'qish:** maqola yoki PDF havolasi yuborilsa, bot uni o'qib chiqadi
- **Byudjet nazorati:** kredit 50/80/100% ga yetganda egasiga xabar; 90% dan keyin hamma tejamkor modelga o'tadi
- **Kunlik zaxira:** `data/bot.db` nusxasi har kuni egasiga yuboriladi (`/zaxira` — hozir). Nusxaga suhbatlardagi PDF/rasm baytlari kirmaydi (Telegram 50 MB chegarasiga sig'sin); baribir katta bo'lsa `.zip` qilinadi yoki bot to'liq bazani serverdan olishni aytadi
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
- **Kompyutersiz yangilash:** yangi versiyani .zip qilib botga yuborasiz yoki `/yangilash github` deysiz — bot so'nggi versiyani GitHub'dan o'zi yuklab o'rnatadi, ishlamasa eski versiyani o'zi tiklaydi; yangi versiya chiqqanini har kuni tekshirib, bir marta xabar beradi

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
| `/yangilash` | Botni yangilash: buyruqdan keyin .zip yuborasiz; `/yangilash github` — so'nggi versiyani GitHub'dan o'zi yuklaydi; `/yangilash tekshir` — yangi versiya bormi |
| `/versiya` | Joriy versiya va qaytish mumkin bo'lgan versiyalar |
| `/qaytarish` | Oldingi versiyaga qaytish |
| `/kod` | Botning joriy kodi (.zip) — yangi funksiya so'raganda Claude'ga yuborish uchun |
| `/zaxira` | Bazaning zaxira nusxasini hozir olish |
| `/hisobot` (`/report`) | Call center hisoboti — hozirgi holat; «Rahbarlarga yuborish» tugmasi |
| `/guruhlar` (`/groups`) | Bot kuzatayotgan guruhlar va ularning turi (lidlar / rahbarlar / vazifalar) |
| `/vazifa` (`/task`) | Vazifa berish: `/vazifa @username nima qilish kerak, qachongacha` (yoki shunchaki `#vazifa …`) |
| `/vazifalar` (`/tasks`) | Ochiq vazifalar va ularning holati |

## Mini App (Telegram ichidagi ilova)

Ilova botning o'zida ishlaydi (alohida server kerak emas): konteyner ichida `WEBAPP_PORT` (8091) portida HTTP server turadi, docker-compose uni faqat `127.0.0.1` ga ochadi. Telegram ilovani faqat **HTTPS** manzildan ochadi, shuning uchun serverdagi Caddy (yoki Nginx) orqali tashqariga chiqariladi. Kirish: Telegram imzosi (initData) bot tokeni bilan tekshiriladi, ilovaga faqat egasi, parol bilan kirganlar va hisobotlar ro'yxatidagi rahbarlar kiradi.

**Tez yo'l (bitta buyruq, serverda):** `cd /root/claude-telegram-bot && bash install.sh miniapp` — Caddy'ga `/yordamchi` yo'lini qo'shadi, `.env` ga `WEBAPP_URL=https://bepulkurs.uz/yordamchi/` yozadi, konteynerni qayta yaratadi va tekshiradi. Boshqa manzil kerak bo'lsa: `bash install.sh miniapp https://DOMEN/yol/` (subdomen bo'lsa, avval DNS'da A yozuv). Qo'lda qilmoqchi bo'lsangiz — quyidagi 3 qadam.

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

Ilova ikki bo'limdan iborat, ochilganda tanlanadi: **AI yordamchi** (egasi va parol bilan kirganlar) va **Hisobotlar** (egasi va «Kirish» ro'yxatidagi rahbarlar; quyida). Faqat bittasiga ruxsati borga tanlov chiqmaydi.

AI bo'limi nimalarni qiladi: model/fikrlash/qidiruv/rejimni almashtiradi, chatga savol yuboradi (javob chatda yoziladi), eski suhbatni davom ettiradi, bot yaratgan fayllarni qayta yuboradi yoki yuklab beradi (nusxalar `data/files/` da, har foydalanuvchi uchun `FILES_MAX_MB`), 30 kunlik sarf grafigi va modellar bo'yicha taqsimot; egasi uchun — parol, foydalanuvchilar, zaxira nusxa, GitHub'dan yangilash va oldingi versiyaga qaytish (tasdiq chatga keladi).

## Call center hisobotlari

Bot lidlar guruhidagi (kanalning izohlar guruhi) har bir lid postini, operatorlarning unga qo'ygan reaksiyasini va izohini yozib boradi. Lidlar guruhiga hech narsa yozmaydi — faqat o'qiydi (bot guruhga faqat «📋 Vazifalar» guruhida «Bajardingizmi?» so'rovlarini va rahbarlar guruhiga kunlik hisobotni yozadi).

**Sozlash (bir marta):**

1. **Lidlar guruhi.** Botni kanalning izohlar guruhiga (masalan, «KOMMENTARIYA TA LIDLAR») qo'shing va **admin** qiling — huquq belgilash shart emas, lekin admin bo'lmasa bot reaksiyalarni ko'rmaydi. Bot sizga lichkada «Bu guruh nima uchun?» deb so'raydi → **📥 Lidlar** → **✅ Tayyor**.
2. **Rahbarlar guruhi.** Botni rahbarlar guruhiga qo'shing (admin shart emas) → **👔 Rahbarlar** → **✅ Tayyor**. Kunlik hisobot shu guruhga keladi; bunday guruh bo'lmasa — sizga lichkada.
3. **Operatorlar.** Mini App → Hisobotlar → Operatorlar → «+ Qo'shish»: Telegram username (yoki ID) va ism. Ro'yxatda yo'q odam qo'ng'iroq qilsa, hisobotda «➕ Ro'yxatda yo'q» qatorida ko'rinadi (bir bosishda operator yoki istisno qilinadi). Har operator botga bir marta `/start` bossin — belgilanmagan lidlar haqida eslatma oladi (AI bo'limi ochilmaydi).
4. **Rahbarlar kirishi.** Mini App → Hisobotlar → Sozlamalar → «Kirish · rahbarlar»: qo'shilgan rahbar botni ochganda «📊 Hisobotlar» tugmasini oladi va faqat shu bo'limni ko'radi, sozlamalarni o'zgartira olmaydi. Parol kerak emas.
5. **«📊 Batafsil» tugmasi (ixtiyoriy).** Kunlik hisobot ostida Mini App'ni o'sha kun bilan ochadigan tugma: @BotFather → botingiz → Bot Settings → Configure Mini App → Enable Mini App → `WEBAPP_URL` manzili. Bot buni o'zi aniqlaydi (10 daqiqagacha), qayta ishga tushirish shart emas.

**Statuslar** (Mini App → Sozlamalar'da qo'shiladi va o'zgartiriladi): 👍 keladi · 👎 kelmaydi · 🆒 ko'tarmadi · 🤔 o'ylayapti · 🤝 Telegram'dan yozadi · 💔 umuman yo'q. Operator lid postiga shu emojini bosadi yoki izoh yozadi. Izohni AI (`REPORT_MODEL`, standart Haiku 5.5) o'qib, statusni, rad sababini va qayta qo'ng'iroq vaqtini ajratadi; telefon raqamlar AI'ga yuborilmaydi. Reaksiya va izoh mos kelmasa, eng so'nggisi olinadi (hisobotda «⚠️ Ziddiyat»). Bir odam bir lidni bir marta sanaydi.

**Kunlik hisobot:** standart 19:00, dushanba–shanba. Davr — oldingi hisobot vaqtidan shu vaqtgacha (yakshanba dushanba hisobotiga qo'shiladi). Ichida: operatorlar jadvali (jami lid va har status soni), ro'yxatda yo'qlar, yangi va belgilanmagan lidlar, kurslar bo'yicha 👍, izohlardan rad sabablari va qayta qo'ng'iroq so'raganlar. Bot o'chiq paytda o'tib ketgan hisobot ishga tushgach «kechikib yuborildi» belgisi bilan keladi (36 soatgacha). Rahbarlar guruhiga yuborib bo'lmasa (bot chiqarilgan), hisobot sizga keladi; tarmoq xatosida 5 daqiqadan keyin qayta uriniladi. Bir kunga bitta kunlik hisobot: vaqtni o'zgartirsangiz, o'sha kun hisoboti qayta kelmaydi. Vaqt, kunlar, eslatma va ish vaqti — Mini App → Hisobotlar → Sozlamalar.

**Eslatma:** yangi lidni 30 daqiqa ichida hech kim belgilamasa (reaksiya yoki izoh), `/start` bosgan operatorlarga lichkada havolalar bilan eslatma boradi — faqat ish vaqtida (09:00–19:00) va hisobot kunlarida, kamida 15 daqiqa oralig'ida (yangi lidlar bitta xabarda). Lid postidan boshqa xabarlarga (e'lon, savol) qo'yilgan reaksiya qo'ng'iroq hisoblanmaydi.

**Mini App → Hisobotlar:** «Bugun» — jonli holat va oldingi kunlar; «Operatorlar» — ro'yxat va natijalar; «Sozlamalar» (faqat egasi) — guruhlar, statuslar, hisobot vaqti, rahbarlar, istisnolar va holat. Chatda: `/hisobot` — hozirgi holat va «Rahbarlarga yuborish»; `/guruhlar` — guruhlar turini o'zgartirish.

**Xarajat:** izoh tahlili arzon modelda (100 ta izoh ≈ $0.01 dan kam), `/xarajat` da «📊 Guruh tahlili» qatorida ko'rinadi.

## Vazifalar

Vazifani faqat siz (egasi) berasiz — xabar **`#vazifa`** yoki **`Vazifa:`** bilan boshlanadi (belgisiz xabar vazifa emas):

- `#vazifa @malika_op ertaga 15:00 gacha oylik hisobotni topshirsin`
- `Vazifa: @sardor_op har dushanba 10:00 da lidlar ro'yxatini tekshirsin` (takroriy)
- guruhda odamning xabariga reply qilib: `#vazifa jumagacha shartnomani tayyorlang`

**Sozlash:** botni vazifalar beriladigan guruhga qo'shing va admin qiling (aks holda `#vazifa` xabarlarini ko'rmaydi; adminsiz faqat `/vazifa …` buyrug'i ishlaydi) → lichkada **📋 Vazifalar** → **✅ Tayyor**. Bir guruh bir nechta turda bo'lishi mumkin (masalan, rahbarlar guruhi ham vazifalar guruhi).

**Qanday ishlaydi:**
- Bot xabaringizga ✍️ qo'yadi va sizga lichkada tasdiq yuboradi (ijrochi, vazifa, muddat; **🗑 Bekor qilish**). Muddat va takrorni arzon AI ajratadi; muddat aytilmasa — keyingi ish kuni 18:00 qo'yiladi va tugmalar bilan o'zgartirasiz (Bugun / Ertaga / 3 kun / 1 hafta).
- Botga lichkada yozilgan vazifa so'rovi ijrochi a'zo bo'lgan «📋 Vazifalar» guruhiga chiqadi.
- Muddatida (faqat ish vaqtida) bot guruhda ijrochini @ bilan belgilab so'raydi: **✅ Ha** — vazifa yopiladi, sizga xabar keladi; **❌ Yo'q** — 3 soatdan keyin yana so'raydi (har 3-«Yo'q»da sizga xabar). Javob bo'lmasa 2 soatdan keyin yana; 3 marta javobsiz qolsa — sizga xabar (**🔔 Yana so'rash / ✅ Bajarildi / 🗑 Bekor**), so'rash to'xtaydi.
- Tugmalarni faqat ijrochi (yoki siz) bosa oladi; boshqalarga «Bu savol … uchun» deb chiqadi.
- Takroriy vazifa: birinchi so'rov chiqqanda keyingi nusxasi yaratiladi; to'xtatish — Mini App'da **🔁 Takrorni to'xtatish** yoki keyingi nusxani bekor qilish.

**Mini App → Hisobotlar → Vazifalar:** ochiq / bajarilgan / bekor qilingan ro'yxat, holati (kutilmoqda, so'raldi, «Yo'q» dedi, javob yo'q, muddati o'tgan), tarixi. Siz qo'shasiz, muddatni o'zgartirasiz, yopasiz, hozir so'ratasiz; rahbarlar faqat ko'radi. Ovozli vazifa hozircha yo'q — faqat matn.

## Ishlab chiquvchilar uchun

- `CLAUDE.md` — loyiha qoidalari va kod xaritasi (Claude Code avtomatik o'qiydi); `docs/TZ.md` — keyingi funksiyalarning texnik topshirig'i (32 ta paket).
- Testlar: `pip install -r requirements-dev.txt && python -m pytest` (soxta Telegram va Anthropic bilan, ~30 s); `python tests/e2e/e2e.py` — haqiqiy launcher bilan yangilanish/qaytarish sinovi.
- Chiqarish: `VERSION` + `UPDATE_NOTES.md` → testlar → `git push` → `python scripts/build_release.py --publish` → `dist/claude-telegram-bot-v….zip` (maxfiy satrlar tekshiriladi) + GitHub Release `v<VERSION>` shu zip bilan → egasi botda `/yangilash github`. `--publish`siz faqat zip yig'iladi (botga `/yangilash` + .zip).
- `--publish`: `gh` CLI (kirilgan bo'lsa) yoki REST orqali — muhitdagi `GITHUB_TOKEN`/`GH_TOKEN` (fine-grained, Contents: Read and write); ixtiyoriy `--repo owner/name`, `--branch main`. Repo'da hali birorta Release bo'lmasa, bot `main` tarmog'i arxividan o'rnatadi (versiya undagi `VERSION` dan, release izohlarisiz). Birinchi Release chiqqach bot doim so'nggi Release'ni oladi — har paketda `--publish` kerak, oddiy push yetarli emas.
- Claude Code ish tartibi: paket tayyor → `VERSION` + `UPDATE_NOTES.md` → testlar → push → `build_release.py --publish` → egasi botda `/yangilash github`.
- Mini App skrinshotlari: `python scripts/miniapp_shots.py` (Playwright) → `docs/screens/`.

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
3. Bot arxivni tekshiradi va nima o'zgarishini (versiya, o'zgargan fayllar, izohlar) ko'rsatadi → **✅ O'rnatish** ni bosing.
4. Bot 10–30 soniyaga qayta ishga tushadi va «✅ Bot yangilandi: v… → v…» deb yozadi.

Xavfsizlik:
- Har yangilanishdan oldin joriy versiya zaxiraga olinadi (oxirgi 5 tasi saqlanadi).
- Yangi versiya ishga tushmasa, eski versiya **avtomatik** tiklanadi va sizga xato matni yuboriladi — shu matnni Claude'ga yuboring.
- `/qaytarish` — oxirgi yangilanishni bekor qiladi (ketma-ket bosilsa, yanada oldingi versiyaga qaytadi).
- Arxivdan `bot/`, `prompts/`, `requirements.txt`, `VERSION` (va `install*.sh`, `docker-compose.yml`, `README.md`) olinadi; `.env`, baza, `Dockerfile` va `launcher.py` ga tegilmaydi. `docker-compose.yml` o'zgargan bo'lsa (masalan, v1.5.0 da Mini App porti), bir marta `docker compose up -d` qiling.
- `requirements.txt` o'zgargan bo'lsa, yangi kutubxonalar avtomatik o'rnatiladi (v1.4.0 da PDF, PowerPoint va grafik kutubxonalari qo'shilgan — birinchi ishga tushish 1–3 daqiqa cho'zilishi mumkin).

Keyingi safar yangi funksiya so'raganingizda, avval botdan `/kod` bilan joriy kodni oling va uni Claude'ga yuboring.

### GitHub'dan yangilash

Zip ko'chirmasdan: yangi versiya GitHub'ga chiqqach, botga `/yangilash github` deb yozasiz — bot arxivni o'zi yuklab, yuqoridagi tekshiruv va tasdiq bilan o'rnatadi. Zaxira, avtomatik tiklash va `/qaytarish` bu usulda ham xuddi shunday ishlaydi.

`.env` sozlamalari (hammasi ixtiyoriy; o'zgartirgach `docker compose up -d --force-recreate`):

| Sozlama | Ma'nosi |
|---|---|
| `GITHUB_REPO` | `owner/repo` (standart `Qibraysadosi/tta-yordamchi-bot`); `off` — GitHub'dan yangilash o'chiq |
| `GITHUB_TOKEN` | Faqat yopiq (private) repo uchun; ochiq repo tokensiz ishlaydi |
| `GITHUB_BRANCH` | Standart `main`; faqat Release bo'lmaganda ishlatiladi — tarmoq arxivi olinadi, versiya undagi `VERSION` dan |
| `UPDATE_CHECK` | `on` (standart) — har kuni zaxira vaqtida (`BACKUP_TIME`; zaxira o'chiq bo'lsa 03:30) GitHub tekshiriladi, yangi versiya bo'lsa egasiga har versiya uchun bir marta xabar keladi («⬇️ Yuklab o'rnatish» tugmasi va release izohlari bilan); `off` — tekshirmaydi |

**Token (faqat yopiq repo uchun):** github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Repository access — faqat shu repo; Repository permissions → Contents: **Read-only** (boshqa huquq kerak emas, yozish huquqi bermang). Tokenni faqat serverdagi `.env` ga `GITHUB_TOKEN=` qatoriga yozing va konteynerni qayta yarating (`docker compose up -d --force-recreate`).

Buyruqlar (faqat egasi):

- `/yangilash github` (`/yangilash git`, `/update github`) — bot so'nggi Release'dagi `claude-telegram-bot-vX.Y.Z.zip` ni yuklaydi (Release'da zip bo'lmasa — uning manba arxivi; Release umuman bo'lmasa — `GITHUB_BRANCH` arxivi), uni yuborilgan .zip kabi tekshiradi (tuzilma, sintaksis, hajm ≤ 20 MB) va «📦 Yangilanish: v1.5.0 → v1.6.0», manba (release/tag), o'zgargan fayllar ro'yxati va izohlarni ko'rsatadi → **✅ O'rnatish** / **❌ Bekor**. Tasdiqdan keyin odatdagi yo'l: zaxira, qayta ishga tushish, yangi versiya ishlamasa avtomatik qaytish.
- `/yangilash tekshir` (`check`) — faqat yangi versiya bor-yo'qligini aytadi, hech narsa yuklamaydi.
- `/yangilash` — avvalgidek .zip kutadi; `GITHUB_REPO` sozlangan bo'lsa javob ostida «🐙 GitHub'dan yangilash» va «🔍 Yangi versiya bormi?» tugmalari chiqadi.
- Mini App → Admin panel → «GitHub'dan yangilash» — arxivni yuklab, tasdiqni chatga yuboradi.

Xatolar: `GITHUB_REPO=off` — «GitHub'dan yangilash o'chiq» (sozlama yozilmagan bo'lsa standart repo ishlatiladi); yopiq repo tokensiz yoki token noto'g'ri — `.env` ga `GITHUB_TOKEN` yozish so'raladi; release yo'q va `GITHUB_BRANCH` noto'g'ri — tarmoqni tekshirish so'raladi; GitHub so'rovlar chegarasi (rate limit) — biroz kuting yoki token qo'shing; tarmoq xatosi — keyinroq qayta urinib ko'ring. Xato bo'lganda joriy versiya o'zgarmaydi.

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
| Yangilash | Telegram'da `/yangilash github` (GitHub'dan) yoki `/yangilash` + .zip (yuqoriga qarang). Faqat `Dockerfile`, `docker-compose.yml` yoki `launcher.py` o'zgarsa: fayllarni almashtirib → `docker compose up -d --build` |
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
| «GitHub'da … topilmadi yoki yopiq» | Repo yopiq (private) yoki `GITHUB_REPO` noto'g'ri (`Qibraysadosi/tta-yordamchi-bot` repo'si ochiq — token kerak emas). Faqat o'qish huquqli fine-grained token yarating (yuqorida «GitHub'dan yangilash») → `.env` ga `GITHUB_TOKEN=` → `docker compose up -d --force-recreate` |
| Hisobotda qo'ng'iroqlar 0, lekin operatorlar belgilagan | Bot lidlar guruhida admin emas: guruh sozlamalari → Administratorlar → botni qo'shing. `/guruhlar` da guruh turi «📥 Lidlar» ekanini tekshiring |
| Kunlik hisobot kelmadi | Mini App → Hisobotlar → Sozlamalar: «Kunlik hisobot» yoqiqmi, lidlar guruhi ulanganmi. Rahbarlar guruhida botni chiqarib yuborishgan bo'lsa, xato haqida sizga xabar keladi |
| «GitHub so'rovlar chegarasi» (rate limit) | Bir oz kuting (odatda 1 soatgacha) yoki `.env` ga `GITHUB_TOKEN` qo'shing — token bilan chegara ancha yuqori |

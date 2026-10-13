#!/usr/bin/env bash
# Claude Telegram botni o'rnatish: token va kalitni so'raydi, .env ga yozadi, Docker'da ishga tushiradi.
# Ishlatish: bash install.sh   (qayta ishga tushirsangiz, saqlangan qiymatlar qayta so'ralmaydi)
set -euo pipefail
cd "$(dirname "$0")"

# ---------------------------------------------------------------- Mini App'ni ulash: bash install.sh miniapp [URL]
# Caddy'ga yo'l (yoki subdomen bloki), docker-compose'ga port, .env ga WEBAPP_URL, konteynerni qayta yaratish.
miniapp_setup() {
URL="${1:-https://bepulkurs.uz/yordamchi/}"
[[ "$URL" == */ ]] || URL="$URL/"
HOST="$(echo "$URL" | sed -E 's#https://([^/]+)/.*#\1#')"
SUBPATH="$(echo "$URL" | sed -E 's#https://[^/]+(/.*)/$#\1#')"
PORT="$( (grep -E '^WEBAPP_PORT=' .env 2>/dev/null || true) | cut -d= -f2)"; PORT="${PORT:-8091}"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"

[ -f .env ] || { echo "❌ .env topilmadi — avval install.sh"; exit 1; }
[ -f "$CADDYFILE" ] || { echo "❌ $CADDYFILE topilmadi (Caddy o'rnatilmagan?). README → Mini App bo'limiga qarang."; exit 1; }

# 1. Caddy: domen blokiga yo'l (yoki subdomen uchun alohida blok)
python3 - "$CADDYFILE" "$HOST" "$SUBPATH" "$PORT" <<'PY'
import re, sys, pathlib
path, host, sub, port = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
text = path.read_text()
if sub and sub != "/":
    if f"handle_path {sub}/*" in text:
        print("Caddy: yo'l allaqachon bor")
    else:
        block = f"    redir {sub} {sub}/ 301\n    handle_path {sub}/* {{\n        reverse_proxy 127.0.0.1:{port}\n    }}\n"
        new, n = re.subn(r"(^[^\n#]*" + re.escape(host) + r"[^\n{]*\{[ \t]*\n)", lambda m: m.group(1) + block, text, count=1, flags=re.M)
        if not n:
            sys.exit(f"❌ Caddyfile ichida {host} bloki topilmadi — README → Mini App bo'limidagi blokni qo'lda qo'shing")
        path.write_text(new); print(f"Caddy: {host}{sub} → 127.0.0.1:{port} qo'shildi")
else:
    if host in text:
        print("Caddy: domen bloki allaqachon bor")
    else:
        path.write_text(text.rstrip("\n") + f"\n\n{host} {{\n    reverse_proxy 127.0.0.1:{port}\n}}\n")
        print(f"Caddy: {host} bloki qo'shildi (DNS A yozuvi shu serverga qarashi kerak)")
PY
caddy validate --config "$CADDYFILE" >/dev/null && systemctl reload caddy && echo "Caddy: qayta yuklandi"

# 2. docker-compose: port faqat shu serverning o'zida (eski compose'da bo'lmasa qo'shiladi)
python3 - "$PORT" <<'PY'
import pathlib, sys
port = sys.argv[1]
c = pathlib.Path("docker-compose.yml"); t = c.read_text()
if "ports:" not in t:
    t = t.replace("    env_file: .env\n", f'    env_file: .env\n    ports:\n      - "127.0.0.1:{port}:{port}"\n', 1)
    c.write_text(t); print("docker-compose: port qo'shildi")
PY

# 3. .env: WEBAPP_URL va WEBAPP_PORT
python3 - "$URL" "$PORT" <<'PY'
import re, pathlib, sys
url, port = sys.argv[1], sys.argv[2]
e = pathlib.Path(".env"); v = e.read_text()
v = re.sub(r"^WEBAPP_URL=.*\n?", "", v, flags=re.M).rstrip("\n") + f"\nWEBAPP_URL={url}\n"
if not re.search(r"^WEBAPP_PORT=", v, flags=re.M):
    v += f"WEBAPP_PORT={port}\n"
e.write_text(v); print(f".env: WEBAPP_URL={url}")
PY

# 4. Konteynerni qayta yaratish va tekshirish
docker compose up -d --force-recreate
echo "⏳ Bot ishga tushmoqda..."; sleep 20
if curl -fs "http://127.0.0.1:$PORT/health" >/dev/null; then
  echo "✅ Mini App serveri ishlayapti: http://127.0.0.1:$PORT/health"
else
  echo "❌ Server javob bermadi — docker compose logs --tail 50"; exit 1
fi
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$URL" || true)"
echo "🌐 $URL → HTTP $CODE (200 bo'lsa tayyor; Telegram'da botga /ilova yozing)"
}
if [ "${1:-}" = "miniapp" ]; then
  shift
  miniapp_setup "$@"
  exit 0
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "❌ Docker Compose topilmadi. Avval Docker o'rnating."
  exit 1
fi

[ -f .env ] || cp .env.example .env

current() { grep -E "^$1=" .env | head -n1 | cut -d= -f2- || true; }
save() {
  if grep -qE "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

TOKEN_RE='^[0-9]+:[A-Za-z0-9_-]{30,}$'
KEY_RE='^sk-ant-[A-Za-z0-9_-]{20,}$'

token="$(current TELEGRAM_BOT_TOKEN)"
if [[ ! "$token" =~ $TOKEN_RE ]]; then
  while true; do
    read -rp "1) Telegram bot tokeni (@BotFather'dan): " token
    token="${token//[[:space:]]/}"
    [[ "$token" =~ $TOKEN_RE ]] && break
    echo "⚠️  Token ko'rinishi noto'g'ri (masalan: 123456789:AA...). Qayta kiriting."
  done
  save TELEGRAM_BOT_TOKEN "$token"
fi

key="$(current ANTHROPIC_API_KEY)"
if [[ ! "$key" =~ $KEY_RE ]]; then
  while true; do
    read -rsp "2) Claude API kaliti (sk-ant-api03-...; yozilgani ekranda ko'rinmaydi): " key
    echo
    key="${key//[[:space:]]/}"
    [[ "$key" =~ $KEY_RE ]] && break
    echo "⚠️  Kalit sk-ant- bilan boshlanishi kerak. apikey_... — bu kalitning ID raqami, kalitning o'zi emas."
  done
  save ANTHROPIC_API_KEY "$key"
fi

chmod 600 .env
mkdir -p data
echo "⏳ Bot yig'ilmoqda va ishga tushirilmoqda (1–3 daqiqa)..."
docker compose up -d --build
sleep 10
echo "────────────── oxirgi loglar ──────────────"
docker compose logs --tail 15
if grep -q "Bot ishga tushdi" <<<"$(docker compose logs 2>/dev/null || true)"; then
  echo "✅ TAYYOR! Telegram'da botingizga /start yozing."
else
  echo "ℹ️  'Bot ishga tushdi' hali ko'rinmadi. Kuzatish uchun: docker compose logs -f"
fi

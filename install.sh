#!/usr/bin/env bash
# Claude Telegram botni o'rnatish: token va kalitni so'raydi, .env ga yozadi, Docker'da ishga tushiradi.
# Ishlatish: bash install.sh   (qayta ishga tushirsangiz, saqlangan qiymatlar qayta so'ralmaydi)
set -euo pipefail
cd "$(dirname "$0")"

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

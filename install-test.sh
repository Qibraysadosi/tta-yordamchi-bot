#!/usr/bin/env bash
# Sinov boti: asosiy botning nusxasini alohida papkada (../claude-telegram-bot-test) ishga tushiradi.
# Yangilanishni avval sinov botiga yuborib tekshirasiz, keyin asosiy botga.
# Ishlatish: bash install-test.sh   (@BotFather'da ochilgan ikkinchi bot tokenini so'raydi)
set -euo pipefail
cd "$(dirname "$0")"
MAIN="$(pwd)"
TEST="$(dirname "$MAIN")/$(basename "$MAIN")-test"

if [ ! -f .env ]; then
  echo "❌ Avval asosiy botni o'rnating (install.sh) — .env topilmadi."
  exit 1
fi

mkdir -p "$TEST"
# Kod nusxasi (baza, .env va zaxiralar nusxalanmaydi)
tar --exclude=./data --exclude=./.env --exclude=./.git --exclude='__pycache__' -cf - . | tar -xf - -C "$TEST"
mkdir -p "$TEST/data"

if [ ! -f "$TEST/.env" ]; then
  grep -vE '^(TELEGRAM_BOT_TOKEN|CONTAINER_NAME|BACKUP_TIME)=' .env > "$TEST/.env"
  TOKEN_RE='^[0-9]+:[A-Za-z0-9_-]{30,}$'
  while true; do
    read -rp "Sinov botining tokeni (@BotFather'dan, asosiy botnikidan boshqa): " token
    token="${token//[[:space:]]/}"
    [[ "$token" =~ $TOKEN_RE ]] && break
    echo "⚠️  Token ko'rinishi noto'g'ri. Qayta kiriting."
  done
  {
    echo "TELEGRAM_BOT_TOKEN=$token"
    echo "CONTAINER_NAME=claude-telegram-bot-test"
    echo "BACKUP_TIME=off"
  } >> "$TEST/.env"
  chmod 600 "$TEST/.env"
fi

cd "$TEST"
echo "⏳ Sinov boti yig'ilmoqda ($TEST)..."
docker compose up -d --build
sleep 10
docker compose logs --tail 10 --no-log-prefix
echo "✅ Sinov boti ishga tushdi. Yangilanishni avval unga yuboring (/yangilash), keyin asosiy botga."

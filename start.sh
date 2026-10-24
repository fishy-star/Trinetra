#!/bin/bash
# Starts Trinetra: tunnel, server, bot and keep-awake. Ctrl+C stops everything.
cd "$(dirname "$0")" || exit 1  # the repo root, wherever it lives
source .venv/bin/activate
mkdir -p logs
trap 'echo; echo "Stopping everything..."; kill 0' INT TERM EXIT

echo "Starting tunnel..."
cloudflared tunnel --url http://localhost:8000 > logs/tunnel.log 2>&1 &
URL=""
for i in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1)
  [ -n "$URL" ] && break
  sleep 1
done
if [ -z "$URL" ]; then echo "No tunnel link after 40 s. Check logs/tunnel.log or switch to hotspot."; exit 1; fi

# Put the new link into .env
if grep -q '^PUBLIC_BASE_URL=' .env; then
  sed -i '' "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=$URL|" .env
else
  echo "PUBLIC_BASE_URL=$URL" >> .env
fi
set -a; source .env; set +a

echo "Starting server and bot..."
uvicorn app.main:app --port 8000 > logs/server.log 2>&1 &
python -m app.channels.telegram_poll > logs/bot.log 2>&1 &
caffeinate -dis &

sleep 3
echo
echo "=================================================="
echo " PUBLIC LINK:  $URL"
echo " TELEGRAM:     t.me/Trinetra_check_bot"
echo "=================================================="
echo " Watch logs in another tab:  tail -f $PWD/logs/bot.log"
echo " Press Ctrl+C here to stop everything."
wait

#!/bin/bash
# Starts Trinetra: tunnel, server, bot and keep-awake.
# Press Ctrl+C twice within 3 seconds to stop everything. Run ./restart.sh to restart only the server and bot.
cd "$(dirname "$0")" || exit 1  # the repo root, wherever it lives
source .venv/bin/activate
mkdir -p logs

# Run a long-lived process in its own process group. A Ctrl+C typed in this terminal (or injected by an
# editor) then reaches only this script, never the server, bot or tunnel directly; the script decides.
# Always run it in the background (`detached cmd &`): `exec` makes the background job *be* the process,
# so $! is the process's own PID and stopping it really stops it.
detached() { exec perl -e 'setpgrp(0, 0); exec @ARGV or die "cannot run $ARGV[0]: $!\n"' "$@"; }

TUNNEL_PID="" SERVER_PID="" BOT_PID="" AWAKE_PID="" LAST_INT=-10

stop_pid() {  # stop_pid PID NAME: ask politely, then insist after 10 s
  local pid=$1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM "$pid" 2>/dev/null
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.5; done
  echo "  $2 did not stop; forcing it."
  kill -KILL "$pid" 2>/dev/null
}

start_app() {
  set -a; source .env; set +a
  detached uvicorn app.main:app --port 8000 >> logs/server.log 2>&1 &
  SERVER_PID=$!
  detached python -m app.channels.telegram_poll >> logs/bot.log 2>&1 &
  BOT_PID=$!
}

stop_app() {
  stop_pid "$BOT_PID" bot
  stop_pid "$SERVER_PID" server
}

stop_all() {
  trap - INT TERM USR1 EXIT
  echo; echo "Stopping everything..."
  stop_app
  stop_pid "$TUNNEL_PID" tunnel
  stop_pid "$AWAKE_PID" caffeinate
  rm -f logs/start.pid
  echo "Stopped."
}

on_int() {
  if (( SECONDS - LAST_INT <= 3 )); then stop_all; exit 0; fi
  LAST_INT=$SECONDS
  echo; echo "Ctrl+C received. Nothing was stopped. Press Ctrl+C again within 3 seconds to stop everything."
}

on_restart() {
  echo; echo "$(date '+%H:%M:%S') Restarting server and bot (the tunnel and public link stay the same)..."
  stop_app
  start_app
  echo "$(date '+%H:%M:%S') Server and bot restarted."
}

trap on_int INT
trap 'stop_all; exit 0' TERM
trap stop_all EXIT
trap on_restart USR1
echo $$ > logs/start.pid

echo "Starting tunnel..."
detached cloudflared tunnel --url http://localhost:8000 > logs/tunnel.log 2>&1 &
TUNNEL_PID=$!
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

echo "Starting server and bot..."
: > logs/server.log; : > logs/bot.log
start_app
detached caffeinate -dis &
AWAKE_PID=$!

sleep 3
echo
echo "=================================================="
echo " PUBLIC LINK:  $URL"
echo " TELEGRAM:     t.me/Trinetra_check_bot"
echo "=================================================="
echo " Watch logs in another tab:  tail -f $PWD/logs/bot.log"
echo " Restart server and bot:     ./restart.sh"
echo " Stop everything:            Ctrl+C twice within 3 seconds"

# `wait` returns whenever a trapped signal arrives; keep waiting until we are told to stop.
while :; do
  wait
  sleep 1
done

#!/bin/bash
# Restarts only the server and the bot, reloading .env. The tunnel keeps running, so the public link stays the same.
# Asks the running ./start.sh to do it, so start.sh still owns every process and its Ctrl+C still stops them all.
cd "$(dirname "$0")" || exit 1
PID=$(cat logs/start.pid 2>/dev/null)
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
  echo "start.sh is not running. Start everything with ./start.sh first."
  exit 1
fi
kill -USR1 "$PID"
echo "Asked start.sh to restart the server and bot. The tunnel and public link are unchanged."
echo "Watch:  tail -f $PWD/logs/server.log $PWD/logs/bot.log"

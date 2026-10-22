#!/bin/bash
# Start collector EarnScope sebagai proses persisten (detached).
set -u
# Proxy egress (pola stabil: tanpa kredensial, via profile.d)
[ -f /etc/profile.d/hatch-egress.sh ] && source /etc/profile.d/hatch-egress.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$HERE/data/collector.pid"
mkdir -p "$HERE/data"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "collector sudah jalan (pid $(cat "$PIDFILE"))"
  exit 0
fi

cd "$HERE"
setsid nohup python3 collector.py >> "$HERE/data/collector.log" 2>&1 < /dev/null &
echo $! > "$PIDFILE"
sleep 2
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "collector jalan (pid $(cat "$PIDFILE"))"
else
  echo "collector GAGAL start — cek $HERE/data/collector.log"
  exit 1
fi

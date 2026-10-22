#!/bin/bash
# Watchdog EarnScope untuk cron: restart collector bila mati.
# Contoh cron: */15 * * * * /path/earnscope/watchdog.sh
set -u
# Proxy egress (pola stabil: tanpa kredensial, via profile.d)
[ -f /etc/profile.d/hatch-egress.sh ] && source /etc/profile.d/hatch-egress.sh
HERE="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$HERE/data/collector.pid"
LOG="$HERE/data/collector.log"

running=false
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  running=true
fi
if ! $running && pgrep -f "earnscope/collector.py" >/dev/null 2>&1; then
  running=true
  pgrep -f "earnscope/collector.py" | head -1 > "$PIDFILE"
fi

if $running; then
  echo "$(date '+%F %T') watchdog: collector OK (pid $(cat "$PIDFILE"))" >> "$LOG"
else
  echo "$(date '+%F %T') watchdog: collector MATI — restart" >> "$LOG"
  "$HERE/run_collector.sh" >> "$LOG" 2>&1
fi

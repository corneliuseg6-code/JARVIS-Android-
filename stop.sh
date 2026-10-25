#!/usr/bin/env bash
# JARVIS Android V2 — stop the local server.
set -euo pipefail

HOME_DIR="${JARVIS_HOME:-$HOME/.jarvis}"
PIDFILE="$HOME_DIR/run/jarvis.pid"

stopped=0

if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.5
    done
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null || true
    echo "JARVIS stopped (pid $PID)."
    stopped=1
  fi
  rm -f "$PIDFILE"
fi

if [ "$stopped" -eq 0 ]; then
  # Fall back to matching the module name.
  if pkill -f "server.main" 2>/dev/null; then
    echo "JARVIS stopped."
    stopped=1
  fi
fi

[ "$stopped" -eq 0 ] && echo "JARVIS was not running."

command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock || true
exit 0

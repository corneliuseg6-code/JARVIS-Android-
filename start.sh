#!/usr/bin/env bash
# JARVIS Android V2 — start the local server.
#   ./start.sh              listen on 127.0.0.1:8787 (phone only)
#   ./start.sh --lan        also reachable from other devices on the Wi-Fi
#   JARVIS_PORT=9000 ./start.sh
set -euo pipefail

cd "$(dirname "$0")"

PORT="${JARVIS_PORT:-8787}"
HOST="127.0.0.1"
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --lan) HOST="0.0.0.0" ;;
    --host) shift; HOST="${1:-$HOST}" ;;
    --port) shift; PORT="${1:-$PORT}" ;;
    --verbose) EXTRA+=("--verbose") ;;
    --no-autoconnect) EXTRA+=("--no-autoconnect") ;;
    *) EXTRA+=("$1") ;;
  esac
  shift
done

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "python3 not found. In Termux run:  pkg install python" >&2
  exit 1
fi

PIDFILE="${JARVIS_HOME:-$HOME/.jarvis}/run/jarvis.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "JARVIS is already running (pid $(cat "$PIDFILE")). Use ./stop.sh first." >&2
  exit 1
fi

# Keep the phone from suspending the server while the screen is off (optional).
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

exec "$PY" -m server.main --host "$HOST" --port "$PORT" ${EXTRA[@]+"${EXTRA[@]}"}

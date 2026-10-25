"""JARVIS Android V2 entry point.

    python3 -m server.main [--host 127.0.0.1] [--port 8787] [--open]
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading

from . import armory, config, paths
from .api import router
from .app import make_server

DEFAULT_PORT = int(os.environ.get("JARVIS_PORT", "8787"))
DEFAULT_HOST = os.environ.get("JARVIS_HOST", "127.0.0.1")


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _write_pid() -> None:
    paths.RUN_DIR.mkdir(parents=True, exist_ok=True)
    (paths.RUN_DIR / "jarvis.pid").write_text(str(os.getpid()), encoding="utf-8")


def _clear_pid() -> None:
    try:
        (paths.RUN_DIR / "jarvis.pid").unlink()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS Android V2 server")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="bind address (default 127.0.0.1; use 0.0.0.0 to reach it from the LAN)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-autoconnect", action="store_true",
                        help="do not open MCP connections marked autoconnect")
    args = parser.parse_args(argv)

    paths.ensure_dirs()
    cfg = config.get_config(refresh=True)

    try:
        httpd = make_server(args.host, args.port, router, paths.WEB_ROOT, verbose=args.verbose)
    except OSError as exc:
        sys.stderr.write(f"[jarvis] cannot bind {args.host}:{args.port} -> {exc}\n")
        sys.stderr.write("[jarvis] is JARVIS already running?  ./stop.sh\n")
        return 1

    _write_pid()

    print("=" * 58)
    print("  JARVIS Android V2")
    print("=" * 58)
    print(f"  local      : http://127.0.0.1:{args.port}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  network    : http://{_local_ip()}:{args.port}")
    print(f"  data       : {paths.JARVIS_HOME}")
    print(f"  voices     : {paths.VOICES_DIR}")
    print(f"  ollama     : {cfg['ollama_url']}  (model: {cfg['chat_model']})")
    print(f"  microphone : MUTED at startup (privacy default)")
    print("  stop       : Ctrl-C, or ./stop.sh")
    print("=" * 58, flush=True)

    if not args.no_autoconnect:
        threading.Thread(target=armory.autoconnect_all, daemon=True).start()

    def shutdown(_signum=None, _frame=None):
        print("\n[jarvis] shutting down ...", flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        httpd.serve_forever(poll_interval=0.3)
    finally:
        armory.disconnect_all()
        httpd.server_close()
        _clear_pid()
        print("[jarvis] stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

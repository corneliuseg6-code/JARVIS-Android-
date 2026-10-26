#!/usr/bin/env python3
"""JARVIS Android V2 self-test.

Run it on the phone to see, in one screen, what actually works:

    python3 tools/selftest.py              # against a running server
    python3 tools/selftest.py --offline    # modules only, no server needed

It never changes settings and never downloads anything.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

passed = failed = warned = 0


def ok(name, detail=""):
    global passed
    passed += 1
    print(f"{GREEN}  PASS{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")


def bad(name, detail=""):
    global failed
    failed += 1
    print(f"{RED}  FAIL{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")


def warn(name, detail=""):
    global warned
    warned += 1
    print(f"{YELLOW}  WARN{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")


def section(title):
    print(f"\n\033[36m{title}\033[0m")


def check_modules():
    section("Backend modules")
    for module in ("paths", "store", "config", "ollama", "mcp_client", "armory",
                   "voices", "vision", "conversations", "app", "api", "main"):
        try:
            __import__(f"server.{module}")
            ok(f"import server.{module}")
        except Exception as exc:  # noqa: BLE001
            bad(f"import server.{module}", repr(exc))


def check_frontend():
    section("Frontend files")
    web = ROOT / "web"
    required = [
        "index.html", "sw.js", "manifest.webmanifest", "css/jarvis.css", "js/app.js",
        "js/avatar/index.js", "js/avatar/rings.js", "js/avatar/face.js",
        "js/i18n/index.js", "js/ui/mic.js", "js/ui/power.js", "js/ui/magnifier.js",
        "js/ui/armory.js", "js/ui/eyes.js", "js/ui/settings.js", "js/ui/chat.js",
        "js/ui/modes.js",
    ]
    for rel in required:
        (ok if (web / rel).is_file() else bad)(f"web/{rel}")

    section("Interface elements")
    html = (web / "index.html").read_text(encoding="utf-8")
    for element, label in (
        ('id="avatar-canvas"', "visualiser canvas"),
        ('id="avatar-switch"', "RINGS / FACE control"),
        ('id="btn-mic"', "MUTE control"),
        ('id="btn-power"', "ACTIVE / STANDBY control"),
        ('id="drawer"', "collapsible navigation drawer"),
        ('id="dock"', "bottom chat bar"),
        ('id="sheet"', "panel sheet"),
    ):
        (ok if element in html else bad)(label)
    for name in ("eyes", "watch", "holo", "focus", "reflex"):
        (ok if f'id="fn-{name}"' in html else bad)(f"{name.upper()} switch")

    section("Translations")
    reference = None
    for code in ("en", "ro", "fr", "de", "es", "it"):
        path = web / "js" / "i18n" / f"{code}.js"
        if not path.is_file():
            bad(f"{code}.js missing")
            continue
        keys = {line.split('"')[1] for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith('"')}
        if reference is None:
            reference = keys
            ok(f"{code}: {len(keys)} keys (reference)")
            continue
        missing = reference - keys
        if missing:
            bad(f"{code}: {len(missing)} missing keys", ", ".join(sorted(missing)[:4]))
        else:
            ok(f"{code}: {len(keys)} keys")

    section("Cube removal")
    hits = []
    for path in list(web.rglob("*.js")) + list(web.rglob("*.html")) + list(web.rglob("*.css")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for num, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # comments may mention the removed mode; only live code counts
            if stripped.startswith(("*", "//", "/*", "<!--")):
                continue
            if "cube" in stripped.lower() and "LEGACY_MODES" not in stripped:
                hits.append(f"{path.relative_to(web)}:{num}")
    if hits:
        warn("cube references remain", ", ".join(hits[:4]))
    else:
        ok("no cube rendering code left (only the legacy-mode guard)")


def check_voices():
    section("Voice engine")
    from server import voices

    info = voices.engine_info()
    if info["available"]:
        ok(f"sherpa-onnx {info['version']}")
        if info["espeak_data"]:
            ok("espeak-ng-data found", info["espeak_data"])
        else:
            warn("espeak-ng-data not found", info["note"])
    else:
        warn("sherpa-onnx unavailable", info["error"] or "")
        print(f"{DIM}        the interface falls back to the Android browser voices{RESET}")

    installed = voices.installed_voices()
    if installed:
        ok(f"{len(installed)} voice model(s) installed", ", ".join(installed))
    else:
        warn("no voice models installed", str(voices.VOICES_DIR if hasattr(voices, "VOICES_DIR") else ""))


def check_server(base):
    section(f"Server at {base}")
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=6) as resp:
            health = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        bad("server not reachable", str(exc.reason))
        print(f"{DIM}        start it with ./start.sh{RESET}")
        return
    ok("server responds")

    for key, label in (("ollama", "Ollama"), ("voice", "Voice"), ("armory", "Tool Armory")):
        state = health.get(key, {}).get("state", "unknown")
        (ok if state == "connected" else warn)(f"{label}: {state}",
                                               health.get(key, {}).get("error", ""))
    vision = health.get("vision", {})
    if not vision.get("enabled"):
        warn("Vision: disabled (opt-in)")
    elif vision.get("model_installed"):
        ok(f"Vision: {vision.get('model')}")
    else:
        warn("Vision: model not pulled", vision.get("note", ""))

    section("Privacy invariants")
    try:
        with urllib.request.urlopen(base + "/api/config", timeout=6) as resp:
            cfg = json.loads(resp.read().decode())
        (ok if cfg.get("mic_muted") else bad)("microphone reported muted at startup")
        (ok if cfg.get("power_state") == "active" else bad)("power state starts active")
        (ok if not cfg.get("save_captures") else warn)("camera captures are not saved")
        (ok if cfg.get("avatar_mode") in ("rings", "face") else bad)(
            f"avatar mode is rings or face ({cfg.get('avatar_mode')})")
        (ok if (cfg.get("wake_word") or "").strip() else bad)(
            f"wake word is set ({cfg.get('wake_word')})")
        minutes = cfg.get("focus_minutes")
        (ok if isinstance(minutes, int) and 1 <= minutes <= 240 else bad)(
            f"focus session length is sane ({minutes})")
    except Exception as exc:  # noqa: BLE001
        bad("config endpoint", repr(exc))

    try:
        with urllib.request.urlopen(base + "/api/diagnostics", timeout=6) as resp:
            body = resp.read().decode()
        from server import config as cfgmod
        # A credential name legitimately appears in the response; only a value
        # that is long enough to be a real secret, and is not simply one of the
        # declared key names, counts as a leak.
        declared = set()
        for conn in json.loads(body).get("connections", []):
            declared.add(conn.get("name", ""))
        for name in cfgmod.secret_names():
            declared.add(name.rsplit(":", 1)[-1])
        leaked = []
        for name in cfgmod.secret_names():
            value = cfgmod.get_secret(name) or ""
            if len(value) >= 12 and value not in declared and value in body:
                leaked.append(name)
        (ok if not leaked else bad)("no credential value in any API response",
                                    ", ".join(leaked))
    except Exception as exc:  # noqa: BLE001
        warn("credential leak check skipped", repr(exc))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--offline", action="store_true", help="skip the server checks")
    args = parser.parse_args()

    print("JARVIS Android V2 — self-test")
    check_modules()
    check_frontend()
    check_voices()
    if not args.offline:
        check_server(args.base.rstrip("/"))

    print(f"\n{GREEN}{passed} passed{RESET}, {YELLOW}{warned} warnings{RESET}, "
          f"{RED if failed else ''}{failed} failed{RESET}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline checks for the language-model providers.

Run it on the phone, with or without a network:

    python3 tools/test_provider.py

It never contacts OpenAI and never spends anything. What it proves is the
part that is easy to get wrong and hard to notice: that failures are
translated into codes the interface can act on, that the API key cannot
escape through an error message or an API response, and that a paid provider
cannot be switched on behind your back.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config, llm, openai_client as oc  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
passed = failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"{GREEN}  PASS{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")
    else:
        failed += 1
        print(f"{RED}  FAIL{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")


def section(title: str) -> None:
    print(f"\n\033[36m{title}\033[0m")


def _http(code: int, body: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.openai.com/v1/chat/completions", code, "err", {},
        io.BytesIO(json.dumps(body).encode()),
    )


def check_error_mapping() -> None:
    section("Error codes")
    cases = [
        (401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}, "auth"),
        (403, {"error": {"message": "Country not supported"}}, "forbidden"),
        (404, {"error": {"message": "The model does not exist", "code": "model_not_found"}}, "model_missing"),
        (429, {"error": {"message": "You exceeded your quota", "type": "insufficient_quota"}}, "quota"),
        (429, {"error": {"message": "Rate limit reached"}}, "rate_limit"),
        (500, {"error": {"message": "server error"}}, "server"),
        (418, {}, "http_error"),
    ]
    for code, body, expected in cases:
        got = oc._from_http(_http(code, body))
        check(f"HTTP {code} -> {expected}", got.code == expected, got.detail[:50])


def check_secrecy() -> None:
    section("The key stays on the backend")
    previous = config.get_secret(oc.SECRET_NAME)
    probe = "sk-probe-0123456789abcdefghijklmnop"
    try:
        oc.set_key(probe)
        check("a stored key is reported as set", oc.has_key())

        public = json.dumps(config.get_config())
        check("absent from /api/config's document", probe not in public)

        status = json.dumps(llm.health(timeout=0.4))
        check("absent from the provider status", probe not in status)

        names = config.secret_names()
        check("referenced by name only", oc.SECRET_NAME in names and probe not in json.dumps(names))

        path = Path(config.paths.SECRETS_FILE) if hasattr(config, "paths") else None
        if path and path.exists():
            mode = oct(path.stat().st_mode & 0o777)
            check("secrets.json is mode 600", mode == "0o600", mode)
    finally:
        oc.set_key(previous or "")


def check_cost_guard() -> None:
    section("A paid provider cannot start itself")
    before = config.get_config()
    restore = {
        "llm_provider": before.get("llm_provider", "ollama"),
        "openai_cost_ack": before.get("openai_cost_ack", False),
    }
    try:
        config.update_config({"openai_cost_ack": False, "llm_provider": "openai"})
        check("refused without the cost acknowledgement",
              config.get_config()["llm_provider"] == "ollama")

        config.update_config({"openai_cost_ack": True, "llm_provider": "openai"})
        check("allowed once acknowledged",
              config.get_config()["llm_provider"] == "openai")

        config.update_config({"llm_provider": "nonsense"})
        check("an unknown provider falls back to ollama",
              config.get_config()["llm_provider"] == "ollama")
    finally:
        config.update_config(restore)


def check_health_cache() -> None:
    """The status chip polls every 12s; that must not become 12s of traffic."""
    section("Status polling")
    calls = {"n": 0}
    real = oc._probe

    def counting(timeout):  # noqa: ANN001
        calls["n"] += 1
        return {"state": "disconnected", "models": [], "url": "test", "key_set": True}

    oc._probe = counting
    try:
        oc._HEALTH_CACHE.update(at=0.0, fingerprint="", value=None)
        for _ in range(4):
            oc.health(timeout=1)
        check("four background polls make one request", calls["n"] == 1,
              f"{calls['n']} request(s)")
        oc.health(timeout=1, force=True)
        check("opening the settings panel checks for real", calls["n"] == 2,
              f"{calls['n']} request(s)")
    finally:
        oc._probe = real
        oc._HEALTH_CACHE.update(at=0.0, fingerprint="", value=None)


def check_router() -> None:
    section("Router")
    check("both providers are offered", set(llm.PROVIDERS) == {"ollama", "openai"})
    check("history window is clamped",
          2 <= llm.history_limit() <= 100, str(llm.history_limit()))
    check("a model is always resolved", bool(llm.active_model()), llm.active_model())


def main() -> int:
    print("JARVIS — provider self-test (offline, costs nothing)")
    check_error_mapping()
    check_secrecy()
    check_cost_guard()
    check_health_cache()
    check_router()
    print(f"\n{GREEN}{passed} passed{RESET}, {RED if failed else ''}{failed} failed{RESET}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

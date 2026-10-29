#!/usr/bin/env python3
"""Offline checks for AI Connections: Claude, OpenAI and the Claude Code CLI.

Run it on the phone, with or without a network:

    python3 tools/test_connections.py

Nothing here contacts Anthropic or OpenAI and nothing here spends money. The
Claude Code section runs a *stub* `claude` — a shell script that prints what
the real one would — so the whole bridge is exercised (detection, argument
building, the approval gate, subprocess, JSON parsing) without a billed call.

What it proves is the part that is easy to get wrong and expensive to notice:

  * the API keys cannot escape through a response, a config document or an
    error message;
  * no paid provider can become active without being switched on *and*
    acknowledged;
  * Claude Code never runs without an explicit approval, never writes in
    plan mode, and cannot be pointed outside its working directory.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    anthropic_client as ac, claude_code as cc, config, llm, openai_client as oc,
)

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
    print(f"\n\033[36m{title}{RESET}")


def http_error(code: int, body: dict) -> urllib.error.HTTPError:
    import io as _io
    return urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages", code, "err", {},
        _io.BytesIO(json.dumps(body).encode()),
    )


# --------------------------------------------------------------------------
section("Anthropic error mapping")

CASES = [
    (401, {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}, "auth"),
    (403, {"error": {"type": "permission_error", "message": "not allowed"}}, "forbidden"),
    (404, {"error": {"type": "not_found_error", "message": "model not found"}}, "model_missing"),
    (400, {"error": {"type": "invalid_request_error", "message": "credit balance is too low"}}, "quota"),
    (400, {"error": {"type": "invalid_request_error", "message": "max_tokens required"}}, "invalid_request"),
    (429, {"error": {"type": "rate_limit_error", "message": "slow down"}}, "rate_limit"),
    (529, {"error": {"type": "overloaded_error", "message": "overloaded"}}, "overloaded"),
    (500, {"error": {"type": "api_error", "message": "boom"}}, "server"),
]
for status, body, expected in CASES:
    mapped = ac._from_http(http_error(status, body))
    check(f"HTTP {status} -> {expected}", mapped.code == expected, mapped.detail[:60])


# --------------------------------------------------------------------------
section("Message translation")

system, turns = ac._to_anthropic([
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi"},
    {"role": "user", "content": "one"},
    {"role": "user", "content": "two"},
])
check("the system prompt is hoisted out of the turns", system == "be brief")
check("no system message survives in the turns",
      all(t["role"] in ("user", "assistant") for t in turns))
check("consecutive same-role turns are merged", len(turns) == 3, f"{len(turns)} turns")
check("the merge keeps both texts", turns[-1]["content"] == "one\n\ntwo")

_, image_turns = ac._to_anthropic([{"role": "user", "content": "what is this", "images": ["QUJD"]}])
blocks = image_turns[0]["content"]
check("images become base64 content blocks",
      isinstance(blocks, list) and blocks[0]["type"] == "image"
      and blocks[0]["source"]["data"] == "QUJD")

_, leading = ac._to_anthropic([{"role": "assistant", "content": "orphan"},
                               {"role": "user", "content": "hi"}])
check("a leading assistant turn is dropped", leading and leading[0]["role"] == "user")


# --------------------------------------------------------------------------
section("The keys stay on the backend")

original_secrets = dict(config._secrets())
try:
    ac.set_key("sk-ant-test-0000000000000000000000")
    oc.set_key("sk-openai-test-00000000000000000")
    check("a stored Anthropic key reads back as set", ac.has_key())

    document = json.dumps(config.get_config())
    check("absent from /api/config's document", "sk-ant-test" not in document)
    check("referenced by name only",
          ac.SECRET_NAME in config.secret_names() and "sk-ant-test" not in str(config.secret_names()))

    status = json.dumps(ac._probe(0.001))
    check("absent from the provider status", "sk-ant-test" not in status)

    from server import paths
    mode = stat.S_IMODE(os.stat(paths.SECRETS_FILE).st_mode)
    check("secrets.json is mode 600", mode == 0o600, oct(mode))

    # --- the cost guard -------------------------------------------------
    config.update_config({"claude_enabled": False, "claude_cost_ack": False,
                          "llm_provider": "claude"})
    check("a disabled paid provider cannot be selected", llm.provider() == "ollama")

    config.update_config({"claude_enabled": True, "claude_cost_ack": False,
                          "llm_provider": "claude"})
    check("an unacknowledged paid provider cannot be selected", llm.provider() == "ollama")

    config.update_config({"claude_enabled": True, "claude_cost_ack": True,
                          "llm_provider": "claude"})
    check("allowed once enabled and acknowledged", llm.provider() == "claude")
    check("Claude resolves a model", llm.active_model("claude") == "claude-opus-5-5",
          llm.active_model("claude"))
    check("Claude is marked billed", "claude" in llm.BILLED)

    config.update_config({"llm_provider": "wat"})
    check("an unknown provider falls back to ollama", llm.provider() == "ollama")

    # --------------------------------------------------------------------
    section("Claude Code: the approval gate")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "project"
        (work / "sub").mkdir(parents=True)
        outside = Path(tmp) / "elsewhere"
        outside.mkdir()

        # A stub CLI: everything the bridge does is real except the model.
        binary = Path(tmp) / "bin" / "claude"
        binary.parent.mkdir()
        binary.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "--version" ]; then echo "9.9.9 (stub)"; exit 0; fi\n'
            'printf \'{"result":"stub saw: %s"}\\n\' "$*"\n'
        )
        binary.chmod(0o755)
        os.environ["PATH"] = str(binary.parent) + os.pathsep + os.environ["PATH"]

        config.update_config({
            "claude_code_enabled": False,
            "claude_code_allow_edits": False,
            "claude_code_workdir": str(work),
        })

        found = cc.detect()
        check("the stub CLI is detected", found["installed"] and "9.9.9" in found["version"],
              found["version"])

        # --- switched off --------------------------------------------------
        try:
            cc.propose("look at this")
            check("a disabled bridge refuses to propose", False)
        except cc.CodeError as exc:
            check("a disabled bridge refuses to propose", exc.code == "disabled", exc.code)

        config.update_config({"claude_code_enabled": True})

        # --- edits need their own switch -----------------------------------
        try:
            cc.propose("change this", mode=cc.EDIT)
            check("edit mode refused while edits are off", False)
        except cc.CodeError as exc:
            check("edit mode refused while edits are off",
                  exc.code == "edits_not_allowed", exc.code)

        # --- the working directory is a boundary ---------------------------
        try:
            cc.propose("peek", directory="../elsewhere")
            check("a path outside the working directory is refused", False)
        except cc.CodeError as exc:
            check("a path outside the working directory is refused",
                  exc.code == "outside_workdir", exc.code)
        try:
            cc.propose("peek", directory=str(outside))
            check("an absolute path outside is refused", False)
        except cc.CodeError as exc:
            check("an absolute path outside is refused", exc.code == "outside_workdir", exc.code)

        sub = cc.propose("look at the subdirectory", directory="sub")
        check("a subdirectory of the working directory is allowed",
              sub["directory"] == str(work / "sub"))
        cc.cancel(sub["id"])

        # --- what plan mode actually passes --------------------------------
        plan = cc.propose("summarise this project")
        argv = plan["argv"]
        check("plan mode passes --permission-mode plan",
              "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "plan")
        check("plan mode is also --restricted", "--restricted" in argv)
        check("permissions are never skipped",
              not any("skip-permissions" in a for a in argv))
        check("output is parsed, not scraped (one JSON event per line)",
              "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json")
        check("a turn limit is always set", "--max-turns" in argv)
        check("proposing does not run anything", plan["state"] == "pending")

        # --- approval is the only way to run -------------------------------
        approved = cc.approve(plan["id"])
        check("approval starts the run", approved["state"] == "running")
        for _ in range(100):
            record = cc.get(plan["id"])
            if record["state"] in ("done", "error"):
                break
            import time as _t
            _t.sleep(0.05)
        check("the run completes and the output is parsed",
              record["state"] == "done" and record["result"].startswith("stub saw:"),
              record["result"][:60] or record["error"][:60])

        try:
            cc.approve(plan["id"])
            check("a finished request cannot be re-run", False)
        except cc.CodeError as exc:
            check("a finished request cannot be re-run", exc.code == "not_pending", exc.code)

        # --- an edit run needs the switch AND the confirmation -------------
        config.update_config({"claude_code_allow_edits": True})
        edit = cc.propose("rename the helper", mode=cc.EDIT)
        check("edit mode passes acceptEdits, not bypass",
              edit["argv"][edit["argv"].index("--permission-mode") + 1] == "acceptEdits")
        try:
            cc.approve(edit["id"], allow_edits=False)
            check("an edit run without confirmation is refused", False)
        except cc.CodeError as exc:
            check("an edit run without confirmation is refused",
                  exc.code == "edits_not_confirmed", exc.code)
        cc.cancel(edit["id"])

        # --- turning the bridge off revokes edits --------------------------
        config.update_config({"claude_code_enabled": False})
        check("disabling the bridge also clears the edit permission",
              config.get_config()["claude_code_allow_edits"] is False)

        # --- nothing in the queue leaks the process handle ------------------
        check("the queue is serialisable and holds no handles",
              "_thread" not in json.dumps(cc.recent()))

finally:
    from server import store, paths as _paths
    store.write_json(_paths.SECRETS_FILE, original_secrets, secure=True)
    config.update_config({
        "llm_provider": "ollama", "claude_enabled": False, "claude_cost_ack": False,
        "claude_code_enabled": False, "claude_code_allow_edits": False,
        "claude_code_workdir": "",
    })

print(f"\n{GREEN}{passed} passed{RESET}"
      + (f", {RED}{failed} failed{RESET}" if failed else f", 0 failed{RESET}"))
sys.exit(1 if failed else 0)

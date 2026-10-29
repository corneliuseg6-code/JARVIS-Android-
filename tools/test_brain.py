#!/usr/bin/env python3
"""Offline checks for JARVIS V4: the multi-AI brain, Claude Code and OpenArt.

    python3 tools/test_brain.py

Nothing here contacts OpenAI, Anthropic, Google or OpenArt, and nothing
spends money. Every remote service is played by `tools/mock_cloud.py`, which
speaks each documented wire format; the test then checks exactly what JARVIS
sent and what it did with the answer. It runs in a throwaway JARVIS_HOME, so
your real settings, keys and workspace are never touched.

What it proves:
  * all four brains stream, and all four can call MCP tools — with each
    provider's own tool-result shape, and Claude's thinking signature and
    Gemini's thoughtSignature returned unchanged;
  * every paid turn is metered from the provider's own token counts, and a
    budget is a hard stop that routing falls back from;
  * the routing modes pick who they say they pick, and say why;
  * the chat endpoint falls back to the next brain when one fails before
    answering, and tells the interface;
  * OpenArt-style sign-in follows the MCP authorization flow (discovery,
    registration, PKCE, resource indicator, refresh) and the tokens never
    reach a response;
  * a credit-spending tool is held for approval even when it claims to be
    read-only, and its image lands in the workspace;
  * Claude Code runs only when approved, reports progress, cost and a diff,
    and can revert exactly its own edits.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="jarvis-test-brain-"))
os.environ["JARVIS_HOME"] = str(HOME)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import mock_cloud  # noqa: E402
from server import (  # noqa: E402
    anthropic_client, api, armory, claude_code as cc, config, gemini_client, llm, media,
    oauth, openai_client, paths, toolchat, usage,
)
from server.app import Request  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
passed = failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"{GREEN}  PASS{RESET}  {name}{DIM}{'  ' + str(detail) if detail else ''}{RESET}")
    else:
        failed += 1
        print(f"{RED}  FAIL{RESET}  {name}{DIM}{'  ' + str(detail) if detail else ''}{RESET}")


def section(title: str) -> None:
    print(f"\n\033[36m{title}{RESET}")


def requests_for(service: str) -> list[tuple]:
    return [r for r in mock_cloud.STATE["requests"] if r[0] == service]


def drain(events) -> list[dict]:
    return list(events)


def chat(message: str, conv: str = "") -> list[tuple[str, dict]]:
    """Drive /api/chat in-process and parse its SSE frames."""
    body = json.dumps({"message": message, "language": "en",
                       **({"conversation_id": conv} if conv else {})}).encode()
    response = api.post_chat(Request("POST", "/api/chat", {}, {}, body))
    frames = []
    for raw in response.stream:
        text = raw.decode()
        event = data = None
        for line in text.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event:
            frames.append((event, data))
    return frames


server, PORT = mock_cloud.start()
BASE = f"http://127.0.0.1:{PORT}"
paths.ensure_dirs()

config.update_config({
    "openai_base_url": BASE + "/v1",
    "anthropic_base_url": BASE,
    "gemini_base_url": BASE,
    "ollama_url": BASE,
    "cost_daily_limit_usd": 0, "cost_monthly_limit_usd": 0,
})
openai_client.set_key("sk-test-openai-0123456789abcdef")
anthropic_client.set_key("sk-ant-test-0123456789abcdef")
gemini_client.set_key("AIza-test-0123456789abcdefghij")

# A real stdio MCP server with an `echo` tool, for the tool loop.
echo_conn = armory.create_connection({
    "name": "Echo", "transport": "stdio",
    "command": f"{sys.executable} {ROOT / 'tools' / 'mock_mcp_server.py'}",
})
armory.connect(echo_conn["id"])

try:
    # ------------------------------------------------------------------
    section("Gates: a paid brain needs switch + acknowledgement + key")
    ok, why = llm.usable("gemini")
    check("Gemini is unusable while switched off", not ok, why)
    config.update_config({"gemini_enabled": True})
    ok, why = llm.usable("gemini")
    check("…and while its cost notice is unacknowledged", not ok and "cost" in why, why)
    config.update_config({"llm_provider": "gemini"})
    check("it cannot be selected as the brain yet", llm.provider() == "ollama", llm.provider())
    for p in ("openai", "claude", "gemini"):
        config.update_config({f"{p}_enabled": True, f"{p}_cost_ack": True})
    check("all three paid brains usable once gated through",
          all(llm.usable(p)[0] for p in ("openai", "claude", "gemini")))

    # ------------------------------------------------------------------
    section("Plain streaming on every brain, with metered cost")
    usage.reset()
    for which, expect in (("ollama", "Hello from Ollama"), ("openai", "Hello from OpenAI"),
                          ("claude", "Hello from Claude"), ("gemini", "Hello from Gemini")):
        events = drain(llm.chat_stream_full([{"role": "user", "content": "hi"}], which=which))
        text = "".join(e["text"] for e in events if e["type"] == "token")
        check(f"{which} streams its answer", text.strip() == expect, text.strip())
    summary = usage.summary()
    check("OpenAI usage recorded from its usage chunk (1000 in / 200 out)",
          any(r["provider"] == "openai" and r["input"] == 1000 and r["output"] == 200
              for r in summary["recent"]))
    check("Claude usage from message_start + message_delta (2000 / 300)",
          any(r["provider"] == "claude" and r["input"] == 2000 and r["output"] == 300
              for r in summary["recent"]))
    check("Gemini thinking tokens billed as output (500 / 150)",
          any(r["provider"] == "gemini" and r["input"] == 500 and r["output"] == 150
              for r in summary["recent"]))
    expected = (1000 * 0.15 + 200 * 0.60) / 1e6
    got = summary["today"]["openai"]
    check("cost = tokens × list price (gpt-4o-mini)", abs(got - expected) < 1e-9, f"{got:.6f}")
    check("Ollama is free and never recorded",
          not any(r["provider"] == "ollama" for r in summary["recent"]))
    unknown = usage.price_of("openai", "gpt-something-new")
    check("an unknown model is priced at the worst known rate, and says so",
          unknown["source"] == "unknown_model_worst_case" and unknown["output"] >= 10.0, unknown)
    config.update_config({"price_overrides": {"openai:gpt-something-new": [1, 2]}})
    check("a user price override wins", usage.price_of("openai", "gpt-something-new")["source"] == "custom")

    sent = requests_for("openai")[-1][2]
    check("OpenAI is asked for its usage chunk", sent.get("stream_options") == {"include_usage": True})
    check("a paid answer is capped (max_completion_tokens)", sent.get("max_completion_tokens") == 4096)
    sent = requests_for("anthropic")[-1]
    check("Anthropic gets anthropic-version 2026-10-21", {k.lower(): v for k, v in sent[3].items()}.get("anthropic-version") == "2026-10-21")
    check("…and no temperature (rejected on that version)", "temperature" not in sent[2])
    sent = requests_for("gemini")[-1]
    check("Gemini key goes in a header, never the URL",
          "key=" not in sent[1]
          and {k.lower(): v for k, v in sent[3].items()}.get("x-goog-api-key", "").startswith("AIza"))

    # ------------------------------------------------------------------
    section("MCP tools on every brain")
    definitions, lookup = toolchat.catalogue()
    echo_name = next(n for n, e in lookup.items() if e["tool"] == "echo")
    for which in ("ollama", "openai", "claude", "gemini"):
        mock_cloud.STATE["requests"].clear()
        events = drain(toolchat.run(
            [{"role": "system", "content": "be brief"}, {"role": "user", "content": "please echo banana"}],
            model=llm.active_model(which), which=which))
        results = [e for e in events if e["type"] == "tool_result"]
        answer = "".join(e.get("text", "") for e in events if e["type"] == "token")
        check(f"{which}: the echo tool really ran", results and results[0]["ran"]
              and "banana" in results[0]["text"], results[0]["text"] if results else "no call")
        check(f"{which}: the answer uses the tool's output", "banana" in answer, answer.strip())

        if which == "openai":
            second = requests_for("openai")[-1][2]["messages"]
            assistant = next(m for m in second if m.get("tool_calls"))
            tool_msg = second[-1]
            check("OpenAI: arguments sent back as a JSON string",
                  isinstance(assistant["tool_calls"][0]["function"]["arguments"], str))
            check("OpenAI: tool result carries the matching tool_call_id",
                  tool_msg.get("role") == "tool" and tool_msg.get("tool_call_id") == "call_abc")
        if which == "claude":
            turns = requests_for("anthropic")[-1][2]["messages"]
            assistant = turns[-2]
            check("Claude: thinking block replayed with its signature",
                  any(b.get("type") == "thinking" and b.get("signature") == "SIG123"
                      for b in assistant["content"]))
            check("Claude: tool_use replayed with parsed input",
                  any(b.get("type") == "tool_use" and b.get("input") == {"text": "banana"}
                      for b in assistant["content"]))
            check("Claude: tool_result in a user turn, matching id",
                  turns[-1]["role"] == "user" and turns[-1]["content"][0]["type"] == "tool_result"
                  and turns[-1]["content"][0]["tool_use_id"] == "toolu_1")
            check("Claude: tools sent as name/description/input_schema",
                  "input_schema" in requests_for("anthropic")[-1][2]["tools"][0])
        if which == "gemini":
            contents = requests_for("gemini")[-1][2]["contents"]
            model_turn = contents[-2]
            check("Gemini: functionCall part returned with its thoughtSignature",
                  model_turn["role"] == "model"
                  and model_turn["parts"][0].get("thoughtSignature") == "GEMSIG")
            check("Gemini: functionResponse sent in a user turn",
                  contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0])
            decl = requests_for("gemini")[-1][2]["tools"][0]["functionDeclarations"]
            check("Gemini: function declarations sent", any(d["name"] == echo_name for d in decl))

    cleaned = gemini_client.clean_schema({
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": ["string", "null"], "default": "x"}},
    })
    check("Gemini schema cleaner drops what Gemini rejects",
          "$schema" not in cleaned and "additionalProperties" not in cleaned
          and cleaned["properties"]["a"] == {"type": "string", "nullable": True}, cleaned)

    # ------------------------------------------------------------------
    section("Routing")
    config.update_config({"routing_mode": "local"})
    r = llm.route("write me a python script")
    check("Local: only Ollama, whatever the task",
          [c["provider"] for c in r["candidates"]] == ["ollama"])
    config.update_config({"routing_mode": "cloud", "cloud_order": ["gemini", "claude", "openai"]})
    r = llm.route("hi")
    check("Cloud: paid brains in the chosen order, Ollama last as fallback",
          [c["provider"] for c in r["candidates"]] == ["gemini", "claude", "openai", "ollama"],
          [c["provider"] for c in r["candidates"]])
    config.update_config({"routing_mode": "auto"})
    r = llm.route("what time is it", ollama_up=True)
    check("Auto: a short everyday turn stays local",
          r["candidates"][0]["provider"] == "ollama", r["candidates"][0]["reason"])
    r = llm.route("fix this python traceback please", ollama_up=True)
    check("Auto: a coding task goes to Claude first",
          r["candidates"][0]["provider"] == "claude", r["candidates"][0]["reason"])
    r = llm.route("hello", ollama_up=False)
    check("Auto: local offline → cloud, and says so",
          r["candidates"][0]["provider"] != "ollama" and "offline" in r["candidates"][0]["reason"])
    config.update_config({"claude_enabled": False})
    r = llm.route("debug my code", ollama_up=True)
    check("Auto: a switched-off brain is skipped, with its reason",
          r["candidates"][0]["provider"] == "gemini"
          and any(s["provider"] == "claude" for s in r["skipped"]), r["skipped"])
    config.update_config({"claude_enabled": True})
    config.update_config({"routing_mode": "manual", "llm_provider": "claude"})
    r = llm.route("anything")
    check("Manual: the chosen brain, then the free one",
          [c["provider"] for c in r["candidates"]] == ["claude", "ollama"])
    config.update_config({"auto_fallback": False})
    check("…and no fallback when that is switched off",
          [c["provider"] for c in llm.route("x")["candidates"]] == ["claude"])
    config.update_config({"auto_fallback": True})

    # ------------------------------------------------------------------
    section("Budgets are a hard stop")
    usage.reset()
    config.update_config({"openai_daily_limit_usd": 0.05, "routing_mode": "manual",
                          "llm_provider": "openai"})
    check("limits are kept to the cent", config.get_config()["openai_daily_limit_usd"] == 0.05)
    drain(llm.chat_stream_full([{"role": "user", "content": "hi"}], which="openai"))
    check("under the limit, OpenAI still answers", usage.spent("openai") < 0.05)
    usage.record("openai", "gpt-4o-mini", 100_000, 100_000)     # a big turn: $0.075
    check("spend is now over the limit", usage.spent("openai") >= 0.05, usage.spent("openai"))
    calls_before = len(requests_for("openai"))
    try:
        drain(llm.chat_stream_full([{"role": "user", "content": "hi"}], which="openai"))
        check("the next OpenAI call is refused", False)
    except llm.LLMError as exc:
        check("the next OpenAI call is refused", exc.code == "budget_exceeded", exc.detail[:80])
    check("…before anything is sent", len(requests_for("openai")) == calls_before)
    r = llm.route("hi")
    check("routing skips the provider over budget and falls back to Ollama",
          [c["provider"] for c in r["candidates"]] == ["ollama"], r["skipped"])
    config.update_config({"openai_daily_limit_usd": 0, "cost_daily_limit_usd": 0.01})
    check("the all-provider daily limit stops every paid brain",
          not any(llm.usable(p)[0] for p in ("openai", "claude", "gemini")))
    check("…but never the free one", llm.usable("ollama")[0])
    config.update_config({"cost_daily_limit_usd": 0})
    usage.reset()

    # ------------------------------------------------------------------
    section("Chat endpoint: route, fallback, cost")
    config.update_config({"routing_mode": "manual", "llm_provider": "claude"})
    frames = chat("hi there")
    kinds = [k for k, _ in frames]
    route = next(d for k, d in frames if k == "route")
    check("the route is announced before the answer",
          kinds.index("route") < kinds.index("token") and route["provider"] == "claude", route)
    done = next(d for k, d in frames if k == "done")
    check("done names who answered and what it cost",
          done["provider"] == "claude" and done["usd"] > 0, f"{done['provider']} ${done['usd']}")
    check("a cost event is streamed", "cost" in kinds)

    mock_cloud.STATE["fail"]["anthropic"] = (
        400, {"type": "error", "error": {"type": "invalid_request_error",
                                         "message": "Your credit balance is too low"}})
    frames = chat("hello again")
    routes = [d for k, d in frames if k == "route"]
    done = next((d for k, d in frames if k == "done"), {})
    check("Claude out of credit → falls back, and says why",
          any(r.get("fallback") and r.get("error") == "quota" for r in routes), routes[-1])
    check("…and Ollama answers the same turn", done.get("provider") == "ollama"
          and "Ollama" in done.get("text", ""), done.get("text"))
    config.update_config({"auto_fallback": False})
    frames = chat("and again")
    err = next((d for k, d in frames if k == "error"), None)
    check("with fallback off, the failure is reported, not hidden",
          err is not None and err["code"] == "quota", err)
    config.update_config({"auto_fallback": True})
    mock_cloud.STATE["fail"].clear()

    mock_cloud.STATE["ollama_no_tools"] = True
    config.update_config({"llm_provider": "ollama"})
    frames = chat("hello local")
    done = next((d for k, d in frames if k == "done"), {})
    check("a local model without tool support answers without them",
          done.get("provider") == "ollama" and "Ollama" in done.get("text", ""), done.get("text"))
    mock_cloud.STATE["ollama_no_tools"] = False

    # ------------------------------------------------------------------
    section("AI Tools menu: real status, no paid calls")
    config.update_config({"routing_mode": "manual", "llm_provider": "claude", "gemini_enabled": False})
    before = {svc: len(requests_for(svc)) for svc in ("openai", "anthropic", "gemini")}
    overview = json.loads(api.get_tools_overview(Request("GET", "/", {}, {}, b"")).body)
    states = {m["provider"]: m["state"] for m in overview["models"]}
    check("models listed in menu order", [m["provider"] for m in overview["models"]]
          == ["openai", "claude", "gemini", "ollama"])
    check("the brain in use reports its live health", states["claude"] == "connected", states)
    check("a usable paid brain not in use says 'untested'", states["openai"] == "untested", states)
    check("a switched-off brain says 'off'", states["gemini"] == "off", states)
    check("the local model is probed for real", states["ollama"] == "connected", states)
    check("no paid completion or probe was spent on brains not in use",
          len(requests_for("openai")) == before["openai"] and len(requests_for("gemini")) == before["gemini"])
    check("Claude Code, OpenArt and MCP sections are present",
          {"code", "openart", "mcp"} <= set(overview) and isinstance(overview["mcp"], list))
    check("no secret in the overview",
          not any(x in json.dumps(overview) for x in ("sk-test", "sk-ant-test", "AIza-test")))
    config.update_config({"gemini_enabled": True})

    # ------------------------------------------------------------------
    section("Small local models + MCP: every turn ends in an answer")
    from server import workspace as ws
    ws.ensure()
    check("a model Ollama refused tools for is remembered (from the test above)",
          "llama3.2:3b" in api._NO_TOOL_MODELS)
    api._NO_TOOL_MODELS.clear()      # this section's model does support tools
    files = armory.create_connection({
        "name": "JARVIS Files", "transport": "stdio",
        "command": f"{sys.executable} {ROOT / 'tools' / 'fs_mcp_server.py'} {HOME}",
    })
    check("the real JARVIS Files server connects", armory.connect(files["id"])["state"] == "connected")
    config.update_config({"routing_mode": "manual", "llm_provider": "ollama", "ollama_num_ctx": 0})
    mock_cloud.STATE["ollama_list_target"] = "workspace_list"

    def turn(modes):
        mock_cloud.STATE["ollama_small"] = modes
        mock_cloud.STATE["requests"].clear()
        frames = chat("JARVIS, list the files in my workspace.")
        done = next((d for k, d in frames if k == "done"), None)
        err = next((d for k, d in frames if k == "error"), None)
        ran = [d for k, d in frames if k == "tool_result" and d.get("ran")]
        return frames, done, err, ran

    frames, done, err, ran = turn({"plain"})
    check("the workspace tool really runs", len(ran) == 1 and "Projects/" in ran[0]["text"],
          ran[0]["text"][:60] if ran else "nothing ran")
    check("…and the final answer lists the workspace sections",
          done is not None and all(x in done["text"] for x in ("Projects", "Research", "Journal")),
          (done or {}).get("text", "")[:80])
    sent = requests_for("ollama")[0][2]
    check("a tool turn asks Ollama for a 4096-token context when none is set",
          (sent.get("options") or {}).get("num_ctx") == 4096, sent.get("options"))

    frames, done, err, ran = turn({"bare"})
    check("a tool name without JARVIS's prefix still resolves and runs",
          len(ran) == 1 and done is not None, [k for k, _ in frames])

    frames, done, err, ran = turn({"loop"})
    check("a model that repeats the same call: the tool runs once",
          len(ran) == 1, f"{len(ran)} runs")
    check("…and the turn still ends with the answer", done is not None and "Projects" in done["text"])
    check("…well before the six-round limit", len(requests_for("ollama")) <= 3,
          f"{len(requests_for('ollama'))} model calls")

    frames, done, err, ran = turn({"empty"})
    check("silent after the result: asked again without tools, and answers",
          done is not None and "Projects" in done["text"]
          and "tools" not in requests_for("ollama")[-1][2])

    frames, done, err, ran = turn({"loop", "mute"})
    check("silent even when asked: the user gets the tool's own result",
          done is not None and done["text"].startswith("Here is what `workspace_list` returned"),
          (done or {}).get("text", "")[:60])

    mock_cloud.STATE["ollama_list_target"] = "list_directory"
    mock_cloud.STATE["ollama_small"] = {"plain"}
    frames = chat("JARVIS, list the files in my workspace.")
    sent = [r[2] for r in requests_for("ollama")]
    tool_msgs = [m for body in sent for m in body.get("messages", []) if m.get("role") == "tool"]
    check("a local model is shown at most 2500 characters of one result",
          tool_msgs and all(len(m["content"]) <= toolchat.LOCAL_RESULT_CHARS for m in tool_msgs))

    mock_cloud.STATE["ollama_small"] = set()
    mock_cloud.STATE["fail"]["ollama"] = (200, {"error": "model runner has unexpectedly stopped"})
    frames = chat("hello there")
    err = next((d for k, d in frames if k == "error"), None)
    check("an Ollama error line becomes a visible error, not an empty reply",
          err is not None and "unexpectedly stopped" in err.get("detail", ""), err)
    mock_cloud.STATE["fail"].clear()

    mock_cloud.STATE["ollama_small"] = {"silent"}
    frames = chat("hello there")
    err = next((d for k, d in frames if k == "error"), None)
    kinds = [k for k, _ in frames]
    check("a model that says nothing at all: an explicit empty_answer error, never a silent done",
          err is not None and err["code"] == "empty_answer" and "done" not in kinds, kinds)
    mock_cloud.STATE["ollama_small"] = set()

    config.update_config({"ollama_num_ctx": 8192})
    mock_cloud.STATE["ollama_small"] = {"plain"}
    mock_cloud.STATE["requests"].clear()
    mock_cloud.STATE["ollama_list_target"] = "workspace_list"
    chat("JARVIS, list the files in my workspace.")
    check("a context size set in Settings always wins",
          requests_for("ollama")[0][2]["options"]["num_ctx"] == 8192)
    config.update_config({"ollama_num_ctx": 0})
    mock_cloud.STATE["ollama_small"] = set()
    armory.delete_connection(files["id"])

    # ------------------------------------------------------------------
    section("Keys never leave the backend")
    public = json.dumps([
        config.get_config(), json.loads(api.get_llm_connections(Request("GET", "/", {}, {}, b"")).body),
        json.loads(api.get_llm_status(Request("GET", "/", {}, {}, b"")).body),
    ])
    for secret in ("sk-test-openai", "sk-ant-test", "AIza-test"):
        check(f"{secret}… absent from config, connections and status", secret not in public)
    api.post_config(Request("POST", "/api/config", {}, {},
                            json.dumps({"gemini_api_key": "AIza-leak"}).encode()))
    check("a key posted to /api/config is dropped", "AIza-leak" not in json.dumps(config.get_config()))

    # ------------------------------------------------------------------
    section("OpenArt-style service: MCP sign-in (OAuth 2.1 + PKCE)")
    api.OPENART_URL = BASE + "/mcp"
    status = json.loads(api.post_openart_setup(Request("POST", "/", {}, {}, b"")).body)
    conn_id = status["id"]
    check("setup adds a billed, sign-in connection",
          status["configured"] and status["billed"] and not status["auth"]["signed_in"])
    result = armory.connect(conn_id)
    check("connecting before sign-in is refused with a reason",
          result["state"] == "error" and "sign in" in result["error"].lower(), result["error"])

    headers = {"Host": "127.0.0.1:8787"}
    start_req = Request("POST", f"/api/armory/{conn_id}/oauth/start", {}, headers, b"")
    start_req.params = {"conn_id": conn_id}
    started = json.loads(api.post_oauth_start(start_req).body)
    auth_url = started["authorize_url"]
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(auth_url).query).items()}
    check("discovery found the authorization server from the 401",
          bool(requests_for("register")) and auth_url.startswith(BASE + "/authorize"))
    check("registered as a public client with a loopback redirect",
          requests_for("register")[-1][2]["token_endpoint_auth_method"] == "none"
          and q["redirect_uri"] == "http://127.0.0.1:8787/api/oauth/callback")
    check("PKCE S256 and a resource indicator are sent",
          q["code_challenge_method"] == "S256" and q["resource"] == BASE + "/mcp")

    # The user signs in on the provider's page, which redirects back.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         type("NoRedirect", (urllib.request.HTTPRedirectHandler,),
                                              {"redirect_request": lambda *a, **k: None})())
    try:
        opener.open(auth_url)
        location = ""
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
    back = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(location).query).items()}
    bad = api.get_oauth_callback(Request("GET", "/api/oauth/callback",
                                         {"state": ["forged"], "code": [back["code"]]}, {}, b""))
    check("a callback with a forged state is refused", b"Sign-in failed" in bad.body)
    page = api.get_oauth_callback(Request("GET", "/api/oauth/callback",
                                          {"state": [back["state"]], "code": [back["code"]]}, {}, b""))
    check("the real callback signs in and connects", b"Signed in" in page.body
          and b"not connected" not in page.body, page.body[-120:])
    token_req = requests_for("token")[-1][2]
    check("the code exchange carried the PKCE verifier and resource",
          token_req.get("code_verifier") and token_req.get("resource") == BASE + "/mcp")
    st = oauth.status(conn_id)
    check("signed in, refreshable, and no token in the status", st["signed_in"] and st["refreshable"]
          and "at-" not in json.dumps(st))
    everything = json.dumps([armory.list_connections(),
                             json.loads(api.get_openart_status(Request("GET", "/", {}, {}, b"")).body)])
    check("the access token never appears in a response", "at-" not in everything and "rt-" not in everything)

    # Expired token: the next call refreshes once and goes through.
    stored = json.loads(config.get_secret(f"conn:{conn_id}:oauth"))
    mock_cloud.STATE["tokens"][stored["tokens"]["access_token"]] = 0
    listed = armory.call_tool(conn_id, "list_models", {})
    check("an expired token is refreshed and the call retried", listed.get("ok"), listed)

    # ------------------------------------------------------------------
    section("Credit spending is approved one call at a time")
    config.update_config({"mcp_allow_writes": True})   # even with writes allowed
    definitions, lookup = toolchat.catalogue()
    gen = next(e for e in lookup.values() if e["tool"] == "generate_image")
    lst = next(e for e in lookup.values() if e["tool"] == "list_models")
    check("generate_image is held although it claims readOnlyHint", gen["billed"] and gen["writes"])
    check("list_models (a plain read) is not held", not lst["writes"])
    before = len(requests_for("mcp"))
    outcome = toolchat.execute(gen, {"prompt": "a red fox"}, conversation_id="")
    check("the call is refused and queued, not run", not outcome["ran"] and outcome.get("needs_approval")
          and outcome["pending"]["billed"])
    check("…and nothing reached the server",
          not any((r[2].get("params") or {}).get("name") == "generate_image"
                  for r in requests_for("mcp")[before:]))
    pending_id = outcome["pending"]["id"]
    refused = None
    try:
        api.post_chat_tool_run(Request("POST", "/", {}, {}, json.dumps({"id": pending_id}).encode()))
    except Exception as exc:  # noqa: BLE001
        refused = str(exc)
    check("running it needs confirm_spend as well", refused and "confirm_spend" in refused, refused)
    ran = json.loads(api.post_chat_tool_run(Request("POST", "/", {}, {}, json.dumps(
        {"id": pending_id, "confirm_spend": True}).encode())).body)
    check("approved: it runs once", ran["ran"] and "credits" in ran["text"], ran["text"][:80])
    saved = ran.get("media") or []
    check("the returned image is saved in the workspace",
          saved and saved[0]["path"].startswith("Results/Media/") and saved[0]["mime"] == "image/png")
    data, mime = media.read(saved[0]["path"])
    check("…and served back as image/png", mime == "image/png" and data[:4] == b"\x89PNG")
    try:
        media.read("../config.json")
        check("the media route cannot read outside the workspace", False)
    except Exception:  # noqa: BLE001
        check("the media route cannot read outside the workspace", True)
    try:
        toolchat.run_approved(pending_id)
        check("the approval cannot be spent twice", False)
    except toolchat.ToolChatError:
        check("the approval cannot be spent twice", True)
    config.update_config({"mcp_allow_writes": False})

    signout = Request("POST", "/", {}, {}, b"")
    signout.params = {"conn_id": conn_id}
    api.post_oauth_signout(signout)
    check("sign out forgets the tokens", not oauth.status(conn_id)["signed_in"]
          and "tokens" not in json.dumps(config.get_secret(f"conn:{conn_id}:oauth") or ""))

    # ------------------------------------------------------------------
    section("Claude Code: approval, progress, cost, diff, revert")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "project"
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        (work / "app.py").write_text("print('bug')\n")
        (work / "notes.txt").write_text("mine\n")
        subprocess.run(["git", "-C", str(work), "add", "."], check=True)
        subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], check=True)
        (work / "notes.txt").write_text("mine, edited before the run\n")   # already dirty

        stub = Path(tmp) / "bin" / "claude"
        stub.parent.mkdir()
        stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "9.9.9 (stub)"; exit 0; fi\n'
            'if [ "$1" = "--help" ]; then echo "Usage: claude --print --restricted '
            '--permission-prompts --max-budget-usd"; exit 0; fi\n'
            'if [ "$1" = "auth" ]; then echo \'{"loggedIn": true, "authMethod": "api_key"}\'; exit 0; fi\n'
            'echo \'{"type":"system","subtype":"init","session_id":"abc-123","model":"stub"}\'\n'
            'echo \'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"app.py"}}]}}\'\n'
            "printf \"print('fixed')\\n\" > app.py\n"
            'echo "new" > added.py\n'
            'echo \'{"type":"result","subtype":"success","is_error":false,"result":"Fixed the bug.","total_cost_usd":0.0123,"session_id":"abc-123"}\'\n'
        )
        stub.chmod(0o755)
        os.environ["PATH"] = str(stub.parent) + os.pathsep + os.environ["PATH"]
        cc.clear_cache()
        config.update_config({"claude_code_enabled": True, "claude_code_allow_edits": True,
                              "claude_code_workdir": str(work), "claude_code_max_budget_usd": 0.5})

        st = cc.status()
        check("auth status comes from the CLI itself", st["auth"]["logged_in"]
              and st["auth"]["method"] == "api_key", st["auth"])

        # Through the chat tool: queued, never run.
        definitions, lookup = toolchat.catalogue()
        check("the chat is offered the Claude Code tool", "jarvis__claude_code" in lookup)
        out = toolchat.execute(lookup["jarvis__claude_code"],
                               {"task": "fix the bug in app.py", "mode": "edit"})
        record = out["code_request"]
        check("the chat tool only queues the task", record["state"] == "pending"
              and "NOT RUN" in out["text"] and record["origin"] == "chat")
        check("budget cap passed to the CLI", "--max-budget-usd" in record["argv"]
              and record["argv"][record["argv"].index("--max-budget-usd") + 1] == "0.50")
        check("edit mode stays restricted when no shell is granted", "--restricted" in record["argv"])
        check("headless prompts are denied, never bypassed",
              "--permission-prompts" in record["argv"]
              and not any("skip-permissions" in a for a in record["argv"]))
        check("(app.py untouched until approved)", "bug" in (work / "app.py").read_text())

        cc.approve(record["id"], allow_edits=True)
        for _ in range(200):
            rec = cc.get(record["id"])
            if rec["state"] not in ("running", "pending"):
                break
            time.sleep(0.05)
        check("the approved run finishes", rec["state"] == "done" and rec["result"] == "Fixed the bug.",
              rec["state"] + " " + rec["error"][:100])
        check("progress lines were captured", any("Edit" in p for p in rec["progress"]), rec["progress"])
        check("cost and session are recorded", rec["cost_usd"] == 0.0123 and rec["session_id"] == "abc-123")
        ch = rec["changes"] or {}
        check("the diff names exactly the run's files",
              ch.get("files") == ["added.py", "app.py"] and "fixed" in ch.get("diff", ""), ch.get("files"))
        check("a file dirty before the run is reported, not claimed",
              "notes.txt" in ch.get("already_dirty", []) and "notes.txt" not in ch.get("files", []))

        follow = cc.propose("now add a test", mode=cc.EDIT, resume=rec["session_id"])
        check("a follow-up resumes the same session", "--resume" in follow["argv"]
              and follow["argv"][follow["argv"].index("--resume") + 1] == "abc-123")
        cc.cancel(follow["id"])

        try:
            cc.revert(record["id"])
            check("revert needs confirmation", False)
        except cc.CodeError as exc:
            check("revert needs confirmation", exc.code == "confirm_required")
        reverted = cc.revert(record["id"], confirm=True)
        check("revert restores the run's edits and deletes its new files",
              "bug" in (work / "app.py").read_text() and not (work / "added.py").exists(),
              reverted["changes"])
        check("…and leaves the user's own uncommitted edit alone",
              (work / "notes.txt").read_text() == "mine, edited before the run\n")
        config.update_config({"claude_code_enabled": False})
        check("the chat tool disappears when Claude Code is off",
              "jarvis__claude_code" not in toolchat.catalogue()[1])

finally:
    armory.disconnect_all()
    server.shutdown()

print(f"\n{GREEN}{passed} passed{RESET}"
      + (f", {RED}{failed} failed{RESET}" if failed else f", 0 failed{RESET}"))
print(f"{DIM}scratch home: {HOME}{RESET}")
sys.exit(1 if failed else 0)

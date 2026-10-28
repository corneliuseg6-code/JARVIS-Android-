#!/usr/bin/env python3
"""Offline test of V4 Phase 2: tools on the paid brains, and routing.

    python3 tools/test_cloud_tools.py

What is real here and what is not
---------------------------------
**Real:** the OpenAI and Claude clients, their SSE parsing, the assembly of
tool calls streamed in fragments, the translation of a tool round back into
each API's own message shape, the tool loop, and the routing rules.

**Stubbed:** the two APIs. A local HTTP server answers ``/chat/completions``
and ``/v1/messages`` with streams in the documented shapes — split the way the
real ones split them, arguments a few characters at a time — and records
every request, so the test can read back exactly what JARVIS sent. The tool
itself is stubbed too; the real MCP path is covered by test_toolcall.py.

Nothing here reaches the internet, and no key is real, so nothing is billed.
It runs in a throwaway JARVIS_HOME and never touches your settings.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["JARVIS_HOME"] = tempfile.mkdtemp(prefix="jarvis-cloudtools-")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    anthropic_client, armory, config, llm, openai_client, toolchat,
)

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"{GREEN}  PASS{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")
    else:
        failed += 1
        print(f"{RED}  FAIL{RESET}  {name}{DIM}{'  ' + detail if detail else ''}{RESET}")


def section(title: str) -> None:
    print(f"\n\033[36m{title}{RESET}")


# --------------------------------------------------------------------------
# stand-ins for the two APIs
# --------------------------------------------------------------------------

REQUESTS: list[dict] = []


def _sse(*payloads: dict | str) -> bytes:
    out = []
    for p in payloads:
        out.append(f"data: {p if isinstance(p, str) else json.dumps(p)}\n\n")
    return "".join(out).encode()


def _openai_reply(body: dict) -> bytes:
    """A tool call on the first round, words once a tool result comes back."""
    last = body["messages"][-1]
    if last["role"] == "tool":
        return _sse(
            {"choices": [{"delta": {"content": "The folder holds "}}]},
            {"choices": [{"delta": {"content": last["content"]}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            "[DONE]",
        )
    if not body.get("tools"):
        return _sse({"choices": [{"delta": {"content": "No tools here."}, "finish_reason": "stop"}]},
                    "[DONE]")
    name = body["tools"][0]["function"]["name"]
    return _sse(
        {"choices": [{"delta": {"content": "Let me look."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_abc", "type": "function",
             "function": {"name": name, "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"pa'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "/sd"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    )


def _claude_reply(body: dict) -> bytes:
    last = body["messages"][-1]
    blocks = last["content"] if isinstance(last["content"], list) else []
    result = next((b for b in blocks if b.get("type") == "tool_result"), None)
    if result:
        return _sse(
            {"type": "message_start", "message": {"id": "m1"}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "Found: " + result["content"]}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_stop"},
        )
    if not body.get("tools"):
        return _sse(
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "No tools here."}},
            {"type": "message_stop"},
        )
    name = body["tools"][0]["name"]
    return _sse(
        {"type": "message_start", "message": {"id": "m0"}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_01", "name": name, "input": {}}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"path"'}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": ': "/sd"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        REQUESTS.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        data = _openai_reply(body) if self.path.endswith("/chat/completions") else _claude_reply(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"

# Ollama is pointed at a port nothing listens on, so "local is down" is real.
config.update_config({
    "openai_base_url": BASE + "/v1",
    "anthropic_base_url": BASE,
    "ollama_url": "http://127.0.0.1:9",
})

# One fake tool, so the loop has something to call.
CALLS: list[tuple] = []
armory.all_tools = lambda: [{
    "name": "list_directory", "connection_id": "files",
    "description": "List a folder", "inputSchema": {
        "type": "object", "properties": {"path": {"type": "string"}}},
}]
armory.call_tool = lambda conn, tool, args: (
    CALLS.append((conn, tool, args)) or
    {"ok": True, "result": {"content": [{"type": "text", "text": "notes.txt, music/"}]}}
)


def drain(events):
    out = list(events)
    return out, "".join(e["text"] for e in out if e["type"] == "token")


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def check_openai() -> None:
    section("OpenAI: tool calls streamed in fragments")
    openai_client.set_key("sk-test-not-real")
    defs, _ = toolchat.catalogue()
    REQUESTS.clear()
    events, text = drain(openai_client.chat_stream_full(
        "gpt-test", [{"role": "user", "content": "what is in /sd?"}], tools=defs))
    message = events[-1]["message"]
    call = (message.get("tool_calls") or [{}])[0]
    check("text before the call is streamed", text == "Let me look.", repr(text))
    check("the call id survives", call.get("id") == "call_abc")
    check("arguments split across chunks are joined and parsed",
          call.get("function", {}).get("arguments") == {"path": "/sd"},
          repr(call.get("function", {}).get("arguments")))
    check("the tools went out in the request", bool(REQUESTS and REQUESTS[0]["body"].get("tools")))

    section("OpenAI: a full tool round through the loop")
    REQUESTS.clear(); CALLS.clear()
    events, text = drain(toolchat.run(
        [{"role": "system", "content": "be brief"}, {"role": "user", "content": "what is in /sd?"}],
        model="gpt-test", provider=llm.OPENAI))
    check("the tool really ran, with the model's arguments",
          CALLS == [("files", "list_directory", {"path": "/sd"})], repr(CALLS))
    check("the answer is built from the tool output", "notes.txt, music/" in text, repr(text))
    second = REQUESTS[-1]["body"]["messages"] if len(REQUESTS) == 2 else []
    assistant = next((m for m in second if m.get("tool_calls")), {})
    tool_msg = next((m for m in second if m.get("role") == "tool"), {})
    check("two requests: the call, then the answer", len(REQUESTS) == 2, str(len(REQUESTS)))
    check("the assistant turn is replayed with string arguments",
          isinstance((assistant.get("tool_calls") or [{}])[0].get("function", {}).get("arguments"), str))
    check("the tool result names the call it answers",
          tool_msg.get("tool_call_id") == "call_abc", repr(tool_msg))
    check("no unknown keys reach OpenAI on the tool message",
          set(tool_msg) == {"role", "tool_call_id", "content"}, repr(sorted(tool_msg)))
    check("the key is sent only as a header",
          all("sk-test-not-real" not in json.dumps(r["body"]) for r in REQUESTS))


def check_claude() -> None:
    section("Claude: tool_use blocks with streamed input")
    anthropic_client.set_key("sk-ant-test-not-real")
    defs, _ = toolchat.catalogue()
    REQUESTS.clear()
    events, text = drain(anthropic_client.chat_stream_full(
        "claude-test", [{"role": "user", "content": "what is in /sd?"}], tools=defs))
    message = events[-1]["message"]
    call = (message.get("tool_calls") or [{}])[0]
    sent_tool = (REQUESTS[0]["body"].get("tools") or [{}])[0] if REQUESTS else {}
    check("text before the call is streamed", text == "Checking.", repr(text))
    check("the tool_use id survives", call.get("id") == "toolu_01")
    check("partial JSON input is joined and parsed",
          call.get("function", {}).get("arguments") == {"path": "/sd"})
    check("definitions are translated to input_schema",
          sent_tool.get("input_schema", {}).get("type") == "object" and "parameters" not in sent_tool,
          repr(sorted(sent_tool)))

    section("Claude: a full tool round through the loop")
    REQUESTS.clear(); CALLS.clear()
    events, text = drain(toolchat.run(
        [{"role": "system", "content": "be brief"}, {"role": "user", "content": "what is in /sd?"}],
        model="claude-test", provider=llm.CLAUDE))
    check("the tool really ran", CALLS == [("files", "list_directory", {"path": "/sd"})], repr(CALLS))
    check("the answer is built from the tool output", "notes.txt, music/" in text, repr(text))
    turns = REQUESTS[-1]["body"]["messages"] if len(REQUESTS) == 2 else []
    roles = [t["role"] for t in turns]
    check("roles alternate user / assistant / user", roles == ["user", "assistant", "user"], repr(roles))
    uses = [b for b in (turns[1]["content"] if len(turns) > 1 else []) if b.get("type") == "tool_use"]
    results = [b for b in (turns[2]["content"] if len(turns) > 2 else []) if b.get("type") == "tool_result"]
    check("the assistant turn carries the tool_use block with a dict input",
          bool(uses) and uses[0].get("input") == {"path": "/sd"})
    check("the result is a tool_result naming the same id",
          bool(results) and results[0].get("tool_use_id") == "toolu_01")
    check("the system prompt is top-level, not a turn",
          REQUESTS[-1]["body"].get("system", "").startswith("be brief"))

    section("Claude: translation edge cases")
    _, turns = anthropic_client._to_anthropic([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "x", "arguments": "{}"}},
            {"id": "b", "function": {"name": "y", "arguments": '{"k": 1}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
        {"role": "tool", "tool_call_id": "b", "content": "ERROR: the tool did not run"},
    ])
    last = turns[-1]["content"] if turns else []
    check("two results in a row merge into one user turn",
          len(turns) == 3 and [b.get("tool_use_id") for b in last] == ["a", "b"], repr(turns)[:120])
    check("an empty assistant text adds no empty text block",
          all(b.get("type") != "text" for b in turns[1]["content"]))
    check("string arguments are decoded into input", turns[1]["content"][1]["input"] == {"k": 1})
    check("a failed tool is flagged is_error", last[1].get("is_error") is True and "is_error" not in last[0])


def check_switch() -> None:
    section("The cloud tools switch")
    config.update_config({"cloud_tools_enabled": False})
    REQUESTS.clear()
    defs, _ = toolchat.catalogue()
    list(llm.chat_stream_full([{"role": "user", "content": "hi"}], model="gpt-test",
                              tools=defs, which=llm.OPENAI))
    check("off: no tools are sent to OpenAI", REQUESTS and "tools" not in REQUESTS[-1]["body"])
    check("off: supports_tools says so", not llm.supports_tools(llm.CLAUDE))
    check("Ollama is unaffected by the switch", llm.supports_tools(llm.OLLAMA))
    config.update_config({"cloud_tools_enabled": True})
    check("on: supports_tools covers the paid brains",
          llm.supports_tools(llm.OPENAI) and llm.supports_tools(llm.CLAUDE))


def check_routing() -> None:
    section("Routing: manual, local, cloud, automatic")
    config.update_config({"llm_mode": "nonsense"})
    check("an unknown mode falls back to manual", config.get_config()["llm_mode"] == "manual")

    # Nothing authorised yet: keys exist, but nothing is enabled or acknowledged.
    config.update_config({"openai_enabled": False, "openai_cost_ack": False,
                          "claude_enabled": False, "claude_cost_ack": False,
                          "llm_provider": "ollama"})
    check("a stored key alone does not authorise billing", not llm.authorised(llm.OPENAI))

    config.update_config({"llm_mode": "auto"})
    r = llm.route(force=True)
    check("auto, Ollama down, nothing authorised: stays on Ollama",
          r["provider"] == llm.OLLAMA and r["reason"] == "local_down_no_cloud", repr(r))

    config.update_config({"llm_mode": "cloud"})
    r = llm.route(force=True)
    check("cloud with nothing authorised refuses to bill",
          r["provider"] == llm.OLLAMA and r["reason"] == "no_cloud", repr(r))

    config.update_config({"claude_enabled": True, "claude_cost_ack": True})
    r = llm.route(force=True)
    check("cloud picks the authorised provider", r["provider"] == llm.CLAUDE, repr(r))

    config.update_config({"openai_enabled": True, "openai_cost_ack": True, "llm_provider": "openai"})
    r = llm.route(force=True)
    check("cloud prefers the one picked in settings", r["provider"] == llm.OPENAI, repr(r))

    config.update_config({"llm_mode": "auto"})
    r = llm.route(force=True)
    check("auto, Ollama down, cloud authorised: falls to cloud",
          r["provider"] == llm.OPENAI and r["reason"] == "local_down", repr(r))
    check("health reports the mode and the reason",
          llm.health(force=True).get("route_reason") == "local_down")

    # Bring a local "Ollama" up: the stub has pulled exactly one model.
    pulled = config.get_config()["chat_model"]

    class Tags(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = json.dumps({"models": [{"name": pulled}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    local = ThreadingHTTPServer(("127.0.0.1", 0), Tags)
    threading.Thread(target=local.serve_forever, daemon=True).start()
    config.update_config({"ollama_url": f"http://127.0.0.1:{local.server_port}"})
    r = llm.route(force=True)
    check("auto, Ollama up with the model: local wins",
          r["provider"] == llm.OLLAMA and r["reason"] == "local_ready", repr(r))
    config.update_config({"chat_model": "not-pulled:1b"})
    r = llm.route(force=True)
    check("auto, Ollama up but model missing: counts as down", r["provider"] == llm.OPENAI, repr(r))

    config.update_config({"llm_mode": "local"})
    check("local is always Ollama", llm.route(force=True)["provider"] == llm.OLLAMA)
    config.update_config({"llm_mode": "manual", "llm_provider": "claude"})
    check("manual is exactly the pick", llm.route(force=True)["provider"] == llm.CLAUDE)
    local.shutdown()


def main() -> int:
    check_openai()
    check_claude()
    check_switch()
    check_routing()
    print(f"\n{GREEN}{passed} passed{RESET}, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

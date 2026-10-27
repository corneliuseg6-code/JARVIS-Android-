#!/usr/bin/env python3
"""End-to-end test of MCP tool calling in chat.

    python3 tools/test_toolcall.py

What is real here and what is not
---------------------------------
**Real:** the MCP server (``tools/fs_mcp_server.py``, over stdio), the Armory
handshake, ``tools/list``, ``tools/call``, the tool catalogue handed to the
model, the parsing of its tool calls, the execution, and the result fed back.

**Stubbed:** the language model. There is no Ollama in a test environment, so
a tiny HTTP server stands in for ``/api/chat``. It behaves like Ollama with
tools: given a question and a tool list it answers with a ``tool_calls``
message; given a ``tool`` message it answers in words — and it *reads* the
tool output, so a test that claims a directory listing came back only passes
if the listing actually arrived.

That split is the point: everything this project owns is exercised for real,
and the one part it cannot run locally is the only part faked.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import armory, config, llm, toolchat  # noqa: E402

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
# a stand-in for Ollama
# --------------------------------------------------------------------------

class StubOllama(BaseHTTPRequestHandler):
    """Answers /api/tags and /api/chat the way Ollama does, with tools."""

    seen_tools: list = []

    def log_message(self, *_args):  # quiet
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": "llama3.2:3b"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length) or "{}")
        StubOllama.seen_tools = req.get("tools") or []
        messages = req.get("messages") or []
        last = messages[-1] if messages else {}

        # A tool result came back: answer in words, quoting what arrived, so
        # the assertion below can only pass on a real listing.
        if last.get("role") == "tool":
            self._json({"message": {
                "role": "assistant",
                "content": "The tool returned:\n" + (last.get("content") or ""),
            }})
            return

        # Otherwise: pick the tool whose name the question asks for.
        text = (last.get("content") or "").lower()
        names = [t["function"]["name"] for t in StubOllama.seen_tools]
        wanted = next((n for n in names if n.split("__")[-1] in text), None)
        if wanted is None:
            self._json({"message": {"role": "assistant", "content": "No tool for that."}})
            return
        args = {"path": "."} if "list_directory" in wanted else {
            "path": "notes/new.txt", "content": "hello",
        }
        self._json({"message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": wanted, "arguments": args}}],
        }})


def start_stub() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), StubOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# --------------------------------------------------------------------------

def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="jarvis-files-"))
    (workspace / "notes").mkdir()
    (workspace / "readme.md").write_text("# hello\n", "utf-8")
    (workspace / "notes" / "todo.txt").write_text("buy milk\n", "utf-8")

    original_cfg = dict(config.get_config())
    stub, stub_url = start_stub()
    conn_id = ""

    try:
        config.update_config({
            "llm_provider": "ollama", "chat_model": "llama3.2:3b",
            "ollama_url": stub_url, "mcp_allow_writes": False,
        })

        section("The MCP server connects (real stdio handshake)")
        conn = armory.create_connection({
            "name": "JARVIS Files",
            "transport": "stdio",
            "command": f"{sys.executable} {ROOT / 'tools' / 'fs_mcp_server.py'} {workspace}",
            "autoconnect": False,
        })
        conn_id = conn["id"]
        state = armory.connect(conn_id)
        check("connected", state["state"] == "connected", state.get("error", ""))
        tool_names = [t["name"] for t in state["tools"]]
        check("tools/list answered", "list_directory" in tool_names, ", ".join(tool_names))

        section("The catalogue the model is handed")
        definitions, lookup = toolchat.catalogue()
        check("tools reach the catalogue", len(definitions) == 5, f"{len(definitions)} tools")
        listing = next((n for n, e in lookup.items() if e["tool"] == "list_directory"), "")
        check("list_directory is offered", bool(listing), listing)
        check("list_directory is read-only", lookup[listing]["writes"] is False)
        writer = next((n for n, e in lookup.items() if e["tool"] == "write_file"), "")
        check("write_file is marked as mutating", lookup[writer]["writes"] is True)
        check("names are namespaced per connection", listing.endswith("__list_directory"), listing)
        schema = definitions[0]["function"]["parameters"]
        check("the server's own JSON schema is passed through", isinstance(schema, dict))

        section("JARVIS Files -> list_directory, end to end")
        events = list(toolchat.run(
            [{"role": "user", "content": "please run list_directory on the root"}],
            model="llama3.2:3b",
        ))
        kinds = [e["type"] for e in events]
        check("the model was actually given the tools", bool(StubOllama.seen_tools),
              f"{len(StubOllama.seen_tools)} sent")
        check("a tool call was made", "tool_call" in kinds, " -> ".join(kinds))

        result = next((e for e in events if e["type"] == "tool_result"), None)
        check("the tool really ran", bool(result and result["ran"]),
              (result or {}).get("text", "")[:60])
        text = (result or {}).get("text", "")
        check("the real directory came back", "readme.md" in text and "notes/" in text,
              text.splitlines()[0] if text else "")
        check("entry count is right", "2 entries" in text, text.splitlines()[0] if text else "")

        answer = next((e for e in events if e["type"] == "answer"), None)
        check("the model answered using the tool output",
              bool(answer) and "readme.md" in answer.get("text", ""),
              (answer or {}).get("text", "")[:60].replace("\n", " "))

        section("A write waits for the user")
        before = sorted(p.name for p in (workspace / "notes").iterdir())
        events = list(toolchat.run(
            [{"role": "user", "content": "please use write_file to add a note"}],
            model="llama3.2:3b",
        ))
        result = next((e for e in events if e["type"] == "tool_result"), None)
        check("the write did NOT run", bool(result) and result["ran"] is False,
              (result or {}).get("text", "")[:70])
        check("an approval was raised", any(e["type"] == "tool_approval" for e in events))
        after = sorted(p.name for p in (workspace / "notes").iterdir())
        check("nothing was written to disk", before == after, f"{before} -> {after}")
        check("the model is told it did not happen",
              "REFUSED" in (result or {}).get("text", ""))

        section("…and runs once approved, for that call only")
        pend = toolchat.pending()
        check("the pending call is listed", len(pend) == 1, f"{len(pend)} pending")
        toolchat.approve(pend[0]["id"])
        events = list(toolchat.run(
            [{"role": "user", "content": "please use write_file to add a note"}],
            model="llama3.2:3b",
        ))
        result = next((e for e in events if e["type"] == "tool_result"), None)
        check("the write ran after approval", bool(result) and result["ran"] is True,
              (result or {}).get("text", "")[:70])
        check("the file is really on disk", (workspace / "notes" / "new.txt").exists())
        check("its contents are right",
              (workspace / "notes" / "new.txt").read_text() == "hello")

        # the approval is spent
        (workspace / "notes" / "new.txt").unlink()
        events = list(toolchat.run(
            [{"role": "user", "content": "please use write_file to add a note"}],
            model="llama3.2:3b",
        ))
        result = next((e for e in events if e["type"] == "tool_result"), None)
        check("the approval cannot be reused", bool(result) and result["ran"] is False,
              (result or {}).get("text", "")[:50])
        check("and nothing was written the second time",
              not (workspace / "notes" / "new.txt").exists())

        section("The server's root is a boundary")
        entry = lookup[next(n for n, e in lookup.items() if e["tool"] == "read_file")]
        outcome = toolchat.execute(entry, {"path": "../../../../etc/passwd"})
        check("a path outside the root is refused",
              not outcome["ran"] or "outside" in outcome["text"].lower(),
              outcome["text"][:60])

        section("A failure is reported as a failure")
        entry = lookup[next(n for n, e in lookup.items() if e["tool"] == "read_file")]
        outcome = toolchat.execute(entry, {"path": "does-not-exist.txt"})
        check("a missing file is not reported as success",
              not outcome["ran"] or outcome["text"].startswith("ERROR"),
              outcome["text"][:60])

        armory.disconnect(conn_id)
        outcome = toolchat.execute(
            {"connection_id": conn_id, "tool": "list_directory", "writes": False}, {"path": "."},
        )
        check("a disconnected server cannot be 'used'",
              outcome["ran"] is False and "not connected" in outcome["text"],
              outcome["text"][:60])

    finally:
        stub.shutdown()
        if conn_id:
            try:
                armory.delete_connection(conn_id)
            except Exception:  # noqa: BLE001
                pass
        config.update_config({
            "llm_provider": original_cfg.get("llm_provider", "ollama"),
            "ollama_url": original_cfg.get("ollama_url", "http://127.0.0.1:11434"),
            "chat_model": original_cfg.get("chat_model", "llama3.2:3b"),
            "mcp_allow_writes": False,
        })
        shutil.rmtree(workspace, ignore_errors=True)

    print(f"\n{GREEN}{passed} passed{RESET}, "
          + (f"{RED}{failed} failed{RESET}" if failed else "0 failed"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

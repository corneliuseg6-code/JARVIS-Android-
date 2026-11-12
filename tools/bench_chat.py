#!/usr/bin/env python3
"""Measure JARVIS's own latency, separately from the model's.

Run with: python3 tools/bench_chat.py

There is no Ollama on every machine, and even where there is, its generation
speed swamps everything else and moves between runs. So this stands a stub in
its place that emits tokens on a fixed schedule — a known prompt-eval delay,
then one token every few milliseconds. Whatever time appears *beyond* that
schedule is JARVIS's own, and that is the number these optimisations change.

What it reports:

    ttft    request sent -> first token visible in the browser's stream
    total   request sent -> stream closed
    stall   ttft minus the stub's own first-token time: pure harness overhead

It measures the two paths separately, because they were not the same code:
plain chat, and chat with an MCP server connected (the tool loop).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The stub's schedule. PROMPT_MS stands in for prompt evaluation, then one
#: token every TOKEN_MS. A real 3B model on a phone is slower than this; the
#: point is that it is *constant*, so the harness's share is visible.
PROMPT_MS = 300
TOKEN_MS = 8
TOKENS = 60

ANSWER = ("Mars is the fourth planet from the Sun and the second smallest in "
          "the solar system, with a thin atmosphere of carbon dioxide and two "
          "small moons named Phobos and Deimos orbiting close to its surface. ")
WORDS = (ANSWER * 3).split()[:TOKENS]


class StubOllama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    #: Set to a tool name to make the first round ask for a tool call.
    tool_round = {"pending": False}

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/tags"):
            body = json.dumps({"models": [{"name": "stub:latest"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.startswith("/api/chat"):
            self.send_error(404)
            return

        streaming = bool(payload.get("stream"))
        messages = payload.get("messages") or []
        wants_tool = (
            bool(payload.get("tools"))
            and not any(m.get("role") == "tool" for m in messages)
            and StubOllama.tool_round["pending"]
        )

        time.sleep(PROMPT_MS / 1000)

        if wants_tool:
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "function": {"name": payload["tools"][0]["function"]["name"],
                             "arguments": {}},
            }]}
            if streaming:
                self._ndjson([{"message": message, "done": False},
                              {"message": {"role": "assistant", "content": ""},
                               "done": True, "done_reason": "stop"}])
            else:
                self._json({"message": message, "done": True})
            return

        if streaming:
            chunks = [{"message": {"role": "assistant", "content": w + " "}, "done": False}
                      for w in WORDS]
            chunks.append({"message": {"role": "assistant", "content": ""}, "done": True,
                           "done_reason": "stop"})
            self._ndjson(chunks, gap=TOKEN_MS / 1000)
        else:
            # The non-streamed path still has to take as long to generate.
            time.sleep(TOKEN_MS * TOKENS / 1000)
            self._json({"message": {"role": "assistant", "content": " ".join(WORDS)},
                        "done": True})

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ndjson(self, chunks, gap: float = 0.0):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index, chunk in enumerate(chunks):
            if gap and index:
                time.sleep(gap)
            line = (json.dumps(chunk) + "\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def time_one_turn(base: str, message: str) -> dict:
    """Send one chat turn and time the SSE frames as a browser would see them."""
    body = json.dumps({"message": message, "language": "en"}).encode()
    request = urllib.request.Request(
        base + "/api/chat", data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.time()
    first_token = None
    tokens = 0
    tool_events = 0
    opener = urllib.request.build_opener()
    event = ""
    with opener.open(request, timeout=120) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
                if event in ("tool_call", "tool_result"):
                    tool_events += 1
                continue
            if not line.startswith("data:"):
                continue
            try:
                frame = json.loads(line[5:].strip() or "{}")
            except json.JSONDecodeError:
                continue
            if event == "token" and isinstance(frame, dict) and frame.get("text"):
                tokens += 1
                if first_token is None:
                    first_token = time.time() - started
    total = time.time() - started
    return {"ttft": first_token, "total": total, "tokens": tokens,
            "tool_events": tool_events}


def main() -> int:
    stub = ThreadingHTTPServer(("127.0.0.1", 0), StubOllama)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_url = f"http://127.0.0.1:{stub.server_address[1]}"

    home = Path(os.environ.get("BENCH_HOME") or (ROOT / ".bench-home"))
    if home.exists():
        import shutil
        shutil.rmtree(home)
    home.mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({
        "ollama_url": stub_url, "chat_model": "stub:latest",
        "llm_provider": "ollama", "conversation_language": "en",
    }))

    port = free_port()
    env = {**os.environ, "JARVIS_HOME": str(home), "JARVIS_PORT": str(port),
           "PYTHONUNBUFFERED": "1"}
    server = subprocess.Popen(
        [sys.executable, "-m", "server.main"], cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"

    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2).read()
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.25)
        else:
            print("the server did not start:")
            print(server.stdout.read()[-2000:] if server.stdout else "")
            return 1

        floor = PROMPT_MS / 1000
        print(f"stub schedule: {PROMPT_MS}ms to first token, "
              f"{TOKEN_MS}ms per token, {TOKENS} tokens")
        print(f"so the floor is ttft {floor:.3f}s, "
              f"total {floor + TOKEN_MS * TOKENS / 1000:.3f}s\n")

        rows = []
        for label, runs in (("plain chat", 3),):
            for run in range(runs):
                result = time_one_turn(base, "tell me about mars")
                rows.append((f"{label} #{run + 1}", result))

        # Now with an MCP server connected, which is what takes the tool path.
        connect = json.dumps({
            "name": "Bench Files", "transport": "stdio",
            "command": f"{sys.executable} {ROOT / 'tools' / 'fs_mcp_server.py'} {ROOT}",
            "autoconnect": False,
        }).encode()
        created = json.loads(urllib.request.urlopen(urllib.request.Request(
            base + "/api/armory", data=connect,
            headers={"Content-Type": "application/json"}), timeout=30).read())
        urllib.request.urlopen(urllib.request.Request(
            base + f"/api/armory/{created['id']}/connect", data=b"{}",
            headers={"Content-Type": "application/json"}), timeout=60).read()

        for run in range(3):
            result = time_one_turn(base, "tell me about mars")
            rows.append((f"with tools #{run + 1}", result))

        print(f"{'':20} {'ttft':>8} {'total':>8} {'tokens':>7} {'stall':>8}")
        for label, row in rows:
            ttft = row["ttft"]
            stall = (ttft - floor) if ttft is not None else None
            print(f"{label:20} "
                  f"{(f'{ttft:.3f}s' if ttft is not None else '  never'):>8} "
                  f"{row['total']:.3f}s".ljust(40)
                  + f"{row['tokens']:>4}   "
                  + (f"{stall:+.3f}s" if stall is not None else "    n/a"))
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        stub.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

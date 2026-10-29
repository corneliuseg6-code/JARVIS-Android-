#!/usr/bin/env python3
"""Offline stand-ins for every remote service JARVIS V4 talks to.

One HTTP server, speaking the documented wire formats of:

  * OpenAI      POST /v1/chat/completions (SSE, tool_calls deltas, usage chunk)
  * Anthropic   POST /v1/messages (SSE: thinking + signature, tool_use with
                input_json_delta, usage in message_start / message_delta)
  * Gemini      POST /v1beta/models/<m>:streamGenerateContent?alt=sse
                (functionCall parts with a thoughtSignature, usageMetadata)
  * Ollama      /api/tags, /api/chat (NDJSON, tool_calls)
  * an MCP server behind OAuth 2.1, shaped like a hosted image service:
      /.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server,
      /register, /authorize, /token, /mcp (streamable HTTP, Bearer required)

It is scripted, not clever: when a request offers tools and the user asks to
"echo" something, the model calls the echo tool; when the last message is a
tool result, it answers with what the tool said. Every request is kept in
`STATE["requests"]` so tests can check exactly what JARVIS sent.

    python3 tools/mock_cloud.py 8799     # run by hand
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# 1×1 transparent PNG.
PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)).decode()

STATE: dict[str, Any] = {
    "requests": [],          # (service, path, body)
    "fail": {},              # service -> (status, body)
    "codes": {},             # auth code -> {challenge, client_id, redirect}
    "tokens": {},            # access token -> expires_at
    "refresh": set(),
    "clients": {},
    "expire_next": False,    # make the next issued access token already expired
    "mcp_sessions": set(),
}
LOCK = threading.Lock()


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
            parts = m.get("parts") or []
            return " ".join(str(p.get("text", "")) for p in parts)
    return ""


def _echo_arg(text: str) -> str | None:
    found = re.search(r"echo\s+(\w+)", text or "", re.IGNORECASE)
    return found.group(1) if found else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ------------------------------------------------------------ helpers
    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type") or ""
        if "x-www-form-urlencoded" in ctype:
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode()).items()}
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    def _json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, events: list[str]) -> None:
        data = "".join(events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, service: str, body: Any) -> None:
        with LOCK:
            STATE["requests"].append((service, self.path, body, dict(self.headers)))

    def _failing(self, service: str) -> bool:
        fail = STATE["fail"].get(service)
        if fail:
            self._json(fail[0], fail[1])
            return True
        return False

    # ------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/v1/models" and self.headers.get("Authorization"):
            self._json(200, {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-5"}]})
        elif parsed.path == "/v1/models":
            self._json(200, {"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-5-5"}]})
        elif parsed.path == "/v1beta/models":
            self._json(200, {"models": [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
            ]})
        elif parsed.path == "/api/tags":
            self._json(200, {"models": [{"name": "llama3.2:3b"}]})
        elif parsed.path.startswith("/.well-known/oauth-protected-resource"):
            base = f"http://{self.headers['Host']}"
            self._json(200, {"resource": base + "/mcp", "authorization_servers": [base],
                             "scopes_supported": ["generate"]})
        elif parsed.path == "/.well-known/oauth-authorization-server":
            base = f"http://{self.headers['Host']}"
            self._json(200, {
                "issuer": base, "authorization_endpoint": base + "/authorize",
                "token_endpoint": base + "/token", "registration_endpoint": base + "/register",
                "code_challenge_methods_supported": ["S256"],
            })
        elif parsed.path == "/authorize":
            # Stands in for the provider's sign-in page: the user "signs in"
            # and is redirected back with a code.
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            self._record("authorize", q)
            if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
                self._json(400, {"error": "invalid_request"})
                return
            code = secrets.token_urlsafe(8)
            STATE["codes"][code] = {"challenge": q["code_challenge"], "client_id": q["client_id"],
                                    "redirect": q["redirect_uri"], "resource": q.get("resource")}
            location = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json(404, {"error": "not found"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/v1/chat/completions":
            return self._openai()
        if path == "/v1/messages":
            return self._anthropic()
        if path.startswith("/v1beta/models/") and ":streamGenerateContent" in path:
            return self._gemini()
        if path == "/api/chat":
            return self._ollama()
        if path == "/register":
            body = self._body()
            self._record("register", body)
            client_id = "client-" + secrets.token_hex(4)
            STATE["clients"][client_id] = body
            return self._json(201, {"client_id": client_id, **body})
        if path == "/token":
            return self._token()
        if path == "/mcp":
            return self._mcp()
        self._json(404, {"error": "not found"})

    # ------------------------------------------------------------ OpenAI
    def _openai(self):
        body = self._body()
        self._record("openai", body)
        if self._failing("openai"):
            return
        messages = body.get("messages") or []
        events = []
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            for word in f"The tool said: {last.get('content')}".split(" "):
                events.append("data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n")
            events.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n")
        elif body.get("tools") and _echo_arg(_last_user(messages)):
            name = body["tools"][0]["function"]["name"]
            args = json.dumps({"text": _echo_arg(_last_user(messages))})
            events.append("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_abc", "type": "function",
                 "function": {"name": name, "arguments": args[:6]}}]}}]}) + "\n\n")
            events.append("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": args[6:]}}]}}]}) + "\n\n")
            events.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}) + "\n\n")
        else:
            for word in "Hello from OpenAI".split(" "):
                events.append("data: " + json.dumps({"choices": [{"delta": {"content": word + " "}}]}) + "\n\n")
            events.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n")
        events.append("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 1000,
                                                                      "completion_tokens": 200}}) + "\n\n")
        events.append("data: [DONE]\n\n")
        self._sse(events)

    # ------------------------------------------------------------ Anthropic
    def _anthropic(self):
        body = self._body()
        self._record("anthropic", body)
        if self._failing("anthropic"):
            return
        turns = body.get("messages") or []
        last = turns[-1] if turns else {}
        content = last.get("content")
        tool_result = None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_result = block
        ev = []

        def e(obj):
            ev.append(f"event: {obj['type']}\ndata: {json.dumps(obj)}\n\n")

        e({"type": "message_start", "message": {"usage": {"input_tokens": 2000, "output_tokens": 1}}})
        e({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
        e({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": ""}})
        e({"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG123"}})
        e({"type": "content_block_stop", "index": 0})
        if tool_result is not None:
            text = f"The tool said: {tool_result.get('content')}"
            e({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}})
            for word in text.split(" "):
                e({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": word + " "}})
            e({"type": "content_block_stop", "index": 1})
            stop = "end_turn"
        elif body.get("tools") and _echo_arg(_last_user(turns)):
            name = body["tools"][0]["name"]
            args = json.dumps({"text": _echo_arg(_last_user(turns))})
            e({"type": "content_block_start", "index": 1,
               "content_block": {"type": "tool_use", "id": "toolu_1", "name": name, "input": {}}})
            e({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": args[:5]}})
            e({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": args[5:]}})
            e({"type": "content_block_stop", "index": 1})
            stop = "tool_use"
        else:
            e({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}})
            for word in "Hello from Claude".split(" "):
                e({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": word + " "}})
            e({"type": "content_block_stop", "index": 1})
            stop = "end_turn"
        e({"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 300}})
        e({"type": "message_stop"})
        self._sse(ev)

    # ------------------------------------------------------------ Gemini
    def _gemini(self):
        body = self._body()
        self._record("gemini", body)
        if self._failing("gemini"):
            return
        contents = body.get("contents") or []
        last = contents[-1] if contents else {}
        response = None
        for part in last.get("parts") or []:
            if "functionResponse" in part:
                response = part["functionResponse"]
        chunks = []
        usage = {"promptTokenCount": 500, "candidatesTokenCount": 100, "thoughtsTokenCount": 50}
        if response is not None:
            chunks.append({"candidates": [{"content": {"role": "model", "parts": [
                {"text": f"The tool said: {response['response'].get('result')}"}]},
                "finishReason": "STOP"}], "usageMetadata": usage})
        elif body.get("tools") and _echo_arg(_last_user(contents)):
            name = body["tools"][0]["functionDeclarations"][0]["name"]
            chunks.append({"candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": name, "args": {"text": _echo_arg(_last_user(contents))}},
                 "thoughtSignature": "GEMSIG"}]}, "finishReason": "STOP"}], "usageMetadata": usage})
        else:
            chunks.append({"candidates": [{"content": {"role": "model", "parts": [{"text": "Hello "}]}}]})
            chunks.append({"candidates": [{"content": {"role": "model", "parts": [{"text": "from Gemini"}]},
                                           "finishReason": "STOP"}], "usageMetadata": usage})
        self._sse(["data: " + json.dumps(c) + "\r\n\r\n" for c in chunks])

    # ------------------------------------------------------------ Ollama
    def _ollama(self):
        body = self._body()
        self._record("ollama", body)
        if self._failing("ollama"):
            return
        messages = body.get("messages") or []
        last = messages[-1] if messages else {}
        lines = []
        if body.get("tools") and STATE.get("ollama_no_tools"):
            return self._json(400, {"error": "registry.ollama.ai/library/tiny does not support tools"})
        if last.get("role") == "tool":
            lines.append({"message": {"role": "assistant", "content": f"The tool said: {last.get('content')}"}, "done": False})
        elif body.get("tools") and _echo_arg(_last_user(messages)):
            name = body["tools"][0]["function"]["name"]
            lines.append({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": name, "arguments": {"text": _echo_arg(_last_user(messages))}}}]}, "done": False})
        else:
            lines.append({"message": {"role": "assistant", "content": "Hello from Ollama"}, "done": False})
        lines.append({"message": {"role": "assistant", "content": ""}, "done": True})
        data = "".join(json.dumps(x) + "\n" for x in lines).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ OAuth
    def _token(self):
        form = self._body()
        self._record("token", form)
        if form.get("grant_type") == "authorization_code":
            entry = STATE["codes"].pop(form.get("code", ""), None)
            if not entry:
                return self._json(400, {"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if expect != entry["challenge"] or form.get("redirect_uri") != entry["redirect"]:
                return self._json(400, {"error": "invalid_grant", "error_description": "PKCE mismatch"})
        elif form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") not in STATE["refresh"]:
                return self._json(400, {"error": "invalid_grant"})
        else:
            return self._json(400, {"error": "unsupported_grant_type"})
        access = "at-" + secrets.token_hex(6)
        refresh = "rt-" + secrets.token_hex(6)
        lifetime = 3600
        STATE["tokens"][access] = time.time() + (-10 if STATE["expire_next"] else lifetime)
        STATE["expire_next"] = False
        STATE["refresh"].add(refresh)
        self._json(200, {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                         "expires_in": lifetime, "scope": "generate"})

    def _mcp(self):
        auth = self.headers.get("Authorization") or ""
        token = auth.removeprefix("Bearer ").strip()
        base = f"http://{self.headers['Host']}"
        body = self._body()
        if token not in STATE["tokens"] or STATE["tokens"][token] < time.time():
            self._record("mcp_denied", body)
            return self._json(401, {"error": "invalid_token"}, headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'})
        self._record("mcp", body)
        method = body.get("method")
        if "id" not in body:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mock-art", "version": "1"}}
            sid = "s-" + secrets.token_hex(4)
            data = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            payload = f"event: message\ndata: {data}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Mcp-Session-Id", sid)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if method == "tools/list":
            result = {"tools": [
                # Claims to be read-only, as a sloppy server might. A billed
                # connection must hold it for approval anyway.
                {"name": "generate_image", "description": "Generate an image; spends credits.",
                 "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}},
                                 "required": ["prompt"]},
                 "annotations": {"readOnlyHint": True}},
                {"name": "list_models", "description": "List models.",
                 "inputSchema": {"type": "object", "properties": {}}},
            ]}
        elif method == "tools/call":
            name = (body.get("params") or {}).get("name")
            if name == "generate_image":
                result = {"content": [{"type": "text", "text": "Generated 1 image (4 credits)."},
                                      {"type": "image", "data": PNG, "mimeType": "image/png"}]}
            else:
                result = {"content": [{"type": "text", "text": "flux, sdxl"}]}
        else:
            return self._json(200, {"jsonrpc": "2.0", "id": body["id"],
                                    "error": {"code": -32601, "message": "unknown"}})
        self._json(200, {"jsonrpc": "2.0", "id": body["id"], "result": result})


def start(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


if __name__ == "__main__":
    srv, bound = start(int(sys.argv[1]) if len(sys.argv) > 1 else 8799)
    print(f"mock cloud on 127.0.0.1:{bound}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()

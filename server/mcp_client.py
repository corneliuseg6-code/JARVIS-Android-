"""Model Context Protocol client.

Two transports are implemented:

  stdio  - spawn a local MCP server process and speak newline-delimited
           JSON-RPC 2.0 over its stdin/stdout (the transport every
           `npx -y @modelcontextprotocol/server-*` package uses).
  http   - POST JSON-RPC to a streamable-HTTP MCP endpoint and read either a
           JSON body or an SSE stream back.

The client is deliberately small: initialize -> tools/list -> tools/call is
all the Tool Armory needs, and anything bigger would be untestable here.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

#: The streamable-HTTP transport and OAuth authorization arrived in
#: 2025-03-26; a server may answer with an older version and we follow it.
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "jarvis-android", "version": "2.0.0"}


class McpError(RuntimeError):
    pass


class _BaseTransport:
    def request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        raise NotImplementedError

    def notify(self, method: str, params: dict[str, Any] | None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class StdioTransport(_BaseTransport):
    def __init__(self, command: list[str], env: dict[str, str] | None = None, cwd: str | None = None):
        if not command:
            raise McpError("empty command")
        full_env = dict(os.environ)
        full_env.update(env or {})
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - command comes from the user's own config
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                cwd=cwd or None,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise McpError(f"command not found: {command[0]}") from exc
        except OSError as exc:
            raise McpError(str(exc)) from exc

        self._next_id = 1
        self._id_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._stderr_tail: list[str] = []
        self._alive = True

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._errreader = threading.Thread(target=self._read_stderr, daemon=True)
        self._errreader.start()

    # -- plumbing ---------------------------------------------------------
    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = msg.get("id")
            if msg_id is None:
                continue  # server-initiated notification: ignored for now
            with self._pending_lock:
                waiter = self._pending.pop(msg_id, None)
            if waiter is not None:
                waiter.put(msg)
        self._alive = False
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for waiter in waiters:
            waiter.put({"error": {"message": "MCP server closed the connection"}})

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr_tail.append(line.rstrip())
            del self._stderr_tail[:-20]

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-5:])

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc.poll() is not None:
            raise McpError(f"MCP server exited ({self._proc.returncode}). {self.stderr_tail()}".strip())
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpError(f"MCP server pipe closed. {self.stderr_tail()}".strip()) from exc

    # -- API --------------------------------------------------------------
    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        with self._id_lock:
            msg_id = self._next_id
            self._next_id += 1
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[msg_id] = waiter
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
        try:
            msg = waiter.get(timeout=timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(msg_id, None)
            raise McpError(f"timeout waiting for {method}") from exc
        if "error" in msg:
            err = msg["error"]
            raise McpError(str(err.get("message") or err))
        return msg.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> None:
        self._alive = False
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass


class HttpTransport(_BaseTransport):
    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 auth: Callable[[bool], str | None] | None = None):
        self._url = url
        self._headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self._headers.update(headers or {})
        self._session_id: str | None = None
        self._protocol: str | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        # auth(force_refresh) -> bearer token. Called again with True after a
        # 401, so an expired OAuth access token is refreshed once and retried
        # instead of failing the call.
        self._auth = auth

    def _post(self, payload: dict[str, Any], timeout: float, *, retried: bool = False) -> Any:
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        if self._auth is not None:
            token = self._auth(False)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                session = resp.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session
                ctype = (resp.headers.get("Content-Type") or "").lower()
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and self._auth is not None and not retried:
                if self._auth(True):
                    return self._post(payload, timeout, retried=True)
                raise McpError("HTTP 401: sign-in expired; sign in again") from exc
            raise McpError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
        except urllib.error.URLError as exc:
            raise McpError(str(exc.reason)) from exc

        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    try:
                        msg = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == payload.get("id"):
                        return msg
            raise McpError("no JSON-RPC response in SSE stream")
        if not body.strip():
            return None
        return json.loads(body)

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
        msg = self._post({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}, timeout)
        if msg is None:
            raise McpError(f"empty response for {method}")
        if "error" in msg:
            err = msg["error"]
            raise McpError(str(err.get("message") or err))
        return msg.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, 15.0)

    def close(self) -> None:
        self._session_id = None


class McpSession:
    """One live MCP connection: handshake + tool cache."""

    def __init__(self, transport: _BaseTransport):
        self.transport = transport
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []

    def initialize(self, timeout: float = 30.0) -> dict[str, Any]:
        result = self.transport.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
                "clientInfo": CLIENT_INFO,
            },
            timeout=timeout,
        ) or {}
        self.server_info = result.get("serverInfo", {}) or {}
        if isinstance(self.transport, HttpTransport) and result.get("protocolVersion"):
            # Streamable HTTP wants the negotiated version on every later request.
            self.transport._protocol = str(result["protocolVersion"])
        self.capabilities = result.get("capabilities", {}) or {}
        try:
            self.transport.notify("notifications/initialized", {})
        except McpError:
            pass  # some servers do not accept the notification; not fatal
        return result

    def list_tools(self, timeout: float = 30.0) -> list[dict[str, Any]]:
        if "tools" not in self.capabilities:
            self.tools = []
            return self.tools
        result = self.transport.request("tools/list", {}, timeout=timeout) or {}
        self.tools = [
            {
                "name": t.get("name", ""),
                "description": (t.get("description") or "")[:400],
                "inputSchema": t.get("inputSchema") or {},
                # The spec's behaviour hints (readOnlyHint, destructiveHint …).
                # Kept so the chat loop can tell a tool that reads from one
                # that writes; see toolchat.is_mutating for how far they are
                # trusted, which is not all the way.
                "annotations": t.get("annotations") if isinstance(t.get("annotations"), dict) else {},
            }
            for t in result.get("tools", [])
        ]
        return self.tools

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 120.0) -> Any:
        return self.transport.request(
            "tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout
        )

    def close(self) -> None:
        self.transport.close()

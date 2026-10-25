"""A very small HTTP framework on top of http.server.

No third-party dependency on purpose: on Termux `pip install` is the step most
likely to fail, so JARVIS runs with nothing but CPython.
"""

from __future__ import annotations

import json
import mimetypes
import re
import socketserver
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote, urlparse

MAX_BODY = 12 * 1024 * 1024


class Request:
    def __init__(self, method: str, path: str, query: dict[str, list[str]], headers, body: bytes):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.params: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HttpError(400, "invalid JSON body")
        return data if isinstance(data, dict) else {"value": data}

    def q(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default


class Response:
    def __init__(
        self,
        body: bytes | str | dict | list | None = None,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        headers: dict[str, str] | None = None,
        stream: Iterator[bytes] | None = None,
    ):
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}
        self.stream = stream
        if stream is not None:
            self.body = b""
        elif body is None:
            self.body = b""
        elif isinstance(body, bytes):
            self.body = body
        elif isinstance(body, str):
            self.body = body.encode("utf-8")
        else:
            self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


Handler = Callable[[Request], Response]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        regex = re.compile(
            "^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern.rstrip("/") or "/") + "/?$"
        )
        self._routes.append((method.upper(), regex, handler))

    def route(self, method: str, pattern: str):
        def decorator(fn: Handler) -> Handler:
            self.add(method, pattern, fn)
            return fn

        return decorator

    def get(self, pattern: str):
        return self.route("GET", pattern)

    def post(self, pattern: str):
        return self.route("POST", pattern)

    def put(self, pattern: str):
        return self.route("PUT", pattern)

    def delete(self, pattern: str):
        return self.route("DELETE", pattern)

    def match(self, method: str, path: str) -> tuple[Handler, dict[str, str]] | None:
        path = path.rstrip("/") or "/"
        allowed = False
        for route_method, regex, handler in self._routes:
            m = regex.match(path)
            if not m:
                continue
            if route_method == method.upper():
                return handler, {k: unquote(v) for k, v in m.groupdict().items()}
            allowed = True
        if allowed:
            raise HttpError(405, "method not allowed")
        return None


class StaticFiles:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def serve(self, rel_path: str) -> Response:
        rel = rel_path.lstrip("/") or "index.html"
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root)):
            raise HttpError(403, "forbidden")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            raise HttpError(404, "not found")
        ctype, _ = mimetypes.guess_type(str(target))
        if target.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        elif target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        headers = {"Cache-Control": "no-cache"}
        if target.suffix == ".js" and target.name == "sw.js":
            headers["Service-Worker-Allowed"] = "/"
        return Response(
            target.read_bytes(),
            content_type=ctype or "application/octet-stream",
            headers=headers,
        )


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "JARVIS/2.0"
    protocol_version = "HTTP/1.1"

    router: Router
    static: StaticFiles
    verbose: bool = False

    # -- logging ----------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        if self.verbose:
            sys.stderr.write("[http] %s %s\n" % (self.address_string(), fmt % args))

    # -- helpers ----------------------------------------------------------
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        if length > MAX_BODY:
            raise HttpError(413, "payload too large")
        return self.rfile.read(length)

    def _send(self, resp: Response) -> None:
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        # Local-only app: permit the PWA origin and nothing exotic.
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in resp.headers.items():
            self.send_header(key, value)

        if resp.stream is not None:
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for chunk in resp.stream:
                    if not chunk:
                        continue
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True
            return

        self.send_header("Content-Length", str(len(resp.body)))
        self.end_headers()
        if resp.body:
            try:
                self.wfile.write(resp.body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_body() if method in ("POST", "PUT", "DELETE") else b""
            req = Request(method, path, parse_qs(parsed.query), self.headers, body)
            matched = self.router.match(method, path)
            if matched is not None:
                handler, params = matched
                req.params = params
                resp = handler(req)
            elif method == "GET":
                resp = self.static.serve(path)
            else:
                raise HttpError(404, "not found")
        except HttpError as exc:
            resp = Response({"error": exc.message}, status=exc.status)
        except Exception as exc:  # noqa: BLE001 - never kill the server on a bad request
            traceback.print_exc()
            resp = Response({"error": f"{type(exc).__name__}: {exc}"}, status=500)
        self._send(resp)

    # -- verbs ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(Response(b"", status=204, content_type="text/plain"))


class JarvisServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


def make_server(host: str, port: int, router: Router, web_root: Path, verbose: bool = False):
    handler = type(
        "BoundHandler",
        (_RequestHandler,),
        {"router": router, "static": StaticFiles(web_root), "verbose": verbose},
    )
    socketserver.TCPServer.address_family = _address_family(host)
    return JarvisServer((host, port), handler)


def _address_family(host: str):
    import socket

    return socket.AF_INET6 if ":" in host else socket.AF_INET


def sse(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

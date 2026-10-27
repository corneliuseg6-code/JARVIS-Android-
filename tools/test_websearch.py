#!/usr/bin/env python3
"""Checks for the web-research tool: tools/web_mcp_server.py.

Run with: python3 tools/test_websearch.py

The live search engines cannot be reached from every environment, so the
parsing and fetching are exercised against a local fixture server that speaks
the same shapes DuckDuckGo, SearXNG and the Wikipedia API return. The address
guard is tested against the real one, with no patching.

Where the fixture is used, ``_blocked`` is patched to permit loopback — the
fixture can only live on loopback, which the guard exists to refuse. The guard
itself is therefore tested separately, unpatched, so the patch cannot hide a
hole in it.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import web_mcp_server as web  # noqa: E402
from server import toolchat  # noqa: E402

PASS, FAIL = [], []


def ok(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {label}" + (f"  [{detail}]" if detail and not cond else ""))


def raises(label: str, fn, *, contains: str = "") -> None:
    try:
        fn()
    except web.ToolError as exc:
        ok(label, contains.lower() in str(exc).lower(), f"said: {exc}")
    except Exception as exc:  # noqa: BLE001
        ok(label, False, f"wrong error type {type(exc).__name__}: {exc}")
    else:
        ok(label, False, "no error raised")


# --------------------------------------------------------------------------
# the fixture
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html><head><title>Mars Facts</title>
<style>body{color:red}</style>
<script>var tracker = "do not read me";</script>
</head>
<body><nav>Home About Contact</nav>
<h1>Mars</h1>
<p>Mars is the fourth planet from the Sun.</p>
<p>Its thin atmosphere is mostly carbon&nbsp;dioxide.</p>
<p>FILLER. """ + ("Olympus Mons is the tallest volcano in the solar system. " * 14) + """</p>
<footer>copyright nobody</footer>
</body></html>
"""

DDG = """<html><body>
<div class="result results_links">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fmars&amp;rut=x">
    Mars &amp; its moons</a>
  <a class="result__snippet" href="x">The fourth planet, <b>Mars</b>, has two moons.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://example.net/atmosphere">Atmosphere of Mars</a>
  <a class="result__snippet" href="y">Mostly carbon dioxide.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="javascript:void(0)">Not a real link</a>
</div>
</body></html>
"""

SEARX = {"results": [
    {"title": "Mars", "url": "https://example.org/mars", "content": "Fourth planet."},
    {"title": "Phobos", "url": "https://example.org/phobos", "content": "A moon."},
    {"title": "no url here", "content": "dropped"},
]}

WIKI = {"query": {"pages": {
    "42": {"index": 2, "title": "Phobos (moon)", "fullurl": "https://en.wikipedia.org/wiki/Phobos",
           "extract": "Phobos is the innermost moon of Mars."},
    "7": {"index": 1, "title": "Mars", "fullurl": "https://en.wikipedia.org/wiki/Mars",
          "extract": "Mars is the fourth planet from the Sun."},
}}}


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list[str] = []

    def log_message(self, *_args):  # quiet
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        Fixture.hits.append(self.path)
        if path == "/page":
            self._send(PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/json":
            self._send(json.dumps({"planet": "Mars"}).encode(), "application/json")
        elif path == "/ddg":
            self._send(DDG.encode(), "text/html")
        elif path == "/search":   # SearXNG's own route, appended to the base URL
            self._send(json.dumps(SEARX).encode(), "application/json")
        elif path == "/wiki":
            self._send(json.dumps(WIKI).encode(), "application/json")
        elif path == "/redirect":
            self._send(b"", "text/plain", 302, {"Location": "/page"})
        elif path == "/offsite":
            self._send(b"", "text/plain", 302, {"Location": "http://10.0.0.1/secret"})
        elif path == "/big":
            self._send(b"x" * (web.MAX_BYTES + 10), "text/plain")
        elif path == "/bin":
            self._send(b"BINARYDATA", "application/octet-stream")
        elif path == "/gone":
            self._send(b"nothing here", "text/plain", 404)
        else:
            self._send(b"?", "text/plain", 404)


@contextlib.contextmanager
def allow_loopback():
    """Let the fixture be reached. The real guard is tested unpatched."""
    original = web._blocked
    web._blocked = lambda ip: "" if ip.is_loopback else original(ip)
    try:
        yield
    finally:
        web._blocked = original


# --------------------------------------------------------------------------

def test_guard() -> None:
    print("\nthe address guard (unpatched)")
    for addr, why in [
        ("http://127.0.0.1:8787/api/config", "loopback"),
        ("http://localhost:8787/api/config", "loopback"),
        ("http://[::1]:8787/", "loopback"),
        ("http://10.0.0.1/admin", "private"),
        ("http://192.168.1.1/", "private"),
        ("http://172.16.4.4/", "private"),
        ("http://169.254.169.254/latest/meta-data/", "link-local"),
        ("http://100.70.0.1/", "carrier-grade"),
        ("http://0.0.0.0/", "unspecified"),
    ]:
        raises(f"refuses {addr}", lambda a=addr: web.check_public(a), contains=why)

    raises("refuses file://", lambda: web.check_public("file:///etc/passwd"), contains="http")
    raises("refuses ftp://", lambda: web.check_public("ftp://example.org/x"), contains="http")
    raises("refuses a URL with no host", lambda: web.check_public("http:///x"), contains="host")

    # Every range the guard names must be caught by _blocked itself, so a new
    # range cannot be added to the message without being added to the check.
    for literal in ("127.0.0.1", "10.1.2.3", "169.254.1.1", "224.0.0.1", "100.64.0.1"):
        ip = ipaddress.ip_address(literal)
        ok(f"_blocked flags {literal}", bool(web._blocked(ip)))
    ok("_blocked allows a public address", web._blocked(ipaddress.ip_address("93.184.216.34")) == "")


def test_fetch(base: str) -> None:
    print("\nreading a page")
    with allow_loopback():
        text = web.run_tool("fetch_url", {"url": base + "/page"})
        ok("the source URL is reported", f"Source: {base}/page" in text)
        ok("the title is reported", "Title: Mars Facts" in text)
        ok("the prose is extracted", "fourth planet from the Sun" in text)
        ok("entities are decoded", "carbon dioxide" in text)
        ok("script contents are dropped", "do not read me" not in text)
        ok("style contents are dropped", "color:red" not in text)
        ok("nav is dropped", "About Contact" not in text)
        ok("footer is dropped", "copyright nobody" not in text)

        clipped = web.run_tool("fetch_url", {"url": base + "/page", "max_chars": 500})
        ok("max_chars is honoured", "[cut after 500 characters" in clipped, clipped[-80:])
        ok("max_chars has a floor so a model cannot ask for nothing",
           "Mars" in web.run_tool("fetch_url", {"url": base + "/page", "max_chars": 1}))

        raw = web.run_tool("fetch_url", {"url": base + "/json"})
        ok("JSON is returned as-is", '"planet": "Mars"' in raw)

        followed = web.run_tool("fetch_url", {"url": base + "/redirect"})
        ok("a redirect is followed", "fourth planet" in followed)
        ok("the final URL is reported, not the one asked for",
           f"Source: {base}/page" in followed)

        raises("a 404 is reported honestly",
               lambda: web.run_tool("fetch_url", {"url": base + "/gone"}), contains="404")
        raises("an oversized page is refused",
               lambda: web.run_tool("fetch_url", {"url": base + "/big"}), contains="larger")
        raises("url is required",
               lambda: web.run_tool("fetch_url", {}), contains="required")
        raises("an unknown tool is refused",
               lambda: web.run_tool("teleport", {}), contains="unknown tool")

    print("\nredirects are re-checked")
    # /offsite redirects to a private address. Loopback is permitted so the
    # first hop connects; the second hop must still be refused.
    with allow_loopback():
        raises("a redirect into a private network is refused",
               lambda: web.run_tool("fetch_url", {"url": base + "/offsite"}),
               contains="private")


def test_search(base: str) -> None:
    print("\nsearch result parsing")
    web.DDG_ENDPOINT = base + "/ddg"
    web.WIKI_ENDPOINT = base + "/wiki"
    with allow_loopback():
        out = web.run_tool("web_search", {"query": "mars moons", "limit": 5})
        ok("results are numbered", "1. Mars & its moons" in out)
        ok("the DuckDuckGo redirect wrapper is unwrapped",
           "https://example.org/mars" in out and "uddg" not in out)
        ok("the snippet comes through", "has two moons" in out)
        ok("the second result is parsed", "https://example.net/atmosphere" in out)
        ok("a javascript: link is dropped", "javascript:" not in out)
        ok("the backend is named", "DuckDuckGo" in out)
        ok("the model is told to cite the URL", "Cite the URL" in out)

        few = web.run_tool("web_search", {"query": "mars", "limit": 1})
        ok("limit is honoured", "1." in few and "2." not in few)
        raises("an empty query is refused",
               lambda: web.run_tool("web_search", {"query": "  "}), contains="required")

        wiki = web.run_tool("wikipedia", {"query": "mars"})
        ok("Wikipedia results are ordered by rank", wiki.index("Mars\n") < wiki.index("Phobos"))
        ok("the article URL is given", "en.wikipedia.org/wiki/Mars" in wiki)
        ok("the extract is included", "innermost moon" in wiki)

        web.SEARXNG = base
        try:
            searx = web.run_tool("web_search", {"query": "mars", "limit": 5})
            ok("SearXNG is used when configured", "SearXNG" in searx)
            ok("SearXNG results are parsed", "https://example.org/phobos" in searx)
            ok("a SearXNG result with no URL is dropped", "no url here" not in searx)
        finally:
            web.SEARXNG = ""


def test_download(base: str, tmp: Path) -> None:
    print("\ndownloading")
    web.DOWNLOAD_ROOT = tmp
    with allow_loopback():
        out = web.run_tool("download_file", {"url": base + "/bin", "filename": "data.bin"})
        saved = tmp / "data.bin"
        ok("the file really is on disk", saved.exists() and saved.read_bytes() == b"BINARYDATA",
           out)
        ok("the report names the path", str(saved) in out)

        web.run_tool("download_file", {"url": base + "/bin", "filename": "../../escape.sh"})
        ok("a traversing filename cannot escape the folder",
           not (tmp.parent / "escape.sh").exists() and (tmp / "escape.sh").exists())

        web.run_tool("download_file", {"url": base + "/bin", "filename": "a b;rm -rf.txt"})
        ok("shell characters are stripped from the name",
           (tmp / "a_b_rm_-rf.txt").exists(), str(list(tmp.iterdir())))


def test_classification() -> None:
    print("\nwhich of these tools need approval")
    by_name = {tool["name"]: tool for tool in web.TOOLS}
    for name in ("web_search", "wikipedia", "fetch_url", "download_file"):
        ok(f"{name} is declared", name in by_name)

    def writes(name: str) -> bool:
        return toolchat.is_mutating(name, by_name[name].get("annotations"))

    ok("web_search is read-only", not writes("web_search"))
    ok("wikipedia is read-only", not writes("wikipedia"))
    ok("fetch_url is read-only", not writes("fetch_url"))
    ok("download_file needs approval", writes("download_file"))

    ok("search declares readOnlyHint",
       by_name["web_search"]["annotations"].get("readOnlyHint") is True)
    ok("download declares it is not read-only",
       by_name["download_file"]["annotations"].get("readOnlyHint") is False)

    # The hints are a tie-breaker for names the verb list cannot place, never
    # a way past the gate.
    ok("a lying readOnlyHint cannot make delete_everything read-only",
       toolchat.is_mutating("delete_everything", {"readOnlyHint": True}))
    ok("a lying readOnlyHint cannot make write_file read-only",
       toolchat.is_mutating("write_file", {"readOnlyHint": True}))
    ok("a lying readOnlyHint cannot make run_shell read-only",
       toolchat.is_mutating("run_shell", {"readOnlyHint": True}))
    ok("destructiveHint makes an unclear name mutating",
       toolchat.is_mutating("mangle_thing", {"destructiveHint": True}))
    ok("an unplaceable name with no hints still needs approval",
       toolchat.is_mutating("mangle_thing", None))
    ok("list_directory stays read-only", not toolchat.is_mutating("list_directory", None))
    ok("read_file stays read-only", not toolchat.is_mutating("read_file", {}))


def test_guidance() -> None:
    print("\nthe model is told to cite sources")
    def defs(*names):
        return [{"type": "function", "function": {"name": n}} for n in names]

    base = [{"role": "system", "content": "You are JARVIS."},
            {"role": "user", "content": "hi"}]

    with_web = toolchat._with_guidance(list(base), defs("web__web_search", "web__fetch_url"))
    ok("the guidance is appended to the existing system message",
       with_web[0]["content"].startswith("You are JARVIS.")
       and "Sources:" in with_web[0]["content"])
    ok("the rest of the conversation is untouched", with_web[1:] == base[1:])
    ok("the original messages are not mutated", base[0]["content"] == "You are JARVIS.")

    without = toolchat._with_guidance(list(base), defs("files__list_directory"))
    ok("no web tools means no web guidance", without[0]["content"] == "You are JARVIS.")

    headless = toolchat._with_guidance(
        [{"role": "user", "content": "hi"}], defs("web__web_search"))
    ok("a system message is added when there is none",
       headless[0]["role"] == "system" and "Sources:" in headless[0]["content"])


def test_error_reporting() -> None:
    print("\na failed call is not reported as a successful one")
    from server import armory

    entry = {"connection_id": "web", "tool": "fetch_url", "writes": False}
    original = armory.call_tool
    try:
        armory.call_tool = lambda *_a, **_k: {"ok": True, "result": {
            "content": [{"type": "text", "text": "refusing to fetch 10.0.0.1"}],
            "isError": True,
        }}
        out = toolchat.execute(entry, {"url": "http://10.0.0.1/"})
        ok("a server-reported error does not count as having run", out["ran"] is False)
        ok("the reason reaches the transcript", "refusing to fetch" in out["text"])
        ok("the error is marked as an error", out["text"].startswith("ERROR:"))

        armory.call_tool = lambda *_a, **_k: {"ok": True, "result": {
            "content": [{"type": "text", "text": "Source: https://example.org/"}]}}
        good = toolchat.execute(entry, {"url": "https://example.org/"})
        ok("a real result still counts as having run", good["ran"] is True)
        ok("the page text comes through", "example.org" in good["text"])
    finally:
        armory.call_tool = original


def test_presets() -> None:
    print("\nthe bundled-server presets")
    from server import armory
    presets = {p["id"]: p for p in armory.bundled_presets()}
    ok("the web server is offered", "web" in presets)
    ok("the files server is offered", "files" in presets)
    web_preset = presets.get("web", {})
    ok("the command points at the real script",
       str(ROOT / "tools" / "web_mcp_server.py") in web_preset.get("command", ""),
       web_preset.get("command", ""))
    ok("the preset is stdio", web_preset.get("transport") == "stdio")
    ok("the command starts with an interpreter that exists",
       Path(web_preset.get("command", " ").split()[0]).exists())
    ok("each preset says what it does", all(p.get("summary") for p in presets.values()))


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    tmp = ROOT / ".websearch-test"
    for stale in (tmp.glob("*") if tmp.exists() else []):
        stale.unlink()
    tmp.mkdir(exist_ok=True)

    try:
        test_guard()
        test_fetch(base)
        test_search(base)
        test_download(base, tmp)
        test_classification()
        test_guidance()
        test_error_reporting()
        test_presets()
    finally:
        server.shutdown()
        for leftover in tmp.glob("*"):
            leftover.unlink()
        tmp.rmdir()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  failed: " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

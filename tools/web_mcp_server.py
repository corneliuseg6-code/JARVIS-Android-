#!/usr/bin/env python3
"""A web-research MCP server — "JARVIS Web" — over stdio.

Free and keyless by default: DuckDuckGo's HTML endpoint for search and
Wikipedia's public API for encyclopaedic lookups. Point ``JARVIS_SEARXNG_URL``
at a SearXNG instance and that is used for search instead.

Register it in the Armory as:

    name:      JARVIS Web
    transport: stdio
    command:   python3 <project>/tools/web_mcp_server.py

Tools
-----
``web_search``   query the web, returns title + URL + snippet per result
``wikipedia``    article extracts with their URLs
``fetch_url``    read one public page as text
``download_file`` save a file (mutating — JARVIS holds it for approval)

Why the address checks matter
-----------------------------
This runs on the user's phone, beside JARVIS's own API on 127.0.0.1 and
whatever else is on their Wi-Fi. A tool that fetches a URL the model chose is
a confused deputy waiting to happen: "read http://127.0.0.1:8787/api/config"
would hand the model the user's own settings, and a LAN address would reach
their router. So every hostname is resolved and **every** address it resolves
to is checked against the private, loopback, link-local and reserved ranges —
before the connection, and again after each redirect, because a public host
is free to redirect to 127.0.0.1.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
USER_AGENT = "JARVIS-Android/3 (+research tool)"

#: Hard ceilings. A phone has neither the memory nor the patience for more.
MAX_BYTES = 2_000_000
MAX_CHARS = 20_000
TIMEOUT = 25.0
MAX_REDIRECTS = 5

#: Endpoints, as module constants so the test suite can point them at a local
#: fixture and exercise the parsing for real.
DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
WIKI_ENDPOINT = "https://en.wikipedia.org/w/api.php"

SEARXNG = (os.environ.get("JARVIS_SEARXNG_URL") or "").strip().rstrip("/")
DOWNLOAD_ROOT = Path(
    os.environ.get("JARVIS_WEB_DOWNLOADS") or (Path.home() / "jarvis-downloads")
).expanduser()


class ToolError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# address safety
# --------------------------------------------------------------------------

def _blocked(ip: ipaddress._BaseAddress) -> str:
    # Narrowest first: is_private is true for link-local and unspecified
    # addresses too, and a vague reason makes a refusal harder to act on.
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (cloud metadata lives here)"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "the unspecified address"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "a private network"
    # 100.64.0.0/10, carrier-grade NAT — the phone's own mobile network.
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "carrier-grade NAT"
    return ""


def check_public(url: str) -> str:
    """Return the URL if it is safe to fetch, else raise with the reason."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError(f"only http and https are allowed, not {parts.scheme or 'that'!r}")
    host = parts.hostname
    if not host:
        raise ToolError("that URL has no host")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ToolError(f"cannot resolve {host}: {exc}") from exc
    if not infos:
        raise ToolError(f"cannot resolve {host}")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        why = _blocked(ip)
        if why:
            raise ToolError(
                f"refusing to fetch {host}: it resolves to {ip}, which is {why}. "
                "This tool only reaches the public internet."
            )
    return url


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by hand so each hop can be re-checked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _open(url: str, *, timeout: float = TIMEOUT):
    opener = urllib.request.build_opener(_NoRedirect)
    hops = 0
    while True:
        check_public(url)
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,*;q=0.5",
        })
        try:
            return opener.open(request, timeout=timeout), url
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and hops < MAX_REDIRECTS:
                location = exc.headers.get("Location") or ""
                exc.close()
                if not location:
                    raise ToolError(f"HTTP {exc.code} with no destination") from exc
                url = urllib.parse.urljoin(url, location)
                hops += 1
                continue
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            raise ToolError(f"HTTP {exc.code} from {url}: {body}".strip()) from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"could not reach {url}: {exc.reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ToolError(f"{url} timed out after {int(timeout)}s") from exc


def fetch_bytes(url: str, *, timeout: float = TIMEOUT) -> tuple[bytes, str, str]:
    """(body, final URL, content-type) with a hard size ceiling."""
    response, final = _open(url, timeout=timeout)
    with response:
        ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ToolError(f"{final} is larger than {MAX_BYTES // 1000}kB; refusing to read it all")
    return data, final, ctype


# --------------------------------------------------------------------------
# HTML to text
# --------------------------------------------------------------------------

class _Reader(HTMLParser):
    """Pull the readable text out of a page, dropping the furniture."""

    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form", "aside"}
    BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            text = data.strip()
            if text:
                self.parts.append(text + " ")


def html_to_text(raw: str) -> tuple[str, str]:
    reader = _Reader()
    try:
        reader.feed(raw)
    except Exception:  # noqa: BLE001 - a broken page still yields what parsed
        pass
    text = "".join(reader.parts)
    # Non-breaking and other exotic spaces read as spaces, not as mojibake.
    text = text.replace("\xa0", " ").replace("​", "").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip(), reader.title.strip()


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

#: DuckDuckGo's HTML endpoint puts the link and its snippet in sibling
#: anchors. They are matched separately and paired by position rather than in
#: one expression: an optional group after a lazy `.*?` happily matches
#: nothing, which silently drops every snippet.
_DDG_LINK = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*?href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S | re.I,
)
_DDG_SNIPPET = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>',
    re.S | re.I,
)
_TAGS = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return html.unescape(_TAGS.sub("", fragment or "")).strip()


def _ddg_href(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; recover the real address."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlsplit(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def search_duckduckgo(query: str, limit: int) -> list[dict]:
    url = DDG_ENDPOINT + "?" + urllib.parse.urlencode({"q": query})
    data, _final, _ctype = fetch_bytes(url)
    page = data.decode("utf-8", "replace")

    # Each snippet belongs to the last link that started before it.
    snippets = [(m.start(), _clean(m.group("snippet"))) for m in _DDG_SNIPPET.finditer(page)]
    links = list(_DDG_LINK.finditer(page))

    results: list[dict] = []
    for index, match in enumerate(links):
        href = _ddg_href(html.unescape(match.group("href") or ""))
        title = _clean(match.group("title"))
        if not href.startswith(("http://", "https://")) or not title:
            continue
        end = links[index + 1].start() if index + 1 < len(links) else len(page)
        snippet = next(
            (text for pos, text in snippets if match.end() <= pos < end), ""
        )
        results.append({"title": title, "url": href, "snippet": snippet[:400]})
        if len(results) >= limit:
            break
    return results


def search_searxng(query: str, limit: int) -> list[dict]:
    url = SEARXNG + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    data, _final, _ctype = fetch_bytes(url)
    payload = json.loads(data.decode("utf-8", "replace") or "{}")
    results = []
    for item in (payload.get("results") or [])[:limit]:
        if not item.get("url"):
            continue
        results.append({
            "title": str(item.get("title") or item["url"])[:300],
            "url": str(item["url"]),
            "snippet": str(item.get("content") or "")[:400],
        })
    return results


def search_wikipedia(query: str, limit: int) -> list[dict]:
    api = WIKI_ENDPOINT + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "extracts|info",
        "generator": "search", "gsrsearch": query, "gsrlimit": str(limit),
        "exintro": "1", "explaintext": "1", "inprop": "url",
    })
    data, _final, _ctype = fetch_bytes(api)
    payload = json.loads(data.decode("utf-8", "replace") or "{}")
    pages = ((payload.get("query") or {}).get("pages") or {}).values()
    out = []
    for page in sorted(pages, key=lambda p: p.get("index", 999)):
        out.append({
            "title": str(page.get("title") or ""),
            "url": str(page.get("fullurl") or ""),
            "snippet": str(page.get("extract") or "")[:1200],
        })
    return out[:limit]


def _format_results(results: list[dict], query: str, source: str) -> str:
    if not results:
        return f"No results for {query!r} from {source}."
    lines = [f"{len(results)} results for {query!r} (via {source}):", ""]
    for i, item in enumerate(results, 1):
        lines.append(f"{i}. {item['title']}")
        lines.append(f"   {item['url']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
        lines.append("")
    lines.append(
        "Cite the URL above for anything you take from a result. "
        "Snippets are previews — use fetch_url to read a page properly."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the public web and return titles, URLs and snippets. "
            "Use this for anything current, or when you need a source to cite."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "limit": {"type": "integer", "description": "How many results (1-10).", "default": 5},
            },
            "required": ["query"],
        },
        "annotations": {"title": "Web search", "readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "wikipedia",
        "description": "Look a subject up on Wikipedia and return article extracts with their URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
        "annotations": {"title": "Wikipedia", "readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "fetch_url",
        "description": (
            "Read one public web page and return its text. Only reaches the public "
            "internet: private, loopback and local-network addresses are refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "An http(s) URL."},
                "max_chars": {"type": "integer", "default": 8000},
            },
            "required": ["url"],
        },
        "annotations": {"title": "Read a page", "readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "download_file",
        "description": (
            "Save a file from a public URL into the downloads folder. "
            "This writes to disk and needs the user's approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "filename": {"type": "string", "description": "Name to save it as."},
            },
            "required": ["url"],
        },
        "annotations": {"title": "Download a file", "readOnlyHint": False, "destructiveHint": False},
    },
]


def run_tool(name: str, args: dict) -> str:
    if name == "web_search":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        limit = max(1, min(10, int(args.get("limit") or 5)))
        if SEARXNG:
            return _format_results(search_searxng(query, limit), query, f"SearXNG at {SEARXNG}")
        return _format_results(search_duckduckgo(query, limit), query, "DuckDuckGo")

    if name == "wikipedia":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("query is required")
        limit = max(1, min(5, int(args.get("limit") or 3)))
        return _format_results(search_wikipedia(query, limit), query, "Wikipedia")

    if name == "fetch_url":
        url = str(args.get("url") or "").strip()
        if not url:
            raise ToolError("url is required")
        cap = max(500, min(MAX_CHARS, int(args.get("max_chars") or 8000)))
        data, final, ctype = fetch_bytes(url)
        raw = data.decode("utf-8", "replace")
        if "json" in ctype:
            body, title = raw, ""
        elif "html" in ctype or raw.lstrip()[:1] == "<":
            body, title = html_to_text(raw)
        else:
            body, title = raw, ""
        head = f"Source: {final}"
        if title:
            head += f"\nTitle: {title}"
        clipped = body[:cap]
        if len(body) > cap:
            clipped += f"\n\n[cut after {cap} characters of {len(body)}]"
        return f"{head}\n\n{clipped}"

    if name == "download_file":
        url = str(args.get("url") or "").strip()
        if not url:
            raise ToolError("url is required")
        data, final, _ctype = fetch_bytes(url)
        raw_name = str(args.get("filename") or "").strip() or Path(
            urllib.parse.urlsplit(final).path
        ).name or "download"
        # No directories, no traversal: one plain name in the downloads folder.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(raw_name).name)[:80] or "download"
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        target = DOWNLOAD_ROOT / safe
        target.write_bytes(data)
        return f"saved {len(data)} bytes from {final} to {target}"

    raise ToolError(f"unknown tool {name}")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> int:
    backend = f"SearXNG {SEARXNG}" if SEARXNG else "DuckDuckGo + Wikipedia"
    print(f"JARVIS Web ready ({backend}); downloads -> {DOWNLOAD_ROOT}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid = msg.get("method"), msg.get("id")

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "JARVIS Web", "version": "1.0.0"},
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                text = run_tool(params.get("name", ""), params.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"content": [{"type": "text", "text": text}]}})
            except ToolError as exc:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": str(exc)}], "isError": True}})
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"method not found: {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

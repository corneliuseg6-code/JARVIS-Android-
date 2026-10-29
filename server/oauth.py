"""Sign in to a remote MCP server the way the MCP specification says to.

This is the authorization flow from the MCP spec (2025-06-18,
"Authorization"), which is how hosted servers such as OpenArt's
(``https://mcp.openart.ai/mcp``) expect a client to connect — there is no API
key to paste. In order:

1. **Protected resource metadata** (RFC 9728). The server's 401 names it in
   ``WWW-Authenticate: Bearer resource_metadata="…"``, or it sits at
   ``/.well-known/oauth-protected-resource``. It names the authorization server.
2. **Authorization server metadata** (RFC 8414, falling back to OpenID
   discovery): the authorize, token and registration endpoints.
3. **Dynamic client registration** (RFC 7591): JARVIS registers itself as a
   public client with a loopback redirect, once per connection.
4. **Authorization code + PKCE (S256)** with the ``resource`` indicator
   (RFC 8707). The user signs in on the provider's own page, in their own
   browser; JARVIS never sees their password.
5. **Tokens** go to ``secrets.json`` (0600) and are refreshed with the
   refresh token when the access token expires. They are never sent to the
   browser, logged, or put in an error message.

Everything here is standard library. Plain http is refused except on
loopback, which only the offline tests use.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config

CLIENT_NAME = "JARVIS Android"
FLOW_TTL = 600.0
USER_AGENT = "JARVIS-Android-V4"

_LOCK = threading.Lock()
_FLOWS: dict[str, dict[str, Any]] = {}


class OAuthError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# storage — secrets only
# --------------------------------------------------------------------------

def _secret_name(conn_id: str) -> str:
    return f"conn:{conn_id}:oauth"


def _load(conn_id: str) -> dict[str, Any]:
    raw = config.get_secret(_secret_name(conn_id)) or ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(conn_id: str, data: dict[str, Any]) -> None:
    config.set_secret(_secret_name(conn_id), json.dumps(data, separators=(",", ":")) if data else "")


def status(conn_id: str) -> dict[str, Any]:
    """What the interface may know: signed in or not, and until when. No token."""
    data = _load(conn_id)
    tokens = data.get("tokens") or {}
    return {
        "signed_in": bool(tokens.get("access_token")),
        "expires_at": int(tokens.get("expires_at") or 0),
        "refreshable": bool(tokens.get("refresh_token")),
        "issuer": (data.get("server") or {}).get("issuer", ""),
        "scope": tokens.get("scope", ""),
        "pending": any(f["conn_id"] == conn_id for f in _FLOWS.values()),
    }


def sign_out(conn_id: str) -> None:
    _save(conn_id, {})


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return url
    raise OAuthError("insecure_url", f"Refusing a non-HTTPS authorization URL: {url[:120]}")


def _json(url: str, *, data: dict[str, Any] | None = None, form: dict[str, str] | None = None,
          timeout: float = 20.0) -> tuple[int, dict[str, Any], dict[str, str]]:
    _check_url(url)
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(text) if text.strip() else {}), dict(response.headers)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            payload = {"error": "http_error", "error_description": text[:200]}
        return exc.code, payload if isinstance(payload, dict) else {}, dict(exc.headers or {})
    except urllib.error.URLError as exc:
        raise OAuthError("network", str(exc.reason)) from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise OAuthError("network", str(exc)) from exc


def _probe_401(mcp_url: str) -> str:
    """POST an unauthenticated initialize; read resource_metadata from the 401."""
    _check_url(mcp_url)
    body = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "jarvis-android", "version": "4"}}}).encode("utf-8")
    request = urllib.request.Request(mcp_url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15.0):
            return ""
    except urllib.error.HTTPError as exc:
        header = (exc.headers or {}).get("WWW-Authenticate") or ""
        found = re.search(r'resource_metadata="([^"]+)"', header)
        return found.group(1) if found else ""
    except urllib.error.URLError as exc:
        raise OAuthError("network", str(exc.reason)) from exc


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def _well_known(base_url: str, suffix: str) -> list[str]:
    parts = urllib.parse.urlsplit(base_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    urls = []
    if path:
        urls.append(f"{origin}/.well-known/{suffix}{path}")
    urls.append(f"{origin}/.well-known/{suffix}")
    return urls


def discover(mcp_url: str) -> dict[str, Any]:
    """Find the authorization server for an MCP endpoint and its endpoints."""
    resource = mcp_url
    candidates = []
    hinted = _probe_401(mcp_url)
    if hinted:
        candidates.append(hinted)
    candidates += _well_known(mcp_url, "oauth-protected-resource")

    prm: dict[str, Any] = {}
    for url in candidates:
        code, body, _ = _json(url)
        if code == 200 and body.get("authorization_servers"):
            prm = body
            break

    if prm:
        issuer = str(prm["authorization_servers"][0])
        resource = str(prm.get("resource") or mcp_url)
    else:
        # Older servers (spec 2025-03-26) put the metadata on the MCP origin.
        parts = urllib.parse.urlsplit(mcp_url)
        issuer = f"{parts.scheme}://{parts.netloc}"

    metadata: dict[str, Any] = {}
    for url in (_well_known(issuer, "oauth-authorization-server")
                + _well_known(issuer, "openid-configuration")):
        code, body, _ = _json(url)
        if code == 200 and body.get("authorization_endpoint") and body.get("token_endpoint"):
            metadata = body
            break
    if not metadata:
        raise OAuthError("no_metadata",
                         f"No OAuth metadata found for {issuer}. The server may not support sign-in.")
    methods = metadata.get("code_challenge_methods_supported")
    if methods and "S256" not in methods:
        raise OAuthError("no_pkce", "The authorization server does not offer PKCE S256.")
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if metadata.get(key):
            _check_url(str(metadata[key]))
    return {
        "issuer": str(metadata.get("issuer") or issuer),
        "authorization_endpoint": metadata["authorization_endpoint"],
        "token_endpoint": metadata["token_endpoint"],
        "registration_endpoint": metadata.get("registration_endpoint", ""),
        "resource": resource,
        "scopes": prm.get("scopes_supported") or [],
    }


def _register(server: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    if not server.get("registration_endpoint"):
        raise OAuthError(
            "no_registration",
            "The server does not allow automatic client registration, so JARVIS "
            "cannot sign in to it on its own.")
    code, body, _ = _json(server["registration_endpoint"], data={
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    if code not in (200, 201) or not body.get("client_id"):
        raise OAuthError("registration_failed",
                         str(body.get("error_description") or body.get("error") or f"HTTP {code}")[:200])
    return {"client_id": body["client_id"], "client_secret": body.get("client_secret", ""),
            "redirect_uri": redirect_uri}


# --------------------------------------------------------------------------
# the flow
# --------------------------------------------------------------------------

def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _sweep() -> None:
    now = time.time()
    for key, flow in list(_FLOWS.items()):
        if now - flow["created"] > FLOW_TTL:
            _FLOWS.pop(key, None)


def start(conn_id: str, mcp_url: str, redirect_uri: str) -> dict[str, Any]:
    """
    Begin signing in. Returns the URL the user opens in their browser.

    Discovery and registration are reused from an earlier sign-in when the
    redirect address has not changed.
    """
    _check_url(redirect_uri)
    stored = _load(conn_id)
    server = stored.get("server") or {}
    client = stored.get("client") or {}
    if not server or server.get("mcp_url") != mcp_url:
        server = {**discover(mcp_url), "mcp_url": mcp_url}
        client = {}
    if not client or client.get("redirect_uri") != redirect_uri:
        client = _register(server, redirect_uri)
    _save(conn_id, {**stored, "server": server, "client": client})

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": server["resource"],
    }
    if server.get("scopes"):
        params["scope"] = " ".join(server["scopes"])
    joiner = "&" if "?" in server["authorization_endpoint"] else "?"
    url = server["authorization_endpoint"] + joiner + urllib.parse.urlencode(params)
    with _LOCK:
        _sweep()
        _FLOWS[state] = {"conn_id": conn_id, "verifier": verifier, "created": time.time()}
    return {"authorize_url": url, "issuer": server["issuer"]}


def finish(state: str, code: str) -> str:
    """Exchange the code from the redirect. Returns the connection id."""
    with _LOCK:
        _sweep()
        flow = _FLOWS.pop(state or "", None)
    if flow is None:
        raise OAuthError("bad_state", "This sign-in link has expired or was not started here.")
    conn_id = flow["conn_id"]
    stored = _load(conn_id)
    server, client = stored.get("server") or {}, stored.get("client") or {}
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": client.get("redirect_uri", ""),
        "client_id": client.get("client_id", ""),
        "code_verifier": flow["verifier"],
        "resource": server.get("resource", ""),
    }
    if client.get("client_secret"):
        form["client_secret"] = client["client_secret"]
    status_code, body, _ = _json(server.get("token_endpoint", ""), form=form)
    if status_code != 200 or not body.get("access_token"):
        raise OAuthError("token_failed",
                         str(body.get("error_description") or body.get("error") or f"HTTP {status_code}")[:200])
    stored["tokens"] = _tokens(body, previous={})
    _save(conn_id, stored)
    return conn_id


def _tokens(body: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    try:
        lifetime = int(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        lifetime = 0
    return {
        "access_token": body["access_token"],
        # A refresh response may omit the refresh token; keep the old one.
        "refresh_token": body.get("refresh_token") or previous.get("refresh_token", ""),
        "expires_at": int(time.time()) + lifetime if lifetime else 0,
        "scope": body.get("scope") or previous.get("scope", ""),
    }


def _refresh(conn_id: str, stored: dict[str, Any]) -> str | None:
    tokens = stored.get("tokens") or {}
    server, client = stored.get("server") or {}, stored.get("client") or {}
    if not tokens.get("refresh_token") or not server.get("token_endpoint"):
        return None
    form = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client.get("client_id", ""),
        "resource": server.get("resource", ""),
    }
    if client.get("client_secret"):
        form["client_secret"] = client["client_secret"]
    try:
        code, body, _ = _json(server["token_endpoint"], form=form)
    except OAuthError:
        return None
    if code != 200 or not body.get("access_token"):
        # The refresh token was revoked or expired: signed out for real.
        stored["tokens"] = {}
        _save(conn_id, stored)
        return None
    stored["tokens"] = _tokens(body, previous=tokens)
    _save(conn_id, stored)
    return stored["tokens"]["access_token"]


def access_token(conn_id: str, *, force_refresh: bool = False) -> str | None:
    """A usable access token, refreshed first if it has (nearly) expired."""
    with _LOCK:
        stored = _load(conn_id)
        tokens = stored.get("tokens") or {}
        if not tokens.get("access_token"):
            return None
        expires = int(tokens.get("expires_at") or 0)
        if force_refresh or (expires and expires - 60 < time.time()):
            return _refresh(conn_id, stored)
        return tokens["access_token"]

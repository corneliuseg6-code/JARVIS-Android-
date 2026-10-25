"""Tool Armory: persistent MCP connection definitions + live connection state.

Design rules enforced here:
  * Credentials live in secrets.json (0600) and are referenced by name only.
    A connection record that travels to the browser never contains a value.
  * A connection is reported CONNECTED only after initialize() succeeded and
    tools/list came back — i.e. after the server actually answered.
"""

from __future__ import annotations

import shlex
import threading
import time
import uuid
from typing import Any

from . import config, paths, store
from .mcp_client import HttpTransport, McpError, McpSession, StdioTransport

_LOCK = threading.RLock()
_SESSIONS: dict[str, McpSession] = {}
_STATUS: dict[str, dict[str, Any]] = {}

VALID_TRANSPORTS = ("stdio", "http")


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _load() -> list[dict[str, Any]]:
    data = store.read_json(paths.CONNECTIONS_FILE, {"connections": []}) or {}
    conns = data.get("connections")
    return conns if isinstance(conns, list) else []


def _save(connections: list[dict[str, Any]]) -> None:
    store.write_json(paths.CONNECTIONS_FILE, {"version": 1, "connections": connections})


def _secret_key(conn_id: str, name: str) -> str:
    return f"conn:{conn_id}:{name}"


def _sanitise(raw: dict[str, Any], conn_id: str) -> dict[str, Any]:
    transport = raw.get("transport") if raw.get("transport") in VALID_TRANSPORTS else "stdio"
    return {
        "id": conn_id,
        "name": (raw.get("name") or "Unnamed").strip()[:60],
        "transport": transport,
        "command": (raw.get("command") or "").strip(),
        "cwd": (raw.get("cwd") or "").strip(),
        "url": (raw.get("url") or "").strip(),
        # names only - the values are in secrets.json
        "env_keys": [str(k).strip() for k in (raw.get("env_keys") or []) if str(k).strip()],
        "header_keys": {
            str(h).strip(): str(s).strip()
            for h, s in (raw.get("header_keys") or {}).items()
            if str(h).strip() and str(s).strip()
        },
        "env_plain": {
            str(k).strip(): str(v)
            for k, v in (raw.get("env_plain") or {}).items()
            if str(k).strip()
        },
        "autoconnect": bool(raw.get("autoconnect")),
        "enabled": raw.get("enabled", True) is not False,
    }


def _status_for(conn_id: str) -> dict[str, Any]:
    return _STATUS.setdefault(
        conn_id,
        {"state": "disconnected", "error": "", "tools": [], "server": {}, "verified_at": 0},
    )


def public_connection(conn: dict[str, Any]) -> dict[str, Any]:
    """Shape sent to the browser: definitions + status, never secret values."""
    status = _status_for(conn["id"])
    return {
        **{k: v for k, v in conn.items() if k != "env_plain"},
        "env_plain_keys": sorted((conn.get("env_plain") or {}).keys()),
        "secrets": {
            name: config.has_secret(_secret_key(conn["id"], name))
            for name in sorted(set(conn.get("env_keys", [])) | set((conn.get("header_keys") or {}).values()))
        },
        "state": status["state"],
        "error": status["error"],
        "tools": status["tools"],
        "server": status["server"],
        "verified_at": status["verified_at"],
    }


def list_connections() -> list[dict[str, Any]]:
    with _LOCK:
        return [public_connection(c) for c in _load()]


def get_connection(conn_id: str) -> dict[str, Any] | None:
    for conn in _load():
        if conn["id"] == conn_id:
            return conn
    return None


def create_connection(raw: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        conns = _load()
        conn_id = raw.get("id") or uuid.uuid4().hex[:12]
        conn = _sanitise(raw, conn_id)
        conns.append(conn)
        _save(conns)
        _store_inline_secrets(conn_id, raw.get("secret_values") or {})
        return public_connection(conn)


def update_connection(conn_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    with _LOCK:
        conns = _load()
        for idx, conn in enumerate(conns):
            if conn["id"] != conn_id:
                continue
            merged = {**conn, **{k: v for k, v in raw.items() if k != "secret_values"}}
            conns[idx] = _sanitise(merged, conn_id)
            _save(conns)
            _store_inline_secrets(conn_id, raw.get("secret_values") or {})
            return public_connection(conns[idx])
    return None


def delete_connection(conn_id: str) -> bool:
    with _LOCK:
        disconnect(conn_id)
        conns = _load()
        remaining = [c for c in conns if c["id"] != conn_id]
        if len(remaining) == len(conns):
            return False
        _save(remaining)
        config.delete_secrets(f"conn:{conn_id}:")
        _STATUS.pop(conn_id, None)
        return True


def _store_inline_secrets(conn_id: str, secret_values: dict[str, Any]) -> None:
    """Secret values arrive once, are written to the 0600 store, and are never
    echoed back. Sending an empty string clears the stored value."""
    for name, value in secret_values.items():
        name = str(name).strip()
        if not name:
            continue
        config.set_secret(_secret_key(conn_id, name), str(value))


# --------------------------------------------------------------------------
# live connections
# --------------------------------------------------------------------------

def _build_transport(conn: dict[str, Any]):
    if conn["transport"] == "stdio":
        if not conn["command"]:
            raise McpError("no command configured")
        argv = shlex.split(conn["command"])
        env = dict(conn.get("env_plain") or {})
        for name in conn.get("env_keys", []):
            value = config.get_secret(_secret_key(conn["id"], name))
            if not value:
                raise McpError(f"missing credential: {name}")
            env[name] = value
        return StdioTransport(argv, env=env, cwd=conn.get("cwd") or None)

    if not conn["url"]:
        raise McpError("no URL configured")
    headers: dict[str, str] = {}
    for header, secret_name in (conn.get("header_keys") or {}).items():
        value = config.get_secret(_secret_key(conn["id"], secret_name))
        if not value:
            raise McpError(f"missing credential: {secret_name}")
        headers[header] = value if header.lower() != "authorization" or value.lower().startswith(
            ("bearer ", "basic ", "token ")
        ) else f"Bearer {value}"
    return HttpTransport(conn["url"], headers=headers)


def connect(conn_id: str) -> dict[str, Any]:
    conn = get_connection(conn_id)
    if conn is None:
        return {"state": "error", "error": "unknown connection"}
    if not conn.get("enabled", True):
        return {"state": "disconnected", "error": "connection disabled"}

    with _LOCK:
        status = _status_for(conn_id)
        if status["state"] == "connecting":
            return public_connection(conn)
        status.update({"state": "connecting", "error": "", "tools": [], "server": {}})
        _SESSIONS.pop(conn_id, None)

    try:
        session = McpSession(_build_transport(conn))
        session.initialize(timeout=45.0)
        tools = session.list_tools(timeout=30.0)
    except McpError as exc:
        with _LOCK:
            _status_for(conn_id).update({"state": "error", "error": str(exc)[:300], "tools": []})
        return public_connection(conn)
    except Exception as exc:  # noqa: BLE001 - any failure is a failed verification
        with _LOCK:
            _status_for(conn_id).update({"state": "error", "error": f"{type(exc).__name__}: {exc}"[:300]})
        return public_connection(conn)

    with _LOCK:
        _SESSIONS[conn_id] = session
        _status_for(conn_id).update(
            {
                "state": "connected",       # verified: handshake + tools/list both answered
                "error": "",
                "tools": tools,
                "server": session.server_info,
                "verified_at": int(time.time()),
            }
        )
    return public_connection(conn)


def disconnect(conn_id: str) -> dict[str, Any] | None:
    with _LOCK:
        session = _SESSIONS.pop(conn_id, None)
        status = _status_for(conn_id)
        status.update({"state": "disconnected", "error": "", "tools": [], "server": {}, "verified_at": 0})
    if session is not None:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass
    conn = get_connection(conn_id)
    return public_connection(conn) if conn else None


def disconnect_all() -> None:
    for conn_id in list(_SESSIONS.keys()):
        disconnect(conn_id)


def call_tool(conn_id: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        session = _SESSIONS.get(conn_id)
    if session is None:
        return {"ok": False, "error": "not connected"}
    try:
        result = session.call_tool(tool, arguments)
        return {"ok": True, "result": result}
    except McpError as exc:
        return {"ok": False, "error": str(exc)[:300]}


def autoconnect_all() -> None:
    for conn in _load():
        if conn.get("autoconnect") and conn.get("enabled", True):
            threading.Thread(target=connect, args=(conn["id"],), daemon=True).start()


def all_tools() -> list[dict[str, Any]]:
    """Tools exposed by currently connected servers (for the chat tool loop)."""
    out: list[dict[str, Any]] = []
    with _LOCK:
        for conn_id, status in _STATUS.items():
            if status["state"] != "connected":
                continue
            for tool in status["tools"]:
                out.append({**tool, "connection_id": conn_id})
    return out

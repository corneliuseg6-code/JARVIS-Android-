"""What JARVIS knows about itself, from live checks — never from a constant.

Before this, the model answering a chat was told nothing about the system it
runs in. Asked "is Ollama connected?" or "which model are you?", a small
local model guessed — usually "disconnected" and "no model active", often
repeating an old error still in the conversation history.

Every chat turn now carries a short status block built here from real
checks, and naming the brain that is *actually* answering that turn
(fallback included). The block says it is authoritative, so it overrides
anything said earlier in the conversation.

Nothing paid is contacted: Ollama is local and probed for real; a paid
brain reports its configuration gates (switched on, acknowledged, key,
budget), exactly as `llm.usable()` decides them.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import armory, llm, ollama

#: How long one Ollama probe is reused. Short: a status that is stale by
#: minutes would be a guess again, but a turn should not wait on a probe the
#: previous turn made a moment ago.
OLLAMA_TTL = 5.0

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def invalidate() -> None:
    with _LOCK:
        _CACHE.update(at=0.0, value=None)


def ollama_state() -> dict[str, Any]:
    """Ollama's real state: one cheap local probe, reused for a few seconds."""
    now = time.monotonic()
    with _LOCK:
        if _CACHE["value"] is not None and now - _CACHE["at"] < OLLAMA_TTL:
            return dict(_CACHE["value"])
    info = ollama.health(timeout=1.5)
    with _LOCK:
        _CACHE.update(at=time.monotonic(), value=dict(info))
    return info


def snapshot() -> dict[str, Any]:
    """Everything the status block says, as data (also handy for tests)."""
    local = ollama_state()
    brains = {}
    for which in llm.PROVIDERS:
        ok, reason = llm.usable(which)
        brains[which] = {"usable": ok, "reason": reason, "model": llm.active_model(which)}
    servers = []
    for conn in armory.list_connections():
        servers.append({
            "name": conn["name"],
            "state": conn["state"],
            "tools": [t.get("name", "") for t in conn.get("tools") or []],
            "billed": bool(conn.get("billed")),
        })
    return {
        "ollama": {
            "state": local.get("state", "unknown"),
            "url": local.get("url", ""),
            "models": local.get("models", []),
            "error": str(local.get("error") or "")[:120],
        },
        "brains": brains,
        "routing": llm.routing_mode(),
        "mcp": servers,
    }


def describe(provider: str, model: str, *, snap: dict[str, Any] | None = None) -> str:
    """
    The status block for one turn, naming the brain answering it.

    Kept short on purpose: it rides along with every turn, and a phone model
    re-reads all of it each time.
    """
    snap = snap or snapshot()
    o = snap["ollama"]
    lines = [
        "LIVE SYSTEM STATUS — checked just now. This is the truth about your own "
        "system and it overrides anything said earlier in this conversation. "
        "When asked about your status, model, Ollama or tools, answer from it.",
        f"- You are answering as: {llm.LABELS.get(provider, provider)} model {model}"
        + (" (local, free)." if provider == llm.OLLAMA else " (paid API).")
        + f" Routing mode: {snap['routing']}.",
    ]
    if o["state"] == "connected":
        models = ", ".join(o["models"][:8]) or "none"
        lines.append(f"- Ollama: connected at {o['url']}. Installed models: {models}.")
    else:
        lines.append(f"- Ollama: NOT reachable at {o['url']}"
                     + (f" ({o['error']})." if o["error"] else "."))
    paid = []
    for which in (llm.CLAUDE, llm.OPENAI, llm.GEMINI):
        b = snap["brains"][which]
        paid.append(f"{llm.LABELS[which]} {'ready' if b['usable'] else 'not usable (' + b['reason'] + ')'}")
    lines.append("- Paid brains: " + "; ".join(paid) + ".")
    live = [s for s in snap["mcp"] if s["state"] == "connected"]
    if live:
        parts = []
        for s in live:
            names = ", ".join(s["tools"][:6]) + (" …" if len(s["tools"]) > 6 else "")
            parts.append(f"{s['name']} ({len(s['tools'])} tools: {names})")
        lines.append("- MCP servers connected: " + "; ".join(parts) + ".")
    else:
        lines.append("- MCP servers connected: none.")
    others = [s["name"] for s in snap["mcp"] if s["state"] != "connected"]
    if others:
        lines.append("- MCP servers configured but not connected: " + ", ".join(others) + ".")
    return "\n".join(lines)


def with_status(messages: list[dict[str, Any]], provider: str, model: str) -> list[dict[str, Any]]:
    """A copy of `messages` whose system message ends with the live status."""
    block = describe(provider, model)
    out = list(messages)
    for index, message in enumerate(out):
        if message.get("role") == "system":
            patched = dict(message)
            patched["content"] = str(message.get("content") or "").rstrip() + "\n\n" + block
            out[index] = patched
            return out
    return [{"role": "system", "content": block}, *out]

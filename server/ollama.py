"""Minimal Ollama client built on urllib (no third-party dependency).

Keeping this dependency-free matters on Termux, where pip installs that need a
compiler are the usual reason an install fails.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from .config import get_config


class OllamaError(RuntimeError):
    pass


def _base() -> str:
    return (get_config().get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")


def _request(path: str, payload: dict[str, Any] | None = None, *, timeout: float = 15.0):
    url = _base() + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    # A local Ollama must never be reached through a corporate/agent proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def health(timeout: float = 3.0) -> dict[str, Any]:
    try:
        with _request("/api/tags", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        models = [m.get("name", "") for m in body.get("models", [])]
        return {"state": "connected", "models": models, "url": _base()}
    except urllib.error.URLError as exc:
        return {"state": "disconnected", "models": [], "url": _base(), "error": str(exc.reason)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as "error"
        return {"state": "error", "models": [], "url": _base(), "error": str(exc)}


def list_models(timeout: float = 5.0) -> list[str]:
    return health(timeout=timeout).get("models", [])


def has_model(name: str) -> bool:
    if not name:
        return False
    wanted = name.split(":")[0]
    for installed in list_models():
        if installed == name or installed.split(":")[0] == wanted:
            return True
    return False


def chat_stream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    keep_alive: str | None = None,
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """Yield Ollama /api/chat NDJSON chunks as dicts."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
    if keep_alive:
        # Without this Ollama unloads the model after about five minutes idle,
        # so the next message pays a multi-gigabyte reload before its first
        # token. On a phone that is the difference between a pause and a wait.
        payload["keep_alive"] = keep_alive
    try:
        resp = _request("/api/chat", payload, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise OllamaError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(str(exc.reason)) from exc
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def chat_once(model: str, messages: list[dict[str, Any]], *, timeout: float = 300.0) -> str:
    parts: list[str] = []
    for chunk in chat_stream(model, messages, timeout=timeout):
        parts.append(chunk.get("message", {}).get("content", ""))
        if chunk.get("done"):
            break
    return "".join(parts)


def chat_stream_full(
    model: str,
    messages: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    keep_alive: str | None = None,
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """
    Stream a turn *and* assemble the complete message, including tool calls.

    This exists because the two things were previously exclusive: the tool
    loop needed `tool_calls` intact, so it asked for the whole message at once
    and the user saw nothing until generation finished. Measured, that cost
    the entire generation time before the first word appeared — on a phone,
    twenty seconds of blank screen.

    Yields ``{"type": "token", "text": …}`` as content arrives, then exactly
    one ``{"type": "message", "message": {…}}`` with the assembled turn. Tool
    calls are collected from whichever chunks carry them, so the loop gets
    what it needs without giving up the stream.
    """
    content: list[str] = []
    calls: list[dict[str, Any]] = []
    role = "assistant"

    for chunk in chat_stream(model, messages, options=options, tools=tools,
                             keep_alive=keep_alive, timeout=timeout):
        message = chunk.get("message") or {}
        role = message.get("role") or role
        for call in message.get("tool_calls") or []:
            calls.append(call)
        piece = message.get("content") or ""
        if piece:
            content.append(piece)
            # Held back when a tool call has already been seen: that text is
            # the model narrating its own call, and showing it as the answer
            # would read as if it had finished.
            if not calls:
                yield {"type": "token", "text": piece}
        if chunk.get("done"):
            break

    assembled: dict[str, Any] = {"role": role, "content": "".join(content)}
    if calls:
        assembled["tool_calls"] = calls
    yield {"type": "message", "message": assembled}


def chat_full(
    model: str,
    messages: list[dict[str, Any]],
    *,
    options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    keep_alive: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """One non-streamed turn, returning the whole assistant message."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
    if keep_alive:
        payload["keep_alive"] = keep_alive
    try:
        resp = _request("/api/chat", payload, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise OllamaError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise OllamaError(str(exc.reason)) from exc
    with resp:
        body = json.loads(resp.read().decode("utf-8") or "{}")
    message = body.get("message")
    return message if isinstance(message, dict) else {"role": "assistant", "content": ""}

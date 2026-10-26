"""OpenAI client, built on urllib so Termux needs no pip install.

Two rules shape this file:

1. **The key never leaves the backend.** It lives in ~/.jarvis/secrets.json
   (chmod 600), is read only when a request is actually being signed, and is
   never put in a response, a log line or an error message. The browser is
   told whether a key is set — never what it is.

2. **Errors must say what to do about them.** A bare "HTTP 401" is useless on
   a phone. Every failure is mapped to a stable code the interface can
   translate, so the user is told the key is wrong, or the account is out of
   credit, or the model name does not exist.

Chunks are yielded in the same shape the Ollama client uses —
``{"message": {"content": str}, "done": bool}`` — so the router and the API
layer above never have to care which provider answered.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

from . import config

#: Name of the entry in the backend secret store. Referenced by name only.
SECRET_NAME = "openai:api_key"

DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Sent with every request so OpenAI-compatible gateways can identify us.
USER_AGENT = "JARVIS-Android-V2"


class OpenAIError(RuntimeError):
    """An API failure with a stable code for the interface to translate."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# key handling
# --------------------------------------------------------------------------

def has_key() -> bool:
    return config.has_secret(SECRET_NAME)


def set_key(value: str) -> None:
    """Store, or clear when given an empty string."""
    config.set_secret(SECRET_NAME, (value or "").strip())


def _key() -> str:
    key = config.get_secret(SECRET_NAME) or ""
    if not key.strip():
        raise OpenAIError("no_key", "No OpenAI API key is stored.")
    return key.strip()


def _base() -> str:
    return (config.get_config().get("openai_base_url") or DEFAULT_BASE_URL).rstrip("/")


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

def _request(path: str, payload: dict[str, Any] | None = None, *, timeout: float, stream: bool = False):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        _base() + path,
        data=body,
        method="POST" if body is not None else "GET",
        headers={
            "Authorization": f"Bearer {_key()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        # Unlike the local Ollama, this is a real internet call, so the
        # environment's proxy settings are honoured rather than bypassed.
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _from_http(exc) from exc
    except urllib.error.URLError as exc:
        raise OpenAIError("network", str(exc.reason)) from exc
    except TimeoutError as exc:
        raise OpenAIError("timeout", "The request timed out.") from exc


def _from_http(exc: urllib.error.HTTPError) -> OpenAIError:
    """Turn an HTTP failure into something a person can act on."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        error = payload.get("error") or {}
        message = str(error.get("message") or "")[:300]
        kind = str(error.get("code") or error.get("type") or "")
    except Exception:  # noqa: BLE001 - the body is not always JSON
        message = ""
        kind = ""

    if exc.code == 401:
        return OpenAIError("auth", message or "The API key was rejected.")
    if exc.code == 403:
        return OpenAIError("forbidden", message or "This key may not use that model or region.")
    if exc.code == 404:
        return OpenAIError("model_missing", message or "That model name does not exist.")
    if exc.code == 429:
        # Out of credit and "too many requests" are very different problems.
        if "insufficient_quota" in kind or "quota" in message.lower():
            return OpenAIError("quota", message or "The account has no remaining credit.")
        return OpenAIError("rate_limit", message or "Too many requests; slow down.")
    if exc.code >= 500:
        return OpenAIError("server", message or f"OpenAI returned HTTP {exc.code}.")
    return OpenAIError("http_error", message or f"HTTP {exc.code}")


# --------------------------------------------------------------------------
# health and models
# --------------------------------------------------------------------------

#: The interface polls /api/health every twelve seconds. Reaching across the
#: internet that often — on a phone, on mobile data — to colour one chip is
#: wasteful and invites rate limiting, so the answer is held briefly. The
#: fingerprint is a hash of the endpoint and the key, so changing either
#: invalidates the cache immediately without ever retaining key material.
HEALTH_TTL = 60.0
_HEALTH_LOCK = threading.Lock()
_HEALTH_CACHE: dict[str, Any] = {"at": 0.0, "fingerprint": "", "value": None}


def _fingerprint() -> str:
    raw = _base() + "\0" + (config.get_secret(SECRET_NAME) or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def health(timeout: float = 8.0, *, force: bool = False) -> dict[str, Any]:
    """
    Report the real state, never a guess, and never echo the key.

    `force` skips the cache — used when the user is explicitly looking at the
    provider panel, as opposed to a background poll.
    """
    fingerprint = _fingerprint()
    now = time.monotonic()
    with _HEALTH_LOCK:
        cached = _HEALTH_CACHE
        if (
            not force
            and cached["value"] is not None
            and cached["fingerprint"] == fingerprint
            and now - cached["at"] < HEALTH_TTL
        ):
            return dict(cached["value"])

    result = _probe(timeout)
    with _HEALTH_LOCK:
        _HEALTH_CACHE.update(at=time.monotonic(), fingerprint=fingerprint, value=dict(result))
    return result


def _probe(timeout: float) -> dict[str, Any]:
    base = _base()
    if not has_key():
        return {
            "state": "disconnected",
            "models": [],
            "url": base,
            "key_set": False,
            "error": "no_key",
            "detail": "No API key stored.",
        }
    try:
        with _request("/models", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        models = sorted(
            m.get("id", "") for m in body.get("data", []) if isinstance(m, dict) and m.get("id")
        )
        return {"state": "connected", "models": models, "url": base, "key_set": True}
    except OpenAIError as exc:
        # A rejected key is an error; being offline is merely disconnected.
        state = "disconnected" if exc.code in ("network", "timeout") else "error"
        return {
            "state": state,
            "models": [],
            "url": base,
            "key_set": True,
            "error": exc.code,
            "detail": exc.detail,
        }


def list_models(timeout: float = 8.0) -> list[str]:
    return health(timeout=timeout).get("models", [])


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------

def chat_stream(
    model: str,
    messages: list[dict[str, Any]],
    *,
    timeout: float = 300.0,
    temperature: float | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream a completion, yielding chunks in the Ollama shape."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [_to_openai(m) for m in messages],
        "stream": True,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    response = _request("/chat/completions", payload, timeout=timeout, stream=True)
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                yield {"message": {"content": ""}, "done": True}
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or ""
            finished = bool(choices[0].get("finish_reason"))
            if piece:
                yield {"message": {"content": piece}, "done": False}
            if finished:
                yield {"message": {"content": ""}, "done": True}
                return
    # The stream ended without a terminator; treat it as a clean finish.
    yield {"message": {"content": ""}, "done": True}


def chat_once(model: str, messages: list[dict[str, Any]], *, timeout: float = 300.0) -> str:
    parts: list[str] = []
    for chunk in chat_stream(model, messages, timeout=timeout):
        parts.append(chunk.get("message", {}).get("content", ""))
        if chunk.get("done"):
            break
    return "".join(parts)


def _to_openai(message: dict[str, Any]) -> dict[str, Any]:
    """
    Translate a JARVIS message into OpenAI's shape.

    JARVIS carries images the way Ollama wants them — a list of bare base64
    strings on the message. OpenAI wants a content array of parts with data
    URLs, so that conversion happens here rather than in the vision code.
    """
    images = message.get("images") or []
    text = message.get("content") or ""
    if not images:
        return {"role": message.get("role", "user"), "content": text}

    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for raw in images:
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{raw}"},
        })
    return {"role": message.get("role", "user"), "content": parts}

"""Claude via the Anthropic Messages API, on urllib so Termux needs no pip.

This is the sibling of ``openai_client`` and follows the same two rules:

1. **The key never leaves the backend.** It lives in ~/.jarvis/secrets.json
   (chmod 600), is read only when a request is being signed, and is never put
   in a response, a log line or an error message.

2. **Errors must say what to do about them.** Every failure maps to a stable
   code the interface can translate.

The project takes no pip dependencies at all — a Termux install that needs a
compiler is the usual way this app fails to start — so the official
``anthropic`` SDK is not used here. The wire format is the documented one:
``POST /v1/messages`` with ``x-api-key`` and ``anthropic-version``.

Chunks come out in the Ollama shape, ``{"message": {"content": str},
"done": bool}``, so the router above never has to know who answered.
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
SECRET_NAME = "anthropic:api_key"

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2026-10-21"
USER_AGENT = "JARVIS-Android-V2"

#: Claude answers on a phone are short; this is a ceiling, not a target.
MAX_TOKENS = 4096

#: US dollars per million tokens, for the cost notice shown before the
#: provider can be switched on. Kept beside the model list so the two cannot
#: drift apart.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

#: Offered in the interface. The API is the source of truth — /v1/models is
#: what the status panel lists — but this gives the picker sensible contents
#: before a key has been entered.
SUGGESTED_MODELS = tuple(PRICING)

EFFORTS = ("low", "medium", "high")


class AnthropicError(RuntimeError):
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
        raise AnthropicError("no_key", "No Anthropic API key is stored.")
    return key.strip()


def _base() -> str:
    return (config.get_config().get("anthropic_base_url") or DEFAULT_BASE_URL).rstrip("/")


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
            "x-api-key": _key(),
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _from_http(exc) from exc
    except urllib.error.URLError as exc:
        raise AnthropicError("network", str(exc.reason)) from exc
    except TimeoutError as exc:
        raise AnthropicError("timeout", "The request timed out.") from exc


def _from_http(exc: urllib.error.HTTPError) -> AnthropicError:
    """Turn an HTTP failure into something a person can act on."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        error = payload.get("error") or {}
        message = str(error.get("message") or "")[:300]
        kind = str(error.get("type") or "")
    except Exception:  # noqa: BLE001 - the body is not always JSON
        message = ""
        kind = ""

    if exc.code == 401:
        return AnthropicError("auth", message or "The API key was rejected.")
    if exc.code == 403:
        return AnthropicError("forbidden", message or "This key may not use that model.")
    if exc.code == 404:
        return AnthropicError("model_missing", message or "That model name does not exist.")
    if exc.code == 400:
        # Anthropic reports an empty balance as a 400, not a 402 or a 429, so
        # "out of credit" and "malformed request" arrive through one door.
        if "credit" in message.lower() or "balance" in message.lower():
            return AnthropicError("quota", message or "The account has no remaining credit.")
        return AnthropicError("invalid_request", message or "The request was rejected.")
    if exc.code == 413:
        return AnthropicError("too_large", message or "The conversation is too large to send.")
    if exc.code == 429:
        return AnthropicError("rate_limit", message or "Too many requests; slow down.")
    if exc.code == 529 or kind == "overloaded_error":
        return AnthropicError("overloaded", message or "Claude is overloaded; try again shortly.")
    if exc.code >= 500:
        return AnthropicError("server", message or f"Anthropic returned HTTP {exc.code}.")
    return AnthropicError("http_error", message or f"HTTP {exc.code}")


# --------------------------------------------------------------------------
# health and models
# --------------------------------------------------------------------------

#: Same reasoning as the OpenAI client: the status chip polls often, and
#: reaching across the internet each time to colour it is wasteful. The
#: fingerprint hashes endpoint plus key, so changing either invalidates the
#: cache at once without ever retaining key material.
HEALTH_TTL = 60.0
_HEALTH_LOCK = threading.Lock()
_HEALTH_CACHE: dict[str, Any] = {"at": 0.0, "fingerprint": "", "value": None}


def _fingerprint() -> str:
    raw = _base() + "\0" + (config.get_secret(SECRET_NAME) or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def health(timeout: float = 8.0, *, force: bool = False) -> dict[str, Any]:
    """Report the real state, never a guess, and never echo the key."""
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
        with _request("/v1/models?limit=100", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        models = sorted(
            m.get("id", "") for m in body.get("data", []) if isinstance(m, dict) and m.get("id")
        )
        return {"state": "connected", "models": models, "url": base, "key_set": True}
    except AnthropicError as exc:
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
    effort: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream an answer, yielding chunks in the Ollama shape."""
    system, turns = _to_anthropic(messages)
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": turns,
        "stream": True,
    }
    if system:
        payload["system"] = system
    chosen = (effort or config.get_config().get("claude_effort") or "").strip().lower()
    if chosen in EFFORTS:
        # How hard Claude thinks before answering. On a phone this is the
        # difference between a reply and a wait, and it is billed, so it is
        # the user's choice rather than a constant.
        payload["output_config"] = {"effort": chosen}

    response = _request("/v1/messages", payload, timeout=timeout, stream=True)
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            # The stream carries "event:" lines too; the data line alone says
            # everything, because each payload repeats its own type.
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = event.get("type")

            if kind == "content_block_delta":
                delta = event.get("delta") or {}
                # Only visible text. A thinking_delta is reasoning, not answer,
                # and carries no text at all under the default display.
                if delta.get("type") == "text_delta":
                    piece = delta.get("text") or ""
                    if piece:
                        yield {"message": {"content": piece}, "done": False}
            elif kind == "message_stop":
                yield {"message": {"content": ""}, "done": True}
                return
            elif kind == "error":
                error = event.get("error") or {}
                raise AnthropicError(
                    "stream_error",
                    str(error.get("message") or "The stream failed.")[:300],
                )
    # The stream ended without a terminator; treat it as a clean finish.
    yield {"message": {"content": ""}, "done": True}


def chat_once(model: str, messages: list[dict[str, Any]], *, timeout: float = 300.0) -> str:
    parts: list[str] = []
    for chunk in chat_stream(model, messages, timeout=timeout):
        parts.append(chunk.get("message", {}).get("content", ""))
        if chunk.get("done"):
            break
    return "".join(parts)


def _to_anthropic(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """
    Translate JARVIS messages into the Messages API shape.

    Three differences from the OpenAI shape have to be handled here rather
    than above: the system prompt is a top-level field, images are content
    blocks with a base64 source, and two messages from the same role in a row
    are rejected — so they are merged.
    """
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role", "user")
        text = message.get("content") or ""
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role not in ("user", "assistant"):
            role = "user"

        images = message.get("images") or []
        if images:
            blocks: list[dict[str, Any]] = []
            for raw in images:
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": raw},
                })
            if text:
                blocks.append({"type": "text", "text": text})
            content: Any = blocks
        else:
            content = text

        if turns and turns[-1]["role"] == role:
            turns[-1] = _merge_turn(turns[-1], content)
        else:
            turns.append({"role": role, "content": content})

    # A conversation must open with the user. A leading assistant turn can
    # only come from a replayed history that lost its first message.
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return "\n\n".join(system_parts), turns


def _merge_turn(turn: dict[str, Any], content: Any) -> dict[str, Any]:
    existing = turn["content"]
    if isinstance(existing, str) and isinstance(content, str):
        return {"role": turn["role"], "content": (existing + "\n\n" + content).strip()}
    as_blocks = existing if isinstance(existing, list) else [{"type": "text", "text": existing}]
    extra = content if isinstance(content, list) else [{"type": "text", "text": content}]
    return {"role": turn["role"], "content": [*as_blocks, *extra]}


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------

def price_of(model: str) -> dict[str, float] | None:
    """US dollars per million tokens, or None for a model we have no price for."""
    pair = PRICING.get(model)
    if not pair:
        return None
    return {"input": pair[0], "output": pair[1]}


# --------------------------------------------------------------------------
# tools + usage (V4)
# --------------------------------------------------------------------------

def _tools(definitions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """OpenAI-shaped function definitions → Messages API tool definitions."""
    out = []
    for d in definitions or []:
        fn = d.get("function") or {}
        schema = fn.get("parameters") or {"type": "object", "properties": {}}
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        out.append({
            "name": fn.get("name", "tool"),
            "description": str(fn.get("description") or "")[:900],
            "input_schema": schema,
        })
    return out


def _to_anthropic_full(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """
    _to_anthropic plus the tool loop's turns.

    An assistant turn that called tools is replayed from the content blocks
    Claude actually returned — thinking blocks with their signatures
    included, unchanged, which the API requires when a tool result follows.
    Tool results become `tool_result` blocks in a user turn; consecutive ones
    land in the same turn through the merge in _to_anthropic.
    """
    rewritten: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": str(message.get("content") or "") or "(empty)",
            }
            if message.get("is_error"):
                block["is_error"] = True
            rewritten.append({"role": "user", "_blocks": [block]})
        elif role == "assistant" and message.get("tool_calls"):
            native = message.get("_native") or {}
            if native.get("provider") == "claude" and native.get("blocks"):
                blocks = [dict(b) for b in native["blocks"]]
            else:
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for index, call in enumerate(message["tool_calls"]):
                    fn = call.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args or "{}")
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append({"type": "tool_use", "id": str(call.get("id") or f"toolu_{index}"),
                                   "name": fn.get("name", ""), "input": args or {}})
            rewritten.append({"role": "assistant", "_blocks": blocks})
        else:
            rewritten.append(message)

    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for message in rewritten:
        if "_blocks" in message:
            content: Any = message["_blocks"]
            role = message["role"]
            if turns and turns[-1]["role"] == role:
                turns[-1] = _merge_turn(turns[-1], content)
            else:
                turns.append({"role": role, "content": content})
            continue
        role = message.get("role", "user")
        text = message.get("content") or ""
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role not in ("user", "assistant"):
            role = "user"
        images = message.get("images") or []
        content = [
            *({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": raw}}
              for raw in images),
            *([{"type": "text", "text": text}] if text else []),
        ] if images else text
        if not content:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1] = _merge_turn(turns[-1], content)
        else:
            turns.append({"role": role, "content": content})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return "\n\n".join(system_parts), turns


def chat_stream_full(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    timeout: float = 300.0,
    effort: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream a turn; yield tokens, then the assembled message and usage."""
    system, turns = _to_anthropic_full(messages)
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens or MAX_TOKENS),
        "messages": turns,
        "stream": True,
    }
    if system:
        payload["system"] = system
    declared = _tools(tools)
    if declared:
        payload["tools"] = declared
    chosen = (effort or config.get_config().get("claude_effort") or "").strip().lower()
    if chosen in EFFORTS:
        payload["output_config"] = {"effort": chosen}

    blocks: dict[int, dict[str, Any]] = {}
    partial: dict[int, str] = {}
    usage = {"input": 0, "output": 0}
    stop = ""
    seen_tool = False

    response = _request("/v1/messages", payload, timeout=timeout, stream=True)
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "message_start":
                u = (event.get("message") or {}).get("usage") or {}
                # Cache reads and writes are input too, at their own rates; the
                # ledger prices them as plain input, which can only over-state.
                usage["input"] = int(u.get("input_tokens") or 0) + \
                    int(u.get("cache_creation_input_tokens") or 0) + \
                    int(u.get("cache_read_input_tokens") or 0)
                usage["output"] = int(u.get("output_tokens") or 0)
            elif kind == "content_block_start":
                index = int(event.get("index") or 0)
                block = dict(event.get("content_block") or {})
                if block.get("type") == "tool_use":
                    block["input"] = {}
                    partial[index] = ""
                    seen_tool = True
                blocks[index] = block
            elif kind == "content_block_delta":
                index = int(event.get("index") or 0)
                delta = event.get("delta") or {}
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                dtype = delta.get("type")
                if dtype == "text_delta":
                    piece = delta.get("text") or ""
                    block["text"] = (block.get("text") or "") + piece
                    if piece and not seen_tool:
                        yield {"type": "token", "text": piece}
                elif dtype == "thinking_delta":
                    block["thinking"] = (block.get("thinking") or "") + (delta.get("thinking") or "")
                elif dtype == "signature_delta":
                    block["signature"] = (block.get("signature") or "") + (delta.get("signature") or "")
                elif dtype == "input_json_delta":
                    partial[index] = partial.get(index, "") + (delta.get("partial_json") or "")
            elif kind == "content_block_stop":
                index = int(event.get("index") or 0)
                if index in partial:
                    try:
                        blocks[index]["input"] = json.loads(partial.pop(index) or "{}")
                    except json.JSONDecodeError:
                        blocks[index]["input"] = {}
            elif kind == "message_delta":
                stop = (event.get("delta") or {}).get("stop_reason") or stop
                u = event.get("usage") or {}
                if u.get("output_tokens") is not None:
                    usage["output"] = int(u["output_tokens"])
            elif kind == "message_stop":
                break
            elif kind == "error":
                error = event.get("error") or {}
                raise AnthropicError("stream_error",
                                     str(error.get("message") or "The stream failed.")[:300])

    ordered = [blocks[i] for i in sorted(blocks)]
    text = "".join(b.get("text") or "" for b in ordered if b.get("type") == "text")
    if stop == "refusal" and not text:
        raise AnthropicError("refusal", "Claude declined this request.")
    message: dict[str, Any] = {"role": "assistant", "content": text}
    calls = [
        {"id": b.get("id", ""), "type": "function",
         "function": {"name": b.get("name", ""), "arguments": b.get("input") or {}}}
        for b in ordered if b.get("type") == "tool_use"
    ]
    if calls:
        message["tool_calls"] = calls
        message["_native"] = {"provider": "claude", "blocks": ordered}
    yield {"type": "message", "message": message, "usage": usage, "stop": stop}

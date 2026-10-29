"""Gemini via the Gemini API (Google AI Studio), on urllib so Termux needs no pip.

The third sibling of ``openai_client`` and ``anthropic_client``, with the same
rules: the key lives only in ~/.jarvis/secrets.json and is sent only in the
``x-goog-api-key`` header (never in the URL, where it would end up in proxy
logs), and every failure maps to a stable code.

**A Gemini app subscription is not API access.** Google One AI Premium /
Google AI Pro unlock the Gemini *app*; the API used here needs a key created
at https://aistudio.google.com/apikey and is billed (or free-tier limited)
separately, under Google's own terms.

Wire format, the documented REST one:
``POST /v1beta/models/{model}:streamGenerateContent?alt=sse``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from . import config

SECRET_NAME = "gemini:api_key"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
USER_AGENT = "JARVIS-Android-V4"

SUGGESTED_MODELS = ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite")


class GeminiError(RuntimeError):
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
    config.set_secret(SECRET_NAME, (value or "").strip())


def _key() -> str:
    key = (config.get_secret(SECRET_NAME) or "").strip()
    if not key:
        raise GeminiError("no_key", "No Gemini API key is stored.")
    return key


def _base() -> str:
    return (config.get_config().get("gemini_base_url") or DEFAULT_BASE_URL).rstrip("/")


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
            "x-goog-api-key": _key(),
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
        raise GeminiError("network", str(exc.reason)) from exc
    except TimeoutError as exc:
        raise GeminiError("timeout", "The request timed out.") from exc


def _from_http(exc: urllib.error.HTTPError) -> GeminiError:
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        error = payload.get("error") or {}
        message = str(error.get("message") or "")[:300]
        status = str(error.get("status") or "")
        reasons = " ".join(
            str(d.get("reason") or "") for d in (error.get("details") or []) if isinstance(d, dict)
        )
    except Exception:  # noqa: BLE001
        message, status, reasons = "", "", ""

    # Google reports a bad key as 400 INVALID_ARGUMENT, not 401.
    if "API_KEY_INVALID" in reasons or exc.code == 401:
        return GeminiError("auth", message or "The API key was rejected.")
    if exc.code == 403:
        return GeminiError("forbidden", message or "This key may not use that model or API.")
    if exc.code == 404:
        return GeminiError("model_missing", message or "That model name does not exist.")
    if exc.code == 429 or status == "RESOURCE_EXHAUSTED":
        if "quota" in message.lower() or "billing" in message.lower():
            return GeminiError("quota", message or "The quota for this key is used up.")
        return GeminiError("rate_limit", message or "Too many requests; slow down.")
    if exc.code == 400:
        return GeminiError("invalid_request", message or "The request was rejected.")
    if exc.code >= 500:
        return GeminiError("server", message or f"Gemini returned HTTP {exc.code}.")
    return GeminiError("http_error", message or f"HTTP {exc.code}")


# --------------------------------------------------------------------------
# health and models
# --------------------------------------------------------------------------

HEALTH_TTL = 60.0
_HEALTH_LOCK = threading.Lock()
_HEALTH_CACHE: dict[str, Any] = {"at": 0.0, "fingerprint": "", "value": None}


def _fingerprint() -> str:
    raw = _base() + "\0" + (config.get_secret(SECRET_NAME) or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def health(timeout: float = 8.0, *, force: bool = False) -> dict[str, Any]:
    fingerprint = _fingerprint()
    with _HEALTH_LOCK:
        cached = _HEALTH_CACHE
        if (not force and cached["value"] is not None and cached["fingerprint"] == fingerprint
                and time.monotonic() - cached["at"] < HEALTH_TTL):
            return dict(cached["value"])
    result = _probe(timeout)
    with _HEALTH_LOCK:
        _HEALTH_CACHE.update(at=time.monotonic(), fingerprint=fingerprint, value=dict(result))
    return result


def _probe(timeout: float) -> dict[str, Any]:
    base = _base()
    if not has_key():
        return {"state": "disconnected", "models": [], "url": base, "key_set": False,
                "error": "no_key", "detail": "No API key stored."}
    try:
        # Listing models is free; it proves the key without spending anything.
        with _request("/v1beta/models?pageSize=200", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        models = sorted(
            str(m.get("name", "")).removeprefix("models/")
            for m in body.get("models", [])
            if isinstance(m, dict) and "generateContent" in (m.get("supportedGenerationMethods") or [])
        )
        return {"state": "connected", "models": models, "url": base, "key_set": True}
    except GeminiError as exc:
        state = "disconnected" if exc.code in ("network", "timeout") else "error"
        return {"state": state, "models": [], "url": base, "key_set": True,
                "error": exc.code, "detail": exc.detail}


def list_models(timeout: float = 8.0) -> list[str]:
    return health(timeout=timeout).get("models", [])


# --------------------------------------------------------------------------
# translation
# --------------------------------------------------------------------------

#: The subset of JSON Schema that Gemini's function declarations accept.
#: MCP servers routinely send `$schema`, `additionalProperties`, `default`
#: and friends, and one unknown keyword fails the whole request.
_SCHEMA_KEYS = {
    "type", "description", "properties", "required", "items", "enum", "format",
    "nullable", "minimum", "maximum", "minItems", "maxItems", "minLength",
    "maxLength", "pattern", "anyOf", "title",
}


def clean_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [clean_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: clean_schema(sub) for name, sub in value.items()}
        elif key in ("items", "anyOf"):
            out[key] = clean_schema(value)
        elif key == "type" and isinstance(value, list):
            # ["string", "null"] → string + nullable
            kinds = [v for v in value if v != "null"]
            out["type"] = kinds[0] if kinds else "string"
            if "null" in value:
                out["nullable"] = True
        else:
            out[key] = value
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    if out.get("type") == "object" and not out.get("properties"):
        # An object with no properties is rejected; drop to an empty schema.
        out.pop("properties", None)
        out.pop("required", None)
    return out


def _tools(definitions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    declarations = []
    for d in definitions or []:
        fn = d.get("function") or {}
        decl = {"name": fn.get("name", "tool"), "description": str(fn.get("description") or "")[:900]}
        params = clean_schema(fn.get("parameters") or {})
        if params and params.get("properties"):
            decl["parameters"] = params
        declarations.append(decl)
    return [{"functionDeclarations": declarations}] if declarations else []


def to_gemini(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """(system instruction, contents). Same-role turns are merged."""
    system: list[str] = []
    contents: list[dict[str, Any]] = []

    def push(role: str, parts: list[dict[str, Any]]) -> None:
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for m in messages:
        role = m.get("role", "user")
        text = str(m.get("content") or "")
        if role == "system":
            if text:
                system.append(text)
            continue
        if role == "tool":
            response: dict[str, Any] = {"name": m.get("name") or "tool",
                                        "response": {"result": text}}
            # Only echo an id Gemini itself issued, never one made up below.
            if m.get("tool_call_id") and not str(m["tool_call_id"]).startswith("gemini_"):
                response["id"] = m["tool_call_id"]
            push("user", [{"functionResponse": response}])
            continue
        if role == "assistant":
            native = m.get("_native") or {}
            if native.get("provider") == "gemini" and native.get("parts"):
                # Returned exactly as received: function-call parts carry a
                # thoughtSignature that must come back unchanged.
                push("model", [dict(p) for p in native["parts"]])
                continue
            parts: list[dict[str, Any]] = [{"text": text}] if text else []
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {}
                parts.append({"functionCall": {"name": fn.get("name", ""), "args": args or {}}})
            push("model", parts)
            continue
        parts = [{"inlineData": {"mimeType": "image/jpeg", "data": img}} for img in m.get("images") or []]
        if text:
            parts.append({"text": text})
        push("user", parts)

    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return "\n\n".join(system), contents


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------

def chat_stream_full(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """Yield token events, then one message event with tool calls and usage."""
    system, contents = to_gemini(messages)
    payload: dict[str, Any] = {"contents": contents}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    declared = _tools(tools)
    if declared:
        payload["tools"] = declared
    if max_tokens:
        payload["generationConfig"] = {"maxOutputTokens": int(max_tokens)}

    path = f"/v1beta/models/{urllib.parse.quote(model, safe='-._')}:streamGenerateContent?alt=sse"
    text: list[str] = []
    native_parts: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    usage = {"input": 0, "output": 0}
    finish = ""

    response = _request(path, payload, timeout=timeout, stream=True)
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                err = chunk["error"]
                raise GeminiError("stream_error", str(err.get("message") or err)[:300])
            feedback = chunk.get("promptFeedback") or {}
            if feedback.get("blockReason"):
                raise GeminiError("blocked", f"Gemini blocked the prompt: {feedback['blockReason']}")
            meta = chunk.get("usageMetadata") or {}
            if meta:
                # Cumulative in each chunk; the last one is the total. Thinking
                # tokens are billed as output.
                usage["input"] = int(meta.get("promptTokenCount") or 0)
                usage["output"] = int(meta.get("candidatesTokenCount") or 0) + \
                    int(meta.get("thoughtsTokenCount") or 0)
            for candidate in chunk.get("candidates") or []:
                finish = candidate.get("finishReason") or finish
                for part in (candidate.get("content") or {}).get("parts") or []:
                    native_parts.append(part)
                    if part.get("thought"):
                        continue          # a thought summary, not the answer
                    if "functionCall" in part:
                        fc = part["functionCall"] or {}
                        calls.append({
                            "id": fc.get("id") or f"gemini_{len(calls)}",
                            "type": "function",
                            "function": {"name": fc.get("name", ""), "arguments": fc.get("args") or {}},
                        })
                    elif part.get("text"):
                        text.append(part["text"])
                        if not calls:
                            yield {"type": "token", "text": part["text"]}
                break  # one candidate is requested

    if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII") and not text and not calls:
        raise GeminiError("blocked", f"Gemini stopped the answer ({finish}).")

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = calls
        message["_native"] = {"provider": "gemini", "parts": native_parts}
    yield {"type": "message", "message": message, "usage": usage, "stop": finish}

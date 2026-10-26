"""HTTP API for the JARVIS frontend."""

from __future__ import annotations

import json
import platform
import sys
import time
from typing import Any, Iterator

from . import (
    anthropic_client, armory, claude_code, config, conversations, llm, ollama,
    openai_client, paths, vision, voices,
)

#: Which secret store entry belongs to which provider. Keeping the mapping in
#: one place is what lets the key routes stay provider-agnostic.
_KEYED_PROVIDERS = {
    llm.OPENAI: openai_client,
    llm.CLAUDE: anthropic_client,
}
from .app import HttpError, Request, Response, Router, sse

router = Router()
STARTED_AT = time.time()

# Runtime-only power state: standby must never survive a restart.
_RUNTIME = {"power_state": "active"}


def _language_instruction(lang: str) -> str:
    names = {
        "ro": "Romanian", "en": "English", "fr": "French",
        "de": "German", "es": "Spanish", "it": "Italian",
    }
    return (
        f"You are JARVIS, a concise, calm assistant. Always answer in "
        f"{names.get(lang, 'English')} unless the user writes in another language."
    )


# --------------------------------------------------------------------------
# health / status
# --------------------------------------------------------------------------

@router.get("/api/health")
def health(req: Request) -> Response:
    cfg = config.get_config()
    # Only the provider actually in use is contacted. Probing a metered API
    # every twelve seconds to colour a chip nobody is looking at would be both
    # slow and, on a billed account, rude.
    brain = llm.health(timeout=6.0)
    ollama_health = (
        ollama.health(timeout=2.5)
        if brain["provider"] == llm.OLLAMA
        else {"state": "disconnected", "models": [], "url": brain.get("url", ""),
              "error": "not in use"}
    )
    engine = voices.engine_info()
    chosen = voices.resolve_voice(
        cfg["conversation_language"], cfg.get("voice_override", ""), cfg.get("voice_by_language")
    )
    connections = armory.list_connections()
    return Response(
        {
            "server": {
                "state": "connected",
                "uptime": int(time.time() - STARTED_AT),
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "power_state": _RUNTIME["power_state"],
                "home": str(paths.JARVIS_HOME),
            },
            # The active brain, whichever it is. This is what the status chip
            # reads; "ollama" below stays for anything still expecting it.
            "llm": brain,
            "ollama": {
                "state": ollama_health["state"],
                "url": ollama_health["url"],
                "models": ollama_health["models"],
                "chat_model": cfg["chat_model"],
                "chat_model_installed": (
                    ollama.has_model(cfg["chat_model"])
                    if brain["provider"] == llm.OLLAMA else False
                ),
                "error": ollama_health.get("error", ""),
            },
            "voice": {
                # "disconnected" rather than "error" when a voice is simply not
                # installed yet: on a fresh install that is expected, not a fault.
                "state": "connected" if (engine["available"] and chosen["installed"]) else "disconnected",
                "engine": engine,
                "selected": chosen,
                "installed": voices.installed_voices(),
            },
            "vision": vision.status(),
            "armory": {
                "state": "connected" if any(c["state"] == "connected" for c in connections)
                         else ("error" if any(c["state"] == "error" for c in connections) else "disconnected"),
                "total": len(connections),
                "connected": sum(1 for c in connections if c["state"] == "connected"),
            },
        }
    )


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

@router.get("/api/config")
def get_config_route(req: Request) -> Response:
    return Response(config.get_config())


@router.post("/api/config")
def post_config(req: Request) -> Response:
    patch = req.json()
    patch.pop("mic_muted", None)      # the mic state is owned by the browser
    patch.pop("power_state", None)    # power state goes through /api/power
    # Credentials never travel through the settings document, which is
    # readable by the browser. They have their own endpoint below.
    for key in (
        "openai_api_key", "api_key", "openai:api_key",
        "anthropic_api_key", "claude_api_key", "anthropic:api_key",
    ):
        patch.pop(key, None)
    return Response(config.update_config(patch))


# --------------------------------------------------------------------------
# language model provider
# --------------------------------------------------------------------------

@router.get("/api/llm/status")
def get_llm_status(req: Request) -> Response:
    """State of the active provider. Reports whether a key exists, never what."""
    # The user is looking at the settings panel, so check for real rather
    # than reusing the cached answer the status chip polls against.
    info = llm.health(force=True)
    info["providers"] = list(llm.PROVIDERS)
    info["openai_key_set"] = openai_client.has_key()
    info["claude_key_set"] = anthropic_client.has_key()
    return Response(info)


@router.get("/api/llm/connections")
def get_llm_connections(req: Request) -> Response:
    """
    Everything the AI Connections panel needs, in one call.

    Each provider reports whether it is switched on, whether a key is stored
    (never the key), what it costs, and — for the one in use — its live state.
    An inactive paid provider is not contacted until the user presses Test.
    """
    cfg = config.get_config()
    active = llm.provider()
    live = llm.health(force=True)

    def entry(which: str, **extra: Any) -> dict[str, Any]:
        return {
            "provider": which,
            "label": llm.LABELS[which],
            "active": which == active,
            "billed": which in llm.BILLED,
            "model": llm.active_model(which),
            # Only the active provider has been probed; the others report
            # "unknown" rather than pretending to a state nobody checked.
            "state": live.get("state", "unknown") if which == active else "unknown",
            "detail": live.get("detail", "") if which == active else "",
            **extra,
        }

    return Response({
        "active": active,
        "connections": [
            entry(
                llm.OLLAMA,
                enabled=True,
                key_required=False,
                key_set=True,
                cost_ack=True,
                url=cfg.get("ollama_url", ""),
            ),
            entry(
                llm.OPENAI,
                enabled=bool(cfg.get("openai_enabled")),
                key_required=True,
                key_set=openai_client.has_key(),
                cost_ack=bool(cfg.get("openai_cost_ack")),
                url=cfg.get("openai_base_url", ""),
            ),
            entry(
                llm.CLAUDE,
                enabled=bool(cfg.get("claude_enabled")),
                key_required=True,
                key_set=anthropic_client.has_key(),
                cost_ack=bool(cfg.get("claude_cost_ack")),
                url=cfg.get("anthropic_base_url", ""),
                suggested_models=list(anthropic_client.SUGGESTED_MODELS),
                pricing={m: anthropic_client.price_of(m) for m in anthropic_client.SUGGESTED_MODELS},
                effort=cfg.get("claude_effort", "low"),
            ),
        ],
        "code": claude_code.status(),
    })


@router.post("/api/llm/test")
def post_llm_test(req: Request) -> Response:
    """
    Check one connection on demand.

    This is the Test button. It probes the named provider whether or not it is
    the active one — that is the whole point — but it never sends a chat
    completion, so testing a paid connection costs nothing: listing models is
    an unbilled call on both APIs.
    """
    which = (req.json().get("provider") or "").lower()
    if which not in llm.PROVIDERS:
        raise HttpError(400, "unknown provider")

    if which == llm.OLLAMA:
        info = ollama.health(timeout=4.0)
        models = info.get("models", [])
    else:
        client = _KEYED_PROVIDERS[which]
        if not client.has_key():
            return Response({
                "provider": which, "state": "disconnected",
                "error": "no_key", "detail": "No API key is stored.", "models": [],
            })
        info = client.health(timeout=12.0, force=True)
        models = info.get("models", [])

    model = llm.active_model(which)
    return Response({
        "provider": which,
        "state": info.get("state", "unknown"),
        "error": info.get("error", ""),
        "detail": info.get("detail", ""),
        "models": models,
        "model": model,
        "model_ready": (model in models) if models else info.get("state") == "connected",
        "billed": which in llm.BILLED,
    })


@router.get("/api/llm/models")
def get_llm_models(req: Request) -> Response:
    try:
        return Response({"provider": llm.provider(), "models": llm.models()})
    except Exception as exc:  # noqa: BLE001 - reported, not raised at the UI
        return Response({"provider": llm.provider(), "models": [], "error": str(exc)[:200]})


@router.post("/api/llm/key")
def post_llm_key(req: Request) -> Response:
    """
    Store an API key on the backend.

    It is written to ~/.jarvis/secrets.json with mode 600 and is never sent
    back: the response says only whether one is now set. There is deliberately
    no GET for this.
    """
    data = req.json()
    which = (data.get("provider") or llm.OPENAI).lower()
    client = _KEYED_PROVIDERS.get(which)
    if client is None:
        raise HttpError(400, "unknown provider")
    value = data.get("key")
    if value is None:
        raise HttpError(400, "key required")
    value = str(value).strip()
    if value and len(value) < 20:
        raise HttpError(400, "that does not look like an API key")
    client.set_key(value)
    return Response({"provider": which, "key_set": client.has_key()})


@router.delete("/api/llm/key")
def delete_llm_key(req: Request) -> Response:
    which = (req.q("provider", llm.OPENAI) or llm.OPENAI).lower()
    client = _KEYED_PROVIDERS.get(which)
    if client is None:
        raise HttpError(400, "unknown provider")
    client.set_key("")
    # Without a key the paid provider cannot work, so fall back rather than
    # leaving every message failing.
    if llm.provider() == which:
        config.update_config({"llm_provider": llm.OLLAMA})
    return Response({"provider": which, "key_set": client.has_key(), "active": llm.provider()})


# --------------------------------------------------------------------------
# Claude Code — the local CLI, behind an approval gate
# --------------------------------------------------------------------------

@router.get("/api/code/status")
def get_code_status(req: Request) -> Response:
    """Whether there is a usable CLI here, and if not, the actual reason."""
    return Response(claude_code.status())


@router.get("/api/code/requests")
def get_code_requests(req: Request) -> Response:
    return Response({"requests": claude_code.recent()})


@router.post("/api/code/request")
def post_code_request(req: Request) -> Response:
    """
    Record a coding request. **Nothing runs here.**

    The response carries the exact argument vector, so the interface shows the
    user the real command before asking them to approve it.
    """
    data = req.json()
    try:
        record = claude_code.propose(
            str(data.get("prompt") or ""),
            directory=str(data.get("directory") or ""),
            mode=str(data.get("mode") or claude_code.PLAN),
        )
    except claude_code.CodeError as exc:
        raise HttpError(400, f"{exc.code}: {exc.detail}") from exc
    return Response(record)


@router.post("/api/code/approve")
def post_code_approve(req: Request) -> Response:
    """The only route that starts a process, and only for a pending request."""
    data = req.json()
    try:
        record = claude_code.approve(
            str(data.get("id") or ""),
            allow_edits=bool(data.get("allow_edits")),
        )
    except claude_code.CodeError as exc:
        raise HttpError(400, f"{exc.code}: {exc.detail}") from exc
    return Response(record)


@router.post("/api/code/cancel")
def post_code_cancel(req: Request) -> Response:
    try:
        return Response(claude_code.cancel(str(req.json().get("id") or "")))
    except claude_code.CodeError as exc:
        raise HttpError(400, f"{exc.code}: {exc.detail}") from exc


@router.post("/api/power")
def post_power(req: Request) -> Response:
    state = (req.json().get("state") or "active").lower()
    if state not in ("active", "standby"):
        raise HttpError(400, "state must be active or standby")
    _RUNTIME["power_state"] = state
    return Response({"state": state})


# --------------------------------------------------------------------------
# voices
# --------------------------------------------------------------------------

@router.get("/api/voices")
def get_voices(req: Request) -> Response:
    cfg = config.get_config()
    return Response(
        {
            "engine": voices.engine_info(),
            "catalog": voices.catalog(),
            "installed": voices.installed_voices(),
            "selected": voices.resolve_voice(
                cfg["conversation_language"], cfg.get("voice_override", ""), cfg.get("voice_by_language")
            ),
        }
    )


@router.post("/api/voices/download")
def post_voice_download(req: Request) -> Response:
    """Explicit, per-voice, user-triggered download. Never called automatically."""
    voice_id = (req.json().get("voice") or "").strip()
    if not voice_id:
        raise HttpError(400, "voice required")
    if not req.json().get("confirm"):
        raise HttpError(400, "download must be confirmed by the user")
    return Response(voices.start_download(voice_id))


@router.get("/api/voices/download/<voice_id>")
def get_voice_download(req: Request) -> Response:
    return Response(voices.download_status(req.params["voice_id"]))


@router.post("/api/tts")
def post_tts(req: Request) -> Response:
    if _RUNTIME["power_state"] == "standby":
        raise HttpError(409, "standby")
    cfg = config.get_config()
    data = req.json()
    text = (data.get("text") or "").strip()
    lang = data.get("language") or cfg["conversation_language"]
    chosen = voices.resolve_voice(lang, cfg.get("voice_override", ""), cfg.get("voice_by_language"))
    if not chosen["installed"]:
        raise HttpError(409, f"voice_unavailable:{chosen['voice'] or lang}")
    try:
        wav = voices.synthesize(text, chosen["voice"], float(cfg.get("tts_speed") or 1.0))
    except Exception as exc:  # noqa: BLE001
        raise HttpError(503, f"tts_failed:{exc}") from exc
    return Response(wav, content_type="audio/wav")


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------

@router.get("/api/conversations")
def get_conversations(req: Request) -> Response:
    return Response({"conversations": conversations.list_conversations()})


@router.post("/api/conversations")
def post_conversation(req: Request) -> Response:
    cfg = config.get_config()
    return Response(conversations.create(req.json().get("language") or cfg["conversation_language"]))


@router.get("/api/conversations/<conv_id>")
def get_conversation(req: Request) -> Response:
    try:
        return Response(conversations.load(req.params["conv_id"]))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc


@router.delete("/api/conversations/<conv_id>")
def delete_conversation(req: Request) -> Response:
    return Response({"deleted": conversations.delete(req.params["conv_id"])})


# --------------------------------------------------------------------------
# chat (SSE)
# --------------------------------------------------------------------------

@router.post("/api/chat")
def post_chat(req: Request) -> Response:
    cfg = config.get_config()
    data = req.json()
    message = (data.get("message") or "").strip()
    if not message:
        raise HttpError(400, "message required")
    lang = data.get("language") or cfg["conversation_language"]
    conv_id = data.get("conversation_id") or ""
    # Whichever provider is active decides the default model.
    model = (data.get("model") or "").strip() or llm.active_model()

    if conv_id:
        try:
            conv = conversations.load(conv_id)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
    else:
        conv = conversations.create(lang)
        conv_id = conv["id"]

    window = llm.history_limit()
    history = [{"role": m["role"], "content": m["content"]} for m in conv["messages"][-window:]]
    system = (cfg.get("system_prompt") or "").strip() or _language_instruction(lang)
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]

    conversations.append(conv_id, "user", message, language=lang)

    def stream() -> Iterator[bytes]:
        yield sse("start", {
            "conversation_id": conv_id,
            "model": model,
            "language": lang,
            "provider": llm.provider(),
        })
        parts: list[str] = []
        try:
            for chunk in llm.chat_stream(messages, model=model):
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    parts.append(piece)
                    yield sse("token", {"text": piece})
                if chunk.get("done"):
                    break
        except llm.LLMError as exc:
            # The code is a stable token the interface translates; the detail
            # is the provider's own words, which is what makes a bad key or an
            # empty balance diagnosable from the phone.
            yield sse("error", {
                "code": exc.code,
                "provider": exc.provider,
                "detail": exc.detail[:300],
            })
            return
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"code": "server_error", "detail": f"{type(exc).__name__}: {exc}"[:300]})
            return
        text = "".join(parts).strip()
        if text:
            conversations.append(conv_id, "assistant", text, language=lang)
        yield sse("done", {"conversation_id": conv_id, "text": text})

    return Response(stream=stream(), content_type="text/event-stream; charset=utf-8")


# --------------------------------------------------------------------------
# Tool Armory
# --------------------------------------------------------------------------

@router.get("/api/armory")
def get_armory(req: Request) -> Response:
    return Response({"connections": armory.list_connections(), "tools": armory.all_tools()})


@router.post("/api/armory")
def post_armory(req: Request) -> Response:
    return Response(armory.create_connection(req.json()), status=201)


@router.put("/api/armory/<conn_id>")
def put_armory(req: Request) -> Response:
    updated = armory.update_connection(req.params["conn_id"], req.json())
    if updated is None:
        raise HttpError(404, "unknown connection")
    return Response(updated)


@router.delete("/api/armory/<conn_id>")
def delete_armory(req: Request) -> Response:
    if not armory.delete_connection(req.params["conn_id"]):
        raise HttpError(404, "unknown connection")
    return Response({"deleted": True})


@router.post("/api/armory/<conn_id>/connect")
def post_armory_connect(req: Request) -> Response:
    return Response(armory.connect(req.params["conn_id"]))


@router.post("/api/armory/<conn_id>/disconnect")
def post_armory_disconnect(req: Request) -> Response:
    result = armory.disconnect(req.params["conn_id"])
    if result is None:
        raise HttpError(404, "unknown connection")
    return Response(result)


@router.post("/api/armory/<conn_id>/call")
def post_armory_call(req: Request) -> Response:
    data = req.json()
    tool = (data.get("tool") or "").strip()
    if not tool:
        raise HttpError(400, "tool required")
    return Response(armory.call_tool(req.params["conn_id"], tool, data.get("arguments") or {}))


# --------------------------------------------------------------------------
# EYES / vision
# --------------------------------------------------------------------------

@router.get("/api/vision/status")
def get_vision_status(req: Request) -> Response:
    return Response(vision.status())


@router.post("/api/vision/describe")
def post_vision_describe(req: Request) -> Response:
    if _RUNTIME["power_state"] == "standby":
        raise HttpError(409, "standby")
    data = req.json()
    image = data.get("image") or ""
    if not image:
        raise HttpError(400, "image required")
    cfg = config.get_config()
    try:
        result = vision.describe(image, data.get("language") or cfg["conversation_language"], data.get("prompt", ""))
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc
    return Response(result)


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------

@router.get("/api/diagnostics")
def get_diagnostics(req: Request) -> Response:
    """Everything needed to debug an install, minus every secret value."""
    cfg = config.get_config()
    return Response(
        {
            "paths": {
                "home": str(paths.JARVIS_HOME),
                "voices": str(paths.VOICES_DIR),
                "conversations": str(paths.CONVERSATIONS_DIR),
            },
            "config_keys": sorted(cfg.keys()),
            "secret_names": config.secret_names(),  # names only, never values
            "voices_installed": voices.installed_voices(),
            "voice_engine": voices.engine_info(),
            "ollama": ollama.health(timeout=2.0),
            "connections": [
                {"id": c["id"], "name": c["name"], "state": c["state"], "tools": len(c["tools"])}
                for c in armory.list_connections()
            ],
        }
    )


@router.post("/api/log")
def post_log(req: Request) -> Response:
    """Frontend error sink: makes JS failures visible in the Termux console."""
    data = req.json()
    line = json.dumps({"level": data.get("level", "error"), "msg": data.get("message", "")[:500]})
    sys.stderr.write(f"[frontend] {line}\n")
    return Response({"ok": True})

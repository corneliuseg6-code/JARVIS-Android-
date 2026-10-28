"""Which brain is answering: a router over Ollama, OpenAI and Claude.

JARVIS can talk to a local Ollama (free, private, offline) or to one of two
paid remote APIs. Everything above this module — the chat endpoint, the health
report, EYES — asks the router rather than a provider, so adding a fourth one
means writing a client and adding two lines here, not editing the API layer.

Both clients already yield chunks in the same shape, so there is no format
translation to do at this level; what the router adds is provider choice,
model resolution and a single error type with codes the interface can
translate.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator

from . import anthropic_client, config, ollama, openai_client

OLLAMA = "ollama"
OPENAI = "openai"
CLAUDE = "claude"
PROVIDERS = (OLLAMA, OPENAI, CLAUDE)

#: Shown in the interface when a provider has no model configured.
FALLBACK_MODEL = {
    OLLAMA: "llama3.2:3b",
    OPENAI: "gpt-4o-mini",
    CLAUDE: "claude-opus-5-5",
}

LABELS = {OLLAMA: "Ollama", OPENAI: "OpenAI", CLAUDE: "Claude"}

#: Which providers cost money. Used for the cost notice and the "billed" flag
#: the interface shows beside the active brain.
BILLED = {OPENAI, CLAUDE}


class LLMError(RuntimeError):
    """A provider failure, carrying a code the frontend knows how to say."""

    def __init__(self, code: str, detail: str = "", provider: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
        self.provider = provider


MODES = ("manual", "local", "cloud", "auto")

#: How long an automatic routing decision is reused. Deciding means asking
#: Ollama whether it is up, and the status chip, the chat endpoint and the
#: model resolution all ask in the same second.
ROUTE_TTL = 15.0
_ROUTE_LOCK = threading.Lock()
_ROUTE_CACHE: dict[str, Any] = {"at": 0.0, "key": "", "value": None}


def configured() -> str:
    """The provider picked in settings, falling back to the free local one."""
    chosen = (config.get_config().get("llm_provider") or OLLAMA).lower()
    return chosen if chosen in PROVIDERS else OLLAMA


def mode() -> str:
    chosen = str(config.get_config().get("llm_mode") or "manual").lower()
    return chosen if chosen in MODES else "manual"


def authorised(which: str) -> bool:
    """
    May this provider be used without asking anyone anything?

    Ollama always may. A paid one needs all three things the user does on
    purpose: switch it on, acknowledge what it costs, and store a key.
    Automatic routing never reaches past this — it cannot start billing an
    account the user has not set up for exactly that.
    """
    if which == OLLAMA:
        return True
    cfg = config.get_config()
    if which == OPENAI:
        return bool(cfg.get("openai_enabled") and cfg.get("openai_cost_ack")
                    and openai_client.has_key())
    if which == CLAUDE:
        return bool(cfg.get("claude_enabled") and cfg.get("claude_cost_ack")
                    and anthropic_client.has_key())
    return False


def cloud_choice() -> str | None:
    """The paid provider to use: the one picked in settings, else the first authorised."""
    picked = configured()
    order = [picked] if picked in BILLED else []
    order += [p for p in (OPENAI, CLAUDE) if p not in order]
    for which in order:
        if authorised(which):
            return which
    return None


def _local_ready() -> bool:
    info = ollama.health(timeout=1.5)
    if info.get("state") != "connected":
        return False
    model = active_model(OLLAMA)
    wanted = model.split(":")[0]
    return any(m == model or m.split(":")[0] == wanted for m in info.get("models", []))


def route(*, force: bool = False) -> dict[str, str]:
    """
    Which provider answers the next turn, and why.

    ``reason`` is a stable code the interface translates, so the status can
    say "Ollama is down — answering with Claude" instead of leaving the user
    to wonder why a reply was billed.
    """
    which_mode = mode()
    if which_mode == "manual":
        return {"provider": configured(), "mode": which_mode, "reason": "manual"}
    if which_mode == "local":
        return {"provider": OLLAMA, "mode": which_mode, "reason": "local"}
    if which_mode == "cloud":
        cloud = cloud_choice()
        if cloud:
            return {"provider": cloud, "mode": which_mode, "reason": "cloud"}
        return {"provider": OLLAMA, "mode": which_mode, "reason": "no_cloud"}

    # auto
    cfg = config.get_config()
    key = "\0".join(str(cfg.get(k)) for k in (
        "llm_provider", "chat_model", "ollama_url", "openai_enabled",
        "openai_cost_ack", "claude_enabled", "claude_cost_ack"))
    key += "\0" + str(openai_client.has_key()) + str(anthropic_client.has_key())
    now = time.monotonic()
    with _ROUTE_LOCK:
        cached = _ROUTE_CACHE
        if (not force and cached["value"] is not None and cached["key"] == key
                and now - cached["at"] < ROUTE_TTL):
            return dict(cached["value"])

    if _local_ready():
        value = {"provider": OLLAMA, "mode": "auto", "reason": "local_ready"}
    else:
        cloud = cloud_choice()
        value = ({"provider": cloud, "mode": "auto", "reason": "local_down"} if cloud
                 else {"provider": OLLAMA, "mode": "auto", "reason": "local_down_no_cloud"})
    with _ROUTE_LOCK:
        _ROUTE_CACHE.update(at=time.monotonic(), key=key, value=dict(value))
    return value


def provider() -> str:
    """The provider that answers the next turn, after routing."""
    return route()["provider"]


def active_model(which: str | None = None) -> str:
    cfg = config.get_config()
    which = which or provider()
    if which == OPENAI:
        return (cfg.get("openai_model") or "").strip() or FALLBACK_MODEL[OPENAI]
    if which == CLAUDE:
        return (cfg.get("claude_model") or "").strip() or FALLBACK_MODEL[CLAUDE]
    return (cfg.get("chat_model") or "").strip() or FALLBACK_MODEL[OLLAMA]


def history_limit() -> int:
    """
    How many past messages to resend.

    This matters far more on OpenAI than on Ollama: every turn re-uploads the
    whole window, so the window length is a direct multiplier on the bill.
    """
    try:
        value = int(config.get_config().get("chat_history_limit") or 20)
    except (TypeError, ValueError):
        value = 20
    return max(2, min(100, value))


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def health(timeout: float = 6.0, *, force: bool = False) -> dict[str, Any]:
    """
    The state of the provider that is actually in use.

    The inactive provider is not contacted: polling OpenAI every twelve
    seconds to colour a chip nobody is looking at would be both slow and, on
    a metered account, rude. Background polls also reuse a briefly cached
    answer; `force` is for when the user is looking straight at the panel.

    The routing mode and the reason for the choice ride along, so the
    interface can say why this provider is the one answering.
    """
    routed = route(force=force)
    info = _health(routed["provider"], timeout=timeout, force=force)
    info["mode"] = routed["mode"]
    info["route_reason"] = routed["reason"]
    return info


def _health(which: str, *, timeout: float, force: bool) -> dict[str, Any]:
    model = active_model(which)
    if which == OPENAI:
        info = openai_client.health(timeout=timeout, force=force)
        models = info.get("models", [])
        ready = bool(models) and model in models
        return {
            "provider": OPENAI,
            "label": "OpenAI",
            "state": info.get("state", "unknown"),
            "model": model,
            "model_ready": ready if models else info.get("state") == "connected",
            "models": models,
            "url": info.get("url", ""),
            "key_set": info.get("key_set", False),
            "error": info.get("error", ""),
            "detail": info.get("detail", ""),
            "billed": True,
        }

    if which == CLAUDE:
        info = anthropic_client.health(timeout=timeout, force=force)
        models = info.get("models", [])
        ready = bool(models) and model in models
        return {
            "provider": CLAUDE,
            "label": LABELS[CLAUDE],
            "state": info.get("state", "unknown"),
            "model": model,
            "model_ready": ready if models else info.get("state") == "connected",
            "models": models,
            "url": info.get("url", ""),
            "key_set": info.get("key_set", False),
            "error": info.get("error", ""),
            "detail": info.get("detail", ""),
            "billed": True,
        }

    info = ollama.health(timeout=min(timeout, 3.0))
    return {
        "provider": OLLAMA,
        "label": "Ollama",
        "state": info.get("state", "unknown"),
        "model": model,
        "model_ready": ollama.has_model(model) if info.get("state") == "connected" else False,
        "models": info.get("models", []),
        "url": info.get("url", ""),
        "key_set": True,          # nothing to configure
        "error": info.get("error", ""),
        "detail": "",
        "billed": False,
    }


def models(timeout: float = 8.0) -> list[str]:
    which = provider()
    if which == OPENAI:
        return openai_client.list_models(timeout=timeout)
    if which == CLAUDE:
        return anthropic_client.list_models(timeout=timeout)
    return ollama.list_models(timeout=timeout)


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------

def ollama_runtime() -> tuple[dict[str, Any], str]:
    """
    (options, keep_alive) for an Ollama request, from the user's settings.

    Only values the user actually set are sent. An unset `num_ctx` is left to
    Ollama's own default rather than guessed at, because raising it costs
    memory and memory is the scarce thing on a phone.
    """
    cfg = config.get_config()
    options: dict[str, Any] = {}
    try:
        ctx = int(cfg.get("ollama_num_ctx") or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx > 0:
        options["num_ctx"] = ctx
    try:
        predict = int(cfg.get("ollama_num_predict"))
    except (TypeError, ValueError):
        predict = -1
    if predict > 0:
        options["num_predict"] = predict
    keep_alive = str(cfg.get("ollama_keep_alive") or "30m").strip()
    return options, keep_alive


def trim_history(
    messages: list[dict[str, Any]], *, budget: int | None = None,
) -> list[dict[str, Any]]:
    """
    Drop the oldest turns until the conversation fits a character budget.

    `history_limit` counts messages, which cannot tell twenty short turns
    from twenty long ones; the model re-reads all of it every turn either
    way. The system message and the newest turn are always kept — without
    them there is nothing to answer.
    """
    if budget is None:
        try:
            budget = int(config.get_config().get("chat_char_budget") or 6000)
        except (TypeError, ValueError):
            budget = 6000
    budget = max(500, budget)

    head = [m for m in messages[:1] if m.get("role") == "system"]
    rest = messages[len(head):]
    if not rest:
        return list(messages)

    kept: list[dict[str, Any]] = []
    used = sum(len(str(m.get("content") or "")) for m in head)
    for message in reversed(rest):
        size = len(str(message.get("content") or ""))
        if kept and used + size > budget:
            break
        kept.append(message)
        used += size
    kept.reverse()
    return [*head, *kept]


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    which: str | None = None,
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """Stream an answer from the active provider, in the Ollama chunk shape."""
    which = which or provider()
    chosen = (model or "").strip() or active_model(which)
    try:
        if which == OPENAI:
            yield from openai_client.chat_stream(chosen, messages, timeout=timeout)
        elif which == CLAUDE:
            yield from anthropic_client.chat_stream(chosen, messages, timeout=timeout)
        else:
            options, keep_alive = ollama_runtime()
            yield from ollama.chat_stream(chosen, messages, options=options,
                                          keep_alive=keep_alive, timeout=timeout)
    except openai_client.OpenAIError as exc:
        raise LLMError(exc.code, exc.detail, OPENAI) from exc
    except anthropic_client.AnthropicError as exc:
        raise LLMError(exc.code, exc.detail, CLAUDE) from exc
    except ollama.OllamaError as exc:
        raise LLMError("ollama_error", str(exc)[:300], OLLAMA) from exc


def chat_once_full(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    which: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """
    One turn, returning the whole assistant message with its `tool_calls`.

    Kept for callers that want the message and not the stream; it drains
    ``chat_stream_full``, so every provider that streams tools has it too.
    """
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    for event in chat_stream_full(messages, model=model, tools=tools,
                                  which=which, timeout=timeout):
        if event["type"] == "message":
            message = event["message"]
    return message


def chat_stream_full(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    which: str | None = None,
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """
    Stream a turn while still assembling the complete message.

    This is what the tool loop uses. Yields ``{"type": "token"}`` events as
    text arrives and one final ``{"type": "message"}`` carrying the whole
    turn with its `tool_calls`, so the loop loses nothing by streaming. All
    three providers implement it, each translating its own wire format into
    the one shape the loop reads.
    """
    which = which or provider()
    chosen = (model or "").strip() or active_model(which)
    if which != OLLAMA and not supports_tools(which):
        tools = None
    try:
        if which == OPENAI:
            yield from openai_client.chat_stream_full(chosen, messages, tools=tools,
                                                      timeout=timeout)
        elif which == CLAUDE:
            yield from anthropic_client.chat_stream_full(chosen, messages, tools=tools,
                                                         timeout=timeout)
        else:
            options, keep_alive = ollama_runtime()
            yield from ollama.chat_stream_full(
                chosen, messages, options=options, tools=tools,
                keep_alive=keep_alive, timeout=timeout)
    except openai_client.OpenAIError as exc:
        raise LLMError(exc.code, exc.detail, OPENAI) from exc
    except anthropic_client.AnthropicError as exc:
        raise LLMError(exc.code, exc.detail, CLAUDE) from exc
    except ollama.OllamaError as exc:
        raise LLMError("ollama_error", str(exc)[:300], OLLAMA) from exc


def supports_tools(which: str | None = None) -> bool:
    """
    Whether this provider is handed MCP tools.

    Ollama always is. The paid providers are too, unless the user turned
    that off: every tool round is a second billed request, and on a long
    research question there can be six of them.
    """
    which = which or provider()
    if which == OLLAMA:
        return True
    return bool(config.get_config().get("cloud_tools_enabled", True))


def chat_once(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    which: str | None = None,
    timeout: float = 300.0,
) -> str:
    parts: list[str] = []
    for chunk in chat_stream(messages, model=model, which=which, timeout=timeout):
        parts.append(chunk.get("message", {}).get("content", ""))
        if chunk.get("done"):
            break
    return "".join(parts)


# --------------------------------------------------------------------------
# vision
# --------------------------------------------------------------------------

def vision_model() -> str:
    cfg = config.get_config()
    if provider() == CLAUDE:
        # Claude takes images on the ordinary chat models, so a separate
        # vision model is only needed if the user names one.
        return (cfg.get("claude_vision_model") or "").strip() or active_model(CLAUDE)
    if provider() == OPENAI:
        # OpenAI's chat models take images directly, so unless the user has
        # named a separate one the ordinary chat model does the job.
        return (cfg.get("openai_vision_model") or "").strip() or active_model(OPENAI)
    return (cfg.get("vision_model") or "").strip()


def vision_ready() -> tuple[bool, str]:
    """(ready, note) — the note tells the user exactly what to do about it."""
    which = provider()
    model = vision_model()
    if which == OPENAI:
        if not openai_client.has_key():
            return False, "No OpenAI API key is stored."
        return True, ""
    if which == CLAUDE:
        if not anthropic_client.has_key():
            return False, "No Anthropic API key is stored."
        return True, ""
    if not model:
        return False, "No vision model is configured."
    if not ollama.has_model(model):
        return False, f"Model not pulled. Run: ollama pull {model}"
    return True, ""

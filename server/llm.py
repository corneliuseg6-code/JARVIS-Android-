"""Which brain is answering: a router over Ollama, OpenAI, Claude and Gemini.

JARVIS can talk to a local Ollama (free, private, offline) or to one of three
paid remote APIs. Everything above this module — the chat endpoint, the tool
loop, the health report, EYES — asks the router rather than a provider.

What the router owns
--------------------
* **Choice.** ``route()`` turns the routing mode (manual / local / cloud /
  auto) into an ordered list of candidates, each with the reason it was
  picked, so every answer can say who answered and why.
* **Gates.** A paid provider is only a candidate when it is switched on, its
  cost notice acknowledged, a key stored, and its budget not reached
  (``usable()``). Nothing here can start spending on its own.
* **Accounting.** Every paid turn's real token counts go to ``usage``; the
  budget is checked before each paid call, not after.
* **One shape.** All four clients yield ``{"type": "token"}`` events and one
  ``{"type": "message"}``, with tool calls in the OpenAI/Ollama shape, so the
  tool loop never knows which provider it is driving.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from . import anthropic_client, config, gemini_client, ollama, openai_client, usage

OLLAMA = "ollama"
OPENAI = "openai"
CLAUDE = "claude"
GEMINI = "gemini"
PROVIDERS = (OLLAMA, OPENAI, CLAUDE, GEMINI)

FALLBACK_MODEL = {
    OLLAMA: "llama3.2:3b",
    OPENAI: "gpt-4o-mini",
    CLAUDE: "claude-opus-5-5",
    GEMINI: "gemini-2.5-flash",
}

LABELS = {OLLAMA: "Ollama", OPENAI: "OpenAI", CLAUDE: "Claude", GEMINI: "Gemini"}

BILLED = {OPENAI, CLAUDE, GEMINI}

CLIENTS = {OPENAI: openai_client, CLAUDE: anthropic_client, GEMINI: gemini_client}

_MODEL_KEY = {OLLAMA: "chat_model", OPENAI: "openai_model", CLAUDE: "claude_model",
              GEMINI: "gemini_model"}

ROUTING_MODES = ("manual", "local", "cloud", "auto")


class LLMError(RuntimeError):
    """A provider failure, carrying a code the frontend knows how to say."""

    def __init__(self, code: str, detail: str = "", provider: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
        self.provider = provider


def provider() -> str:
    """The brain picked by hand (llm_provider), falling back to Ollama."""
    chosen = (config.get_config().get("llm_provider") or OLLAMA).lower()
    return chosen if chosen in PROVIDERS else OLLAMA


def routing_mode() -> str:
    mode = config.get_config().get("routing_mode") or "manual"
    return mode if mode in ROUTING_MODES else "manual"


def active_model(which: str | None = None) -> str:
    which = which or provider()
    value = str(config.get_config().get(_MODEL_KEY.get(which, "chat_model")) or "").strip()
    return value or FALLBACK_MODEL.get(which, FALLBACK_MODEL[OLLAMA])


def history_limit() -> int:
    """How many past messages to resend; on a paid API, a multiplier on the bill."""
    try:
        value = int(config.get_config().get("chat_history_limit") or 20)
    except (TypeError, ValueError):
        value = 20
    return max(2, min(100, value))


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def usable(which: str) -> tuple[bool, str]:
    """
    (may this provider be used right now, and if not, why).

    Configuration only — nothing is contacted. Ollama is always allowed; it
    may still be offline, which the turn itself discovers.
    """
    if which == OLLAMA:
        return True, ""
    if which not in BILLED:
        return False, "unknown provider"
    cfg = config.get_config()
    if not cfg.get(f"{which}_enabled"):
        return False, f"{LABELS[which]} is switched off"
    if not cfg.get(f"{which}_cost_ack"):
        return False, f"{LABELS[which]}'s cost notice is not acknowledged"
    if not CLIENTS[which].has_key():
        return False, f"no {LABELS[which]} API key is stored"
    try:
        usage.check(which)
    except usage.BudgetExceeded as exc:
        return False, exc.detail
    return True, ""


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

#: Signs that a turn is worth a stronger, paid model. Deliberately simple and
#: visible: the reason shown with the answer names which rule matched.
_CODE = re.compile(
    r"```|\btraceback\b|\bstack ?trace\b|\bexception\b|\bsyntaxerror\b|\bdef \w+\(|"
    r"\bfunction\s*\w*\(|\bclass \w+|\b(refactor|debug|compile|regex|sql|python|javascript|"
    r"typescript|kotlin|java|rust|golang|bash|script|code|cod|codul|programm\w*|bug|fix)\b",
    re.IGNORECASE,
)
_DEEP = re.compile(
    r"\b(research|analy[sz]e|analiz\w*|compare|compar\w*|vergleich\w*|investiga\w*|"
    r"step by step|in detail|detailed|detaliat\w*|plan|strategy|strategie|essay|report|"
    r"raport|summari[sz]e|rezum\w*)\b",
    re.IGNORECASE,
)
LONG_TURN = 600


def classify(message: str, *, images: bool = False) -> tuple[str, str]:
    """('simple' | 'complex', the rule that decided)."""
    text = message or ""
    if images:
        return "complex", "it includes an image"
    if _CODE.search(text):
        return "complex", "it looks like a coding task"
    if len(text) > LONG_TURN:
        return "complex", f"it is long ({len(text)} characters)"
    if _DEEP.search(text):
        return "complex", "it asks for analysis or research"
    return "simple", "it is a short everyday request"


def _cloud_order(prefer: str | None = None) -> list[str]:
    order = [p for p in config.get_config().get("cloud_order") or [] if p in BILLED]
    for p in (CLAUDE, OPENAI, GEMINI):
        if p not in order:
            order.append(p)
    if prefer and prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)
    return order


def route(message: str = "", *, images: bool = False, ollama_up: bool | None = None) -> dict[str, Any]:
    """
    Decide who answers. Returns ``{"mode", "candidates": [{provider, model,
    reason}], "skipped": [{provider, reason}]}``.

    Candidates are tried in order; a later one is only used when every
    earlier one failed before producing any output, and the interface is
    told when that happens.
    """
    cfg = config.get_config()
    mode = routing_mode()
    fallback = bool(cfg.get("auto_fallback", True))
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def add(which: str, reason: str) -> None:
        if any(c["provider"] == which for c in candidates):
            return
        ok, why = usable(which)
        if ok:
            candidates.append({"provider": which, "model": active_model(which), "reason": reason})
        else:
            skipped.append({"provider": which, "reason": why})

    if mode == "manual":
        chosen = provider()
        add(chosen, "chosen in AI Connections")
        if fallback and chosen != OLLAMA:
            add(OLLAMA, "fallback: the free local model")
    elif mode == "local":
        add(OLLAMA, "Local mode: nothing leaves the phone")
    elif mode == "cloud":
        for which in _cloud_order():
            add(which, "Cloud mode")
        if fallback:
            add(OLLAMA, "fallback: no cloud brain answered")
    else:  # auto
        kind, why = classify(message, images=images)
        if ollama_up is None:
            ollama_up = ollama.health(timeout=1.5).get("state") == "connected"
        coding = why == "it looks like a coding task"
        if kind == "simple" and ollama_up:
            add(OLLAMA, f"Automatic: local, because {why}")
            for which in _cloud_order():
                add(which, "Automatic: fallback to cloud")
        else:
            reason = f"Automatic: cloud, because {why}" if kind == "complex" \
                else "Automatic: cloud, because the local model is offline"
            for which in _cloud_order(CLAUDE if coding else None):
                add(which, reason)
            add(OLLAMA, "Automatic: no cloud brain is usable, staying local")
    return {"mode": mode, "candidates": candidates, "skipped": skipped}


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def health(timeout: float = 6.0, *, force: bool = False, which: str | None = None) -> dict[str, Any]:
    """
    The state of one provider (default: the hand-picked one).

    Only that provider is contacted: probing a metered API every twelve
    seconds to colour a chip nobody is looking at would be slow and rude.
    """
    which = which or provider()
    model = active_model(which)
    if which in CLIENTS:
        info = CLIENTS[which].health(timeout=timeout, force=force)
        models = info.get("models", [])
        return {
            "provider": which,
            "label": LABELS[which],
            "state": info.get("state", "unknown"),
            "model": model,
            "model_ready": (model in models) if models else info.get("state") == "connected",
            "models": models,
            "url": info.get("url", ""),
            "key_set": info.get("key_set", False),
            "error": info.get("error", ""),
            "detail": info.get("detail", ""),
            "billed": True,
            "routing": routing_mode(),
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
        "key_set": True,
        "error": info.get("error", ""),
        "detail": "",
        "billed": False,
        "routing": routing_mode(),
    }


def models(timeout: float = 8.0, which: str | None = None) -> list[str]:
    which = which or provider()
    if which in CLIENTS:
        return CLIENTS[which].list_models(timeout=timeout)
    return ollama.list_models(timeout=timeout)


# --------------------------------------------------------------------------
# chat
# --------------------------------------------------------------------------

def ollama_runtime() -> tuple[dict[str, Any], str]:
    """(options, keep_alive) for an Ollama request, from the user's settings."""
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
    """Drop the oldest turns until the conversation fits a character budget."""
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


def _for_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip what only the paid clients understand."""
    out = []
    for m in messages:
        clean = {k: v for k, v in m.items() if k not in ("_native", "tool_call_id", "is_error")}
        out.append(clean)
    return out


def _max_tokens() -> int:
    try:
        return int(config.get_config().get("max_output_tokens") or 4096)
    except (TypeError, ValueError):
        return 4096


def chat_stream_full(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    timeout: float = 300.0,
    which: str | None = None,
    purpose: str = "chat",
) -> Iterator[dict[str, Any]]:
    """
    Stream one turn on one provider and assemble the complete message.

    Yields ``{"type": "token"}`` as text arrives and one ``{"type":
    "message", "message", "usage"}`` at the end, with `tool_calls` when the
    model asked for tools. A paid provider is budget-checked before the call
    and its reported usage recorded after it.
    """
    which = which or provider()
    chosen = (model or "").strip() or active_model(which)
    try:
        if which == OLLAMA:
            options, keep_alive = ollama_runtime()
            for event in ollama.chat_stream_full(
                    chosen, _for_ollama(messages), options=options, tools=tools,
                    keep_alive=keep_alive, timeout=timeout):
                if event["type"] == "message":
                    event = {**event, "usage": {"input": 0, "output": 0}, "provider": OLLAMA}
                yield event
            return

        client = CLIENTS.get(which)
        if client is None:
            raise LLMError("unknown_provider", which, which)
        try:
            usage.check(which)
        except usage.BudgetExceeded as exc:
            raise LLMError("budget_exceeded", exc.detail, which) from exc

        finished = started = False
        try:
            for event in client.chat_stream_full(chosen, messages, tools=tools,
                                                 max_tokens=_max_tokens(), timeout=timeout):
                started = True
                if event["type"] == "message":
                    spent = event.get("usage") or {}
                    entry = usage.record(which, chosen, spent.get("input", 0),
                                         spent.get("output", 0), purpose=purpose)
                    finished = True
                    event = {**event, "provider": which, "cost": entry}
                yield event
        finally:
            if started and not finished:
                # The stream broke or was abandoned before usage arrived.
                # Record the call so it is visible, with zero known tokens.
                usage.record(which, chosen, 0, 0, purpose=purpose, complete=False)
    except openai_client.OpenAIError as exc:
        raise LLMError(exc.code, exc.detail, OPENAI) from exc
    except anthropic_client.AnthropicError as exc:
        raise LLMError(exc.code, exc.detail, CLAUDE) from exc
    except gemini_client.GeminiError as exc:
        raise LLMError(exc.code, exc.detail, GEMINI) from exc
    except ollama.OllamaError as exc:
        raise LLMError("ollama_error", str(exc)[:300], OLLAMA) from exc


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 300.0,
    which: str | None = None,
    purpose: str = "chat",
) -> Iterator[dict[str, Any]]:
    """Stream a plain answer, in the Ollama chunk shape."""
    for event in chat_stream_full(messages, model=model, timeout=timeout, which=which,
                                  purpose=purpose):
        if event["type"] == "token":
            yield {"message": {"content": event["text"]}, "done": False}
        elif event["type"] == "message":
            yield {"message": {"content": ""}, "done": True, "usage": event.get("usage"),
                   "cost": event.get("cost")}


def chat_once_full(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    timeout: float = 300.0,
    which: str | None = None,
) -> dict[str, Any]:
    """One turn, returning the whole assistant message."""
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    for event in chat_stream_full(messages, model=model, tools=tools, timeout=timeout, which=which):
        if event["type"] == "message":
            message = event["message"]
    return message


def supports_tools(which: str | None = None) -> bool:
    """Every provider can be handed MCP tools now; Ollama depends on the model."""
    return (which or provider()) in PROVIDERS


def chat_once(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 300.0,
    which: str | None = None,
    purpose: str = "chat",
) -> str:
    parts: list[str] = []
    for chunk in chat_stream(messages, model=model, timeout=timeout, which=which, purpose=purpose):
        parts.append(chunk.get("message", {}).get("content", ""))
        if chunk.get("done"):
            break
    return "".join(parts)


# --------------------------------------------------------------------------
# vision
# --------------------------------------------------------------------------

def vision_model() -> str:
    cfg = config.get_config()
    which = provider()
    if which == CLAUDE:
        return (cfg.get("claude_vision_model") or "").strip() or active_model(CLAUDE)
    if which == OPENAI:
        return (cfg.get("openai_vision_model") or "").strip() or active_model(OPENAI)
    if which == GEMINI:
        return active_model(GEMINI)       # Gemini chat models take images
    return (cfg.get("vision_model") or "").strip()


def vision_ready() -> tuple[bool, str]:
    """(ready, note) — the note tells the user exactly what to do about it."""
    which = provider()
    model = vision_model()
    if which in CLIENTS:
        ok, why = usable(which)
        return (True, "") if ok else (False, why[:1].upper() + why[1:] + ".")
    if not model:
        return False, "No vision model is configured."
    if not ollama.has_model(model):
        return False, f"Model not pulled. Run: ollama pull {model}"
    return True, ""

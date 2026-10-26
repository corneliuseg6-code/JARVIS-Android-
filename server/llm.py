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


def provider() -> str:
    """The configured provider, falling back to the free local one."""
    chosen = (config.get_config().get("llm_provider") or OLLAMA).lower()
    return chosen if chosen in PROVIDERS else OLLAMA


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
    """
    which = provider()
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

def chat_stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """Stream an answer from the active provider, in the Ollama chunk shape."""
    which = provider()
    chosen = (model or "").strip() or active_model(which)
    try:
        if which == OPENAI:
            yield from openai_client.chat_stream(chosen, messages, timeout=timeout)
        elif which == CLAUDE:
            yield from anthropic_client.chat_stream(chosen, messages, timeout=timeout)
        else:
            yield from ollama.chat_stream(chosen, messages, timeout=timeout)
    except openai_client.OpenAIError as exc:
        raise LLMError(exc.code, exc.detail, OPENAI) from exc
    except anthropic_client.AnthropicError as exc:
        raise LLMError(exc.code, exc.detail, CLAUDE) from exc
    except ollama.OllamaError as exc:
        raise LLMError("ollama_error", str(exc)[:300], OLLAMA) from exc


def chat_once(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    timeout: float = 300.0,
) -> str:
    parts: list[str] = []
    for chunk in chat_stream(messages, model=model, timeout=timeout):
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

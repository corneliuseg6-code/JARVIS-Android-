"""User configuration (non-secret) and the secrets store (never leaves the host).

config.json  -> safe to send to the browser.
secrets.json -> API keys / tokens. Readable only by the backend, chmod 0600,
                and exposed to the frontend solely as the boolean "is set".
"""

import re
import threading
from typing import Any

from . import paths, store

SUPPORTED_UI_LANGUAGES = ("ro", "en", "fr", "de", "es", "it")

DEFAULTS: dict[str, Any] = {
    "version": 2,
    # --- interface -------------------------------------------------------
    "ui_language": "ro",
    "conversation_language": "ro",
    "conversation_follows_ui": True,
    "avatar_mode": "rings",          # "rings" | "face"  (cube removed in V2)
    "theme_accent": "cyan",
    "reduced_motion": False,
    "magnifier_enabled": True,
    "hud_label": "J.A.R.V.I.S.",     # the text in the middle of the rings
    # Two user-defined top-bar shortcuts. OpenAI and GitHub are fixed in the
    # frontend; these are the other two. An empty url means "not set up yet",
    # which the interface shows as a "+" rather than a dead button.
    "shortcut1_name": "",
    "shortcut1_url": "",
    "shortcut2_name": "",
    "shortcut2_url": "",
    # --- assistant behaviour ---------------------------------------------
    "wake_word": "Jarvis",           # REFLEX acts only on a phrase containing it
    "focus_minutes": 30,             # length of a FOCUS session
    # --- privacy ---------------------------------------------------------
    # The microphone is ALWAYS muted on start. This value is deliberately not
    # persisted as "on": see get_config(), which forces it back to True.
    "mic_muted": True,
    "power_state": "active",         # "active" | "standby"
    "save_captures": False,          # camera stills are never written unless true
    "vision_local_only": True,
    # --- voice -----------------------------------------------------------
    "tts_enabled": True,
    "voice_by_language": {
        "ro": "ro_RO-mihai-medium",
        "en": "en_GB-alan-medium",
        "fr": "",
        "de": "",
        "es": "",
        "it": "",
    },
    "voice_override": "",            # manual pick; empty = follow language
    "tts_speed": 1.0,
    # --- models ----------------------------------------------------------
    # Which brain answers. "ollama" is free, local and offline; "openai" is
    # a paid remote API. Switching to a billed provider needs an explicit
    # acknowledgement, enforced in update_config() rather than only in the UI.
    "llm_provider": "ollama",            # "ollama" | "openai" | "claude"
    "openai_enabled": False,             # the connection may be used at all
    "openai_model": "gpt-4o-mini",
    "openai_vision_model": "",           # empty = use the chat model
    "openai_base_url": "https://api.openai.com/v1",
    "openai_cost_ack": False,            # the user has seen the cost notice
    # --- Claude (Anthropic API) ------------------------------------------
    "claude_enabled": False,
    "claude_model": "claude-opus-5-5",
    "claude_vision_model": "",           # empty = use the chat model
    "anthropic_base_url": "https://api.anthropic.com",
    "claude_cost_ack": False,
    "claude_effort": "low",              # low | medium | high — billed thinking
    # --- Claude Code (the local CLI) --------------------------------------
    # Off by default, and useless until a working directory is chosen. Edits
    # are a second switch on top of that; see server/claude_code.py.
    "claude_code_enabled": False,
    "claude_code_allow_edits": False,
    "claude_code_workdir": "",
    "claude_code_model": "",             # empty = whatever the CLI defaults to
    "claude_code_max_turns": 6,
    # --- MCP tools in chat ------------------------------------------------
    # Off by default: a tool that changes something waits for the user, one
    # call at a time. Turning this on skips that per-call approval.
    "mcp_allow_writes": False,
    # Every turn resends this many past messages. On a metered provider that
    # window is a direct multiplier on the bill, so it is configurable.
    "chat_history_limit": 20,
    # A second brake on the window, in characters. A phone model re-reads the
    # whole window every turn, and twenty long messages cost far more than
    # twenty short ones, which a message count cannot see.
    "chat_char_budget": 6000,
    # How long Ollama should keep the model in memory between messages.
    # Ollama's own default is five minutes, after which the next message pays
    # a multi-gigabyte reload before its first token.
    "ollama_keep_alive": "30m",
    # 0 means "don't send it", leaving Ollama's own default. Raising num_ctx
    # costs memory, which on a phone is the scarce thing, so it is opt-in.
    "ollama_num_ctx": 0,
    # -1 means no limit. A positive value caps answer length, which bounds
    # the worst case on a slow device at the price of cutting long answers.
    "ollama_num_predict": -1,
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "llama3.2:3b",
    "vision_model": "qwen3-vl:2b-instruct",
    "vision_enabled": False,         # opt-in: loads a second large model
    "system_prompt": "",
}

_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None


def _merge(defaults: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    out = dict(defaults)
    for key, value in stored.items():
        if key in defaults and isinstance(defaults[key], dict) and isinstance(value, dict):
            merged = dict(defaults[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def get_config(*, refresh: bool = False) -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is None or refresh:
            stored = store.read_json(paths.CONFIG_FILE, {}) or {}
            cfg = _merge(DEFAULTS, stored)
            # Privacy invariants, enforced on every load:
            cfg["mic_muted"] = True          # never auto-enable the microphone
            cfg["power_state"] = "active"    # standby is a runtime state, not sticky
            if cfg.get("ui_language") not in SUPPORTED_UI_LANGUAGES:
                cfg["ui_language"] = "ro"
            if cfg.get("conversation_language") not in SUPPORTED_UI_LANGUAGES:
                cfg["conversation_language"] = cfg["ui_language"]
            if cfg.get("avatar_mode") not in ("rings", "face"):
                cfg["avatar_mode"] = "rings"
            _clamp_v21(cfg)
            _CACHE = cfg
        return dict(_CACHE)


def _clamp_v21(cfg: dict[str, Any]) -> None:
    """Keep the V2.1 interface keys inside what the frontend can render."""
    label = str(cfg.get("hud_label") or "").strip()
    cfg["hud_label"] = label[:16] or DEFAULTS["hud_label"]
    wake = str(cfg.get("wake_word") or "").strip()
    cfg["wake_word"] = wake[:24] or DEFAULTS["wake_word"]

    # Shortcuts: only ever hand the browser an http(s) address. Anything else
    # — javascript:, data:, a typo — is dropped rather than rendered as a
    # link, and the slot falls back to its "+" state.
    for n in (1, 2):
        name = str(cfg.get(f"shortcut{n}_name") or "").strip()
        cfg[f"shortcut{n}_name"] = name[:18]
        url = str(cfg.get(f"shortcut{n}_url") or "").strip()
        cfg[f"shortcut{n}_url"] = url[:400] if url.startswith(("http://", "https://")) else ""
    try:
        minutes = int(cfg.get("focus_minutes") or DEFAULTS["focus_minutes"])
    except (TypeError, ValueError):
        minutes = DEFAULTS["focus_minutes"]
    cfg["focus_minutes"] = max(1, min(240, minutes))

    # --- provider --------------------------------------------------------
    if cfg.get("llm_provider") not in ("ollama", "openai", "claude"):
        cfg["llm_provider"] = "ollama"
    # A paid provider cannot be selected until it has been switched on AND its
    # cost notice acknowledged. Enforcing it here rather than only in the UI
    # means a hand-edited config.json, or a POST that skipped the interface,
    # still cannot start spending money. Ollama is always the fallback.
    if cfg.get("llm_provider") == "openai" and not (
        cfg.get("openai_enabled") and cfg.get("openai_cost_ack")
    ):
        cfg["llm_provider"] = "ollama"
    if cfg.get("llm_provider") == "claude" and not (
        cfg.get("claude_enabled") and cfg.get("claude_cost_ack")
    ):
        cfg["llm_provider"] = "ollama"
    base = str(cfg.get("openai_base_url") or "").strip()
    cfg["openai_base_url"] = base if base.startswith(("http://", "https://")) \
        else DEFAULTS["openai_base_url"]
    anthropic_base = str(cfg.get("anthropic_base_url") or "").strip()
    cfg["anthropic_base_url"] = anthropic_base if anthropic_base.startswith(("http://", "https://")) \
        else DEFAULTS["anthropic_base_url"]
    if str(cfg.get("claude_effort") or "").lower() not in ("low", "medium", "high"):
        cfg["claude_effort"] = DEFAULTS["claude_effort"]

    # --- Claude Code ------------------------------------------------------
    # Edits cannot be allowed on a connection that is off, and neither switch
    # means anything without a working directory.
    if not cfg.get("claude_code_enabled"):
        cfg["claude_code_allow_edits"] = False
    try:
        turns = int(cfg.get("claude_code_max_turns") or DEFAULTS["claude_code_max_turns"])
    except (TypeError, ValueError):
        turns = DEFAULTS["claude_code_max_turns"]
    cfg["claude_code_max_turns"] = max(1, min(20, turns))
    try:
        window = int(cfg.get("chat_history_limit") or DEFAULTS["chat_history_limit"])
    except (TypeError, ValueError):
        window = DEFAULTS["chat_history_limit"]
    cfg["chat_history_limit"] = max(2, min(100, window))
    try:
        budget = int(cfg.get("chat_char_budget") or DEFAULTS["chat_char_budget"])
    except (TypeError, ValueError):
        budget = DEFAULTS["chat_char_budget"]
    cfg["chat_char_budget"] = max(500, min(200_000, budget))
    try:
        ctx = int(cfg.get("ollama_num_ctx") or 0)
    except (TypeError, ValueError):
        ctx = 0
    cfg["ollama_num_ctx"] = 0 if ctx <= 0 else max(512, min(131_072, ctx))
    try:
        predict = int(cfg.get("ollama_num_predict"))
    except (TypeError, ValueError):
        predict = DEFAULTS["ollama_num_predict"]
    cfg["ollama_num_predict"] = -1 if predict < 0 else max(16, min(8192, predict))
    keep = str(cfg.get("ollama_keep_alive") or "").strip()
    # Ollama takes a duration string ("30m", "1h") or a number of seconds.
    # Anything else would be rejected at request time, far from here.
    if not re.fullmatch(r"-?\d+(\.\d+)?(ms|s|m|h)?", keep):
        keep = DEFAULTS["ollama_keep_alive"]
    cfg["ollama_keep_alive"] = keep


def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a shallow patch of known keys and persist it."""
    global _CACHE
    with _LOCK:
        cfg = get_config()
        for key, value in patch.items():
            if key not in DEFAULTS:
                continue  # ignore unknown keys instead of growing the file
            if isinstance(DEFAULTS[key], dict) and isinstance(value, dict):
                merged = dict(cfg.get(key) or {})
                merged.update(value)
                cfg[key] = merged
            else:
                cfg[key] = value
        if cfg.get("ui_language") not in SUPPORTED_UI_LANGUAGES:
            cfg["ui_language"] = "ro"
        if cfg.get("conversation_language") not in SUPPORTED_UI_LANGUAGES:
            cfg["conversation_language"] = cfg["ui_language"]
        if cfg.get("avatar_mode") not in ("rings", "face"):
            cfg["avatar_mode"] = "rings"
        if cfg.get("conversation_follows_ui"):
            cfg["conversation_language"] = cfg["ui_language"]
        _clamp_v21(cfg)
        # Not persisted as enabled — the mic is muted at every start.
        persisted = dict(cfg)
        persisted["mic_muted"] = True
        persisted["power_state"] = "active"
        store.write_json(paths.CONFIG_FILE, persisted)
        _CACHE = cfg
        return dict(cfg)


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------

def _secrets() -> dict[str, str]:
    return store.read_json(paths.SECRETS_FILE, {}) or {}


def set_secret(name: str, value: str) -> None:
    data = _secrets()
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    store.write_json(paths.SECRETS_FILE, data, secure=True)


def get_secret(name: str) -> str | None:
    """Backend-only. Never serialise the result into an HTTP response."""
    return _secrets().get(name)


def secret_names() -> list[str]:
    return sorted(_secrets().keys())


def has_secret(name: str) -> bool:
    return bool(_secrets().get(name))


def delete_secrets(prefix: str) -> None:
    data = _secrets()
    remaining = {k: v for k, v in data.items() if not k.startswith(prefix)}
    if remaining != data:
        store.write_json(paths.SECRETS_FILE, remaining, secure=True)

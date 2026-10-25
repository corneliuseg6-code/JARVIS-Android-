"""User configuration (non-secret) and the secrets store (never leaves the host).

config.json  -> safe to send to the browser.
secrets.json -> API keys / tokens. Readable only by the backend, chmod 0600,
                and exposed to the frontend solely as the boolean "is set".
"""

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
            _CACHE = cfg
        return dict(_CACHE)


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

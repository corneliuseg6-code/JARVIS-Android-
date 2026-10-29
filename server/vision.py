"""EYES: local image understanding through an Ollama vision model.

Nothing here runs unless the user turns vision on, and no frame is written to
disk unless save_captures is explicitly enabled. Images are passed to Ollama
in-memory as base64 and dropped afterwards.
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any

from . import config, llm, paths

MAX_IMAGE_BYTES = 6 * 1024 * 1024

PROMPTS = {
    "ro": "Descrie pe scurt ce vezi în imagine.",
    "en": "Briefly describe what you see in this image.",
    "fr": "Décris brièvement ce que tu vois sur cette image.",
    "de": "Beschreibe kurz, was du auf diesem Bild siehst.",
    "es": "Describe brevemente lo que ves en esta imagen.",
    "it": "Descrivi brevemente ciò che vedi in questa immagine.",
}


def status() -> dict[str, Any]:
    cfg = config.get_config()
    provider = llm.provider()
    model = llm.vision_model()
    ready, note = llm.vision_ready()
    return {
        "enabled": bool(cfg.get("vision_enabled")),
        "provider": provider,
        "model": model,
        # Kept under its old name so the frontend's "model is not installed"
        # message keeps working for both providers.
        "model_installed": ready,
        "ollama": llm.health().get("state") if provider == llm.OLLAMA else "disconnected",
        "local_only": bool(cfg.get("vision_local_only", True)),
        "save_captures": bool(cfg.get("save_captures")),
        # On a paid provider, sending a frame costs money — say so plainly.
        "billed": provider in llm.BILLED,
        "note": note,
    }


def _decode(data_url: str) -> bytes:
    if "," in data_url and data_url.strip().startswith("data:"):
        data_url = data_url.split(",", 1)[1]
    try:
        raw = base64.b64decode(data_url, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid image data") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image too large")
    return raw


def describe(image_data_url: str, language: str = "en", prompt: str = "") -> dict[str, Any]:
    cfg = config.get_config()
    if not cfg.get("vision_enabled"):
        return {"ok": False, "error": "vision_disabled"}
    info = status()
    if not info["model_installed"]:
        return {"ok": False, "error": "model_missing", "detail": info["note"]}

    raw = _decode(image_data_url)
    if cfg.get("save_captures"):
        paths.CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        (paths.CAPTURES_DIR / f"capture-{int(time.time())}.jpg").write_bytes(raw)

    messages = [
        {
            "role": "user",
            "content": prompt or PROMPTS.get(language, PROMPTS["en"]),
            "images": [base64.b64encode(raw).decode("ascii")],
        }
    ]
    try:
        text = llm.chat_once(messages, model=info["model"], timeout=180.0, purpose="vision")
    except llm.LLMError as exc:
        return {"ok": False, "error": exc.code, "provider": exc.provider, "detail": exc.detail[:300]}
    return {"ok": True, "text": text.strip(), "provider": info["provider"]}

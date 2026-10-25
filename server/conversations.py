"""Conversation persistence.

One JSON file per conversation under ~/.jarvis/conversations/. Existing files
from an earlier JARVIS install are read as-is; unknown fields are preserved on
rewrite so an upgrade never drops data.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from . import paths, store

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
MAX_MESSAGES = 400


def _path(conv_id: str):
    if not _SAFE_ID.match(conv_id):
        raise ValueError("invalid conversation id")
    return paths.CONVERSATIONS_DIR / f"{conv_id}.json"


def list_conversations() -> list[dict[str, Any]]:
    paths.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for file in paths.CONVERSATIONS_DIR.glob("*.json"):
        data = store.read_json(file, None)
        if not isinstance(data, dict):
            continue
        messages = data.get("messages") or []
        out.append(
            {
                "id": data.get("id", file.stem),
                "title": data.get("title") or _derive_title(messages),
                "updated_at": data.get("updated_at", 0),
                "language": data.get("language", ""),
                "message_count": len(messages),
            }
        )
    out.sort(key=lambda c: c["updated_at"], reverse=True)
    return out


def _derive_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user" and msg.get("content"):
            return str(msg["content"])[:48]
    return ""


def load(conv_id: str) -> dict[str, Any]:
    data = store.read_json(_path(conv_id), None)
    if not isinstance(data, dict):
        return {"id": conv_id, "title": "", "messages": [], "updated_at": 0, "language": ""}
    data.setdefault("messages", [])
    data.setdefault("id", conv_id)
    return data


def create(language: str = "") -> dict[str, Any]:
    conv_id = uuid.uuid4().hex[:16]
    data = {
        "id": conv_id,
        "title": "",
        "language": language,
        "messages": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    store.write_json(_path(conv_id), data)
    return data


def append(conv_id: str, role: str, content: str, *, language: str = "") -> dict[str, Any]:
    data = load(conv_id)
    data["messages"].append({"role": role, "content": content, "ts": int(time.time())})
    del data["messages"][:-MAX_MESSAGES]
    data["updated_at"] = int(time.time())
    if language:
        data["language"] = language
    if not data.get("title"):
        data["title"] = _derive_title(data["messages"])
    store.write_json(_path(conv_id), data)
    return data


def delete(conv_id: str) -> bool:
    try:
        _path(conv_id).unlink()
        return True
    except (OSError, ValueError):
        return False

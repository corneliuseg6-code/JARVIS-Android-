"""Keep the images, audio and video a tool returns, as real files in the workspace.

An MCP tool hands media back inline (``{"type": "image", "data": base64,
"mimeType": …}``) or as an embedded resource with a ``blob``. Either way the
bytes are written to ``workspace/Results/Media/`` so the user owns a copy
even after the provider's link expires, and the chat can show it through
``/api/workspace/media``. Links (``resource_link``) are left as links:
fetching them would be a download the user did not ask for.
"""

from __future__ import annotations

import base64
import re
import time
from typing import Any

from . import workspace

MEDIA_DIR = "Results/Media"

#: What may be saved and served. Anything else is refused rather than served
#: with a guessed type: an HTML "image" served from our origin would run.
TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "video/mp4": ".mp4", "video/webm": ".webm",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/ogg": ".ogg",
}
EXT_TYPES = {ext: mime for mime, ext in TYPES.items()}
EXT_TYPES[".jpeg"] = "image/jpeg"

MAX_BYTES = 60_000_000


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text or "media").strip("-").lower()[:40] or "media"


def _blocks(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    found = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("image", "audio", "video") and block.get("data"):
            found.append({"mime": str(block.get("mimeType") or ""), "data": block["data"]})
        elif block.get("type") == "resource" and isinstance(block.get("resource"), dict):
            res = block["resource"]
            if res.get("blob"):
                found.append({"mime": str(res.get("mimeType") or ""), "data": res["blob"]})
    return found


def keep(result: Any, *, tool: str = "", connection: str = "") -> list[dict[str, Any]]:
    """Save every inline media block in a tool result; return what was saved."""
    saved: list[dict[str, Any]] = []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for index, block in enumerate(_blocks(result)):
        mime = block["mime"].split(";")[0].strip().lower()
        ext = TYPES.get(mime)
        if not ext:
            continue
        try:
            raw = base64.b64decode(block["data"], validate=False)
        except (ValueError, TypeError):
            continue
        if not raw or len(raw) > MAX_BYTES:
            continue
        name = f"{stamp}-{_slug(connection)}-{_slug(tool)}-{index + 1}{ext}"
        relative = f"{MEDIA_DIR}/{name}"
        target = workspace.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        saved.append({
            "kind": mime.split("/")[0],
            "mime": mime,
            "path": relative,
            "bytes": len(raw),
            "url": "/api/workspace/media?path=" + relative,
        })
    if saved:
        try:
            workspace.journal(
                f"{connection or 'A tool'} `{tool}` returned {len(saved)} file(s): "
                + ", ".join(m["path"] for m in saved),
                kind="media",
            )
        except Exception:  # noqa: BLE001 - the journal is a courtesy
            pass
    return saved


def read(relative: str) -> tuple[bytes, str]:
    """Bytes and content type for a saved media file, refusing anything else."""
    target = workspace.resolve(relative)
    ext = target.suffix.lower()
    mime = EXT_TYPES.get(ext)
    if not mime:
        raise workspace.WorkspaceError("not a media file JARVIS can show")
    if not target.is_file():
        raise workspace.WorkspaceError("no such file")
    if target.stat().st_size > MAX_BYTES:
        raise workspace.WorkspaceError("file too large to show")
    return target.read_bytes(), mime

#!/usr/bin/env python3
"""A real MCP filesystem server — "JARVIS Files" — over stdio.

Small, dependency-free, and confined to one root, so it can be pointed at a
projects folder on a phone without handing the model the whole device.

Register it in the Armory as:

    name:      JARVIS Files
    transport: stdio
    command:   python3 <project>/tools/fs_mcp_server.py <root>

The root may also come from JARVIS_FILES_ROOT. Every path is resolved and
checked against it, so `..` cannot walk out.

Tools: list_directory, read_file, get_file_info (read-only) and write_file,
create_directory (mutating — JARVIS holds those for approval).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(
    sys.argv[1] if len(sys.argv) > 1 else os.environ.get("JARVIS_FILES_ROOT", ".")
).expanduser().resolve()

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "list_directory",
        "description": (
            "List the entries in a directory under the server's root. "
            "Returns one line per entry, marked [DIR] or [FILE] with its size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory relative to the root. Omit or '.' for the root itself.",
                },
            },
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file under the server's root.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "get_file_info",
        "description": "Size, kind and modification time of one entry.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a text file under the server's root.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "create_directory",
        "description": "Create a directory under the server's root.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
]


class ToolError(RuntimeError):
    pass


def resolve(raw: str) -> Path:
    """Resolve a requested path and refuse anything outside the root."""
    candidate = (ROOT / (raw or ".")).expanduser()
    try:
        target = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"bad path: {exc}") from exc
    if target != ROOT and ROOT not in target.parents:
        raise ToolError(f"{raw!r} is outside the served root")
    return target


def run_tool(name: str, args: dict) -> str:
    if name == "list_directory":
        target = resolve(args.get("path") or ".")
        if not target.is_dir():
            raise ToolError(f"{args.get('path') or '.'} is not a directory")
        rows = []
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.is_dir():
                rows.append(f"[DIR]  {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = -1
                rows.append(f"[FILE] {entry.name} ({size} bytes)")
        header = f"{len(rows)} entries in {target.relative_to(ROOT) or '.'}"
        return header + ("\n" + "\n".join(rows) if rows else "\n(empty)")

    if name == "read_file":
        target = resolve(args.get("path", ""))
        if not target.is_file():
            raise ToolError(f"{args.get('path')} is not a file")
        return target.read_text("utf-8", "replace")[:40000]

    if name == "get_file_info":
        target = resolve(args.get("path", ""))
        if not target.exists():
            raise ToolError(f"{args.get('path')} does not exist")
        st = target.stat()
        kind = "directory" if target.is_dir() else "file"
        return json.dumps({"kind": kind, "size": st.st_size, "modified": int(st.st_mtime)})

    if name == "write_file":
        target = resolve(args.get("path", ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        target.write_text(content, "utf-8")
        return f"wrote {len(content)} characters to {target.relative_to(ROOT)}"

    if name == "create_directory":
        target = resolve(args.get("path", ""))
        target.mkdir(parents=True, exist_ok=True)
        return f"created {target.relative_to(ROOT)}"

    raise ToolError(f"unknown tool {name}")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> int:
    if not ROOT.is_dir():
        print(f"root {ROOT} is not a directory", file=sys.stderr)
        return 1
    print(f"JARVIS Files serving {ROOT}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        mid = msg.get("id")

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "JARVIS Files", "version": "1.0.0"},
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                text = run_tool(name, args)
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": text}],
                }})
            except ToolError as exc:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                }})
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                }})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"method not found: {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

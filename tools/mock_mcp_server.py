#!/usr/bin/env python3
"""A minimal MCP stdio server used to verify the Tool Armory end to end.

Run it through the Armory with:
    transport: stdio
    command:   python3 <project>/tools/mock_mcp_server.py

If a credential name is configured, the server refuses to initialise unless
the matching environment variable arrives — which is how the credential
injection path gets tested without a real API key.
"""

import json
import os
import sys

REQUIRED_ENV = os.environ.get("MOCK_MCP_REQUIRE", "")

TOOLS = [
    {
        "name": "echo",
        "description": "Return the text it was given.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "clock",
        "description": "Return the server's current time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            if REQUIRED_ENV and not os.environ.get(REQUIRED_ENV):
                send({"jsonrpc": "2.0", "id": msg_id,
                      "error": {"code": -32000, "message": f"missing {REQUIRED_ENV}"}})
                continue
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "mock-mcp", "version": "1.0.0"},
                },
            })
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                text = str(args.get("text", ""))
            elif name == "clock":
                import datetime
                text = datetime.datetime.now().isoformat(timespec="seconds")
            else:
                send({"jsonrpc": "2.0", "id": msg_id,
                      "error": {"code": -32601, "message": f"unknown tool {name}"}})
                continue
            send({"jsonrpc": "2.0", "id": msg_id,
                  "result": {"content": [{"type": "text", "text": text}], "isError": False}})
        elif msg_id is not None:
            send({"jsonrpc": "2.0", "id": msg_id,
                  "error": {"code": -32601, "message": f"unknown method {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The chat tool loop: let the model actually use the Armory's MCP tools.

The Armory could already connect to an MCP server, list its tools and call
them — but nothing joined that to the conversation. The model was never told
the tools existed, and nothing watched its replies for a request to use one.
This module is that join.

One round looks like:

    1. gather the tools every *connected* server exposes;
    2. ask the model, handing it those tools;
    3. if it asked for one, run it for real through ``armory.call_tool`` and
       append the result as a ``tool`` message;
    4. go back to 2, until the model answers in words or the round limit is hit.

Two rules this file exists to keep
----------------------------------
**Never claim a tool ran unless it ran.** Every `tool_result` event carries
what `armory.call_tool` actually returned, including its failures. A tool that
was refused, errored or was never reached says so in the transcript the model
sees, so the model cannot report success over a failure it was told about.

**Writes need the user.** Tools are classified read-only or mutating. A
read-only tool runs straight away. A mutating one stops the loop, and nothing
happens until the user approves that exact call — same arguments, same tool.
Approval is per call and is consumed by it.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Any, Iterator

from . import armory, config

#: How many tool rounds one message may take before we stop. A model that
#: loops forever is a real failure mode; this bounds it.
MAX_ROUNDS = 6

#: How long a pending approval is kept before it expires.
PENDING_TTL = 900.0

#: Tool names that only read. Everything else is treated as mutating, which is
#: the safe direction to be wrong in: a read wrongly held for approval is an
#: inconvenience, a write wrongly allowed is not.
READ_ONLY_HINTS = (
    "list", "read", "get", "search", "find", "stat", "info", "tree", "glob",
    "query", "fetch", "describe", "show", "view", "head", "tail", "count",
    "exists", "diff", "status", "echo", "clock", "ping",
)

#: …except these, which read like reads but are not.
MUTATING_OVERRIDES = ("get_and_delete", "read_and_clear", "search_replace")

#: Verbs that unambiguously change something. A server claiming one of these
#: is read-only is not believed — see is_mutating.
WRITE_VERBS = (
    "write", "create", "delete", "remove", "update", "set", "put", "post",
    "patch", "move", "rename", "copy", "append", "insert", "edit", "save",
    "upload", "download", "install", "uninstall", "exec", "execute", "run",
    "shell", "command", "send", "publish", "deploy", "push", "commit",
    "merge", "kill", "stop", "start", "restart", "drop", "truncate", "clear",
    "reset", "revoke", "grant", "pay", "purchase", "order", "book",
)


class ToolChatError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------

def _safe_name(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)[:48] or "tool"


def catalogue() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    (tool definitions for the model, lookup from the name we gave it).

    Two servers may both expose ``list_directory``, so the name handed to the
    model is prefixed with the connection. The lookup carries the real name
    and which connection to call it on.
    """
    definitions: list[dict[str, Any]] = []
    lookup: dict[str, dict[str, Any]] = {}
    for tool in armory.all_tools():
        real = str(tool.get("name") or "").strip()
        conn_id = str(tool.get("connection_id") or "")
        if not real or not conn_id:
            continue
        exposed = f"{_safe_name(conn_id)[:12]}__{_safe_name(real)}"
        schema = tool.get("inputSchema") or tool.get("input_schema") or {
            "type": "object", "properties": {},
        }
        definitions.append({
            "type": "function",
            "function": {
                "name": exposed,
                "description": str(tool.get("description") or real)[:900],
                "parameters": schema,
            },
        })
        lookup[exposed] = {
            "connection_id": conn_id,
            "tool": real,
            "writes": is_mutating(real, tool.get("annotations")),
        }
    return definitions, lookup


def is_mutating(tool_name: str, annotations: dict[str, Any] | None = None) -> bool:
    """
    Does this tool change something? Unknown verbs count as yes.

    The name is read first, because it is the one thing we can judge for
    ourselves. A server's behaviour hints are only consulted for names the
    verb list cannot place — ``web_search`` reads, but "web" is not a verb, so
    without the hint it would sit behind an approval prompt forever.

    The hints are deliberately not trusted both ways. ``readOnlyHint: true``
    on a tool called ``delete_everything`` is ignored: the MCP spec says
    annotations are hints and may not describe a tool faithfully, so a
    compromised or sloppy server must not be able to talk its way past the
    approval gate. Hints are believed when they say a tool writes, and only
    believed that it reads when the name gives us nothing either way.
    """
    name = (tool_name or "").strip().lower()
    if not name:
        return True
    if any(bad in name for bad in MUTATING_OVERRIDES):
        return True

    hints = annotations if isinstance(annotations, dict) else {}
    if hints.get("destructiveHint") is True or hints.get("readOnlyHint") is False:
        return True

    parts = re.split(r"[_\-/.]", name)
    head = parts[0]
    if any(part in WRITE_VERBS for part in parts):
        return True              # a write verb anywhere wins over any hint
    if head in READ_ONLY_HINTS:
        return False
    if hints.get("readOnlyHint") is True:
        return False             # the name told us nothing; take the server's word
    return True


#: Appended to the system message whenever the catalogue holds a tool that can
#: reach the web. A model handed a search tool will happily answer from memory
#: and then cite nothing; being asked for the URL is what turns a tool result
#: into something the user can check.
RESEARCH_GUIDANCE = (
    "\n\nYou can reach the live web through your tools. Search or read a page "
    "whenever the answer depends on current facts — news, prices, versions, "
    "dates, anything that may have changed — rather than answering from "
    "memory. When you use a result, name the page you took it from and give "
    "its full URL in your answer, one per line at the end under 'Sources:'. "
    "If a search returns nothing useful, say so instead of filling the gap."
)

#: Names that mean a tool can reach the web.
_WEB_MARKERS = ("search", "fetch_url", "wikipedia", "browse", "http_get", "crawl")


def _with_guidance(
    messages: list[dict[str, Any]], definitions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add the sourcing instruction when the model has a way to reach the web."""
    names = " ".join(d["function"]["name"].lower() for d in definitions)
    if not any(marker in names for marker in _WEB_MARKERS):
        return messages
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            patched = dict(message)
            patched["content"] = str(message.get("content") or "") + RESEARCH_GUIDANCE
            return [*messages[:index], patched, *messages[index + 1:]]
    return [{"role": "system", "content": RESEARCH_GUIDANCE.strip()}, *messages]


# --------------------------------------------------------------------------
# approvals
# --------------------------------------------------------------------------

_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}


def _signature(conn_id: str, tool: str, arguments: dict[str, Any]) -> str:
    """Identify one exact call, so an approval cannot be reused for another."""
    return json.dumps(
        {"c": conn_id, "t": tool, "a": arguments}, sort_keys=True, separators=(",", ":")
    )


def _sweep() -> None:
    now = time.time()
    for key, rec in list(_PENDING.items()):
        if now - rec["created"] > PENDING_TTL:
            _PENDING.pop(key, None)


def propose(conn_id: str, tool: str, arguments: dict[str, Any], *, conversation_id: str = "") -> dict[str, Any]:
    """Record a mutating call awaiting approval. Nothing runs."""
    with _LOCK:
        _sweep()
        record = {
            "id": uuid.uuid4().hex[:12],
            "created": time.time(),
            "conversation_id": conversation_id,
            "connection_id": conn_id,
            "tool": tool,
            "arguments": arguments,
            "signature": _signature(conn_id, tool, arguments),
            "approved": False,
            "used": False,
        }
        _PENDING[record["id"]] = record
        return _public(record)


def approve(pending_id: str) -> dict[str, Any]:
    with _LOCK:
        _sweep()
        record = _PENDING.get(pending_id)
        if record is None:
            raise ToolChatError("that request has expired or never existed")
        if record["used"]:
            raise ToolChatError("that request has already run")
        record["approved"] = True
        return _public(record)


def reject(pending_id: str) -> dict[str, Any]:
    with _LOCK:
        record = _PENDING.pop(pending_id, None)
        if record is None:
            raise ToolChatError("that request has expired or never existed")
        record["approved"] = False
        return _public(record)


def pending() -> list[dict[str, Any]]:
    with _LOCK:
        _sweep()
        return [_public(r) for r in _PENDING.values() if not r["used"]]


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "signature"}


def _take_approval(conn_id: str, tool: str, arguments: dict[str, Any]) -> bool:
    """
    Consume an approval for exactly this call.

    Matching on the signature is what stops an approval for one path being
    spent on a different one.
    """
    want = _signature(conn_id, tool, arguments)
    with _LOCK:
        _sweep()
        for record in _PENDING.values():
            if record["approved"] and not record["used"] and record["signature"] == want:
                record["used"] = True
                return True
    return False


def writes_allowed() -> bool:
    """A standing permission, off by default; approval is still per call."""
    return bool(config.get_config().get("mcp_allow_writes"))


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------

def _as_text(result: Any) -> str:
    """
    Flatten an MCP tool result into something a model can read.

    The spec returns ``{"content": [{"type": "text", "text": ...}, ...]}``;
    anything else is passed through as JSON rather than dropped.
    """
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts: list[str] = []
        for block in result["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text") is not None:
                parts.append(str(block["text"]))
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        if result.get("isError"):
            return "ERROR: " + ("\n".join(parts) or "the tool reported an error")
        if parts:
            return "\n".join(parts)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def execute(entry: dict[str, Any], arguments: dict[str, Any], *, conversation_id: str = "") -> dict[str, Any]:
    """
    Run one tool call, or explain exactly why it did not run.

    The returned dict always says what really happened: `ran` is only true
    when ``armory.call_tool`` actually returned.
    """
    conn_id = entry["connection_id"]
    tool = entry["tool"]

    if entry["writes"] and not _take_approval(conn_id, tool, arguments):
        if not writes_allowed():
            record = propose(conn_id, tool, arguments, conversation_id=conversation_id)
            return {
                "ran": False,
                "needs_approval": True,
                "pending": record,
                "text": (
                    f"REFUSED: `{tool}` modifies data and has not been approved. "
                    "It was NOT run. Ask the user to approve it, and do not "
                    "describe its effect as having happened."
                ),
            }

    started = time.time()
    outcome = armory.call_tool(conn_id, tool, arguments)
    elapsed = int((time.time() - started) * 1000)

    if not outcome.get("ok"):
        return {
            "ran": False,
            "error": outcome.get("error", "unknown error"),
            "ms": elapsed,
            "text": f"ERROR: the tool did not run — {outcome.get('error', 'unknown error')}",
        }
    result = outcome.get("result")
    text = _as_text(result)

    # The call reached the server, but the server says the operation failed —
    # a refused URL, a 404, a search that returned nothing. That is not a
    # success, and a card that says "ran in 240ms" over an error message
    # invites the model and the user to believe something happened.
    failed = isinstance(result, dict) and bool(result.get("isError"))
    return {
        "ran": not failed,
        "ms": elapsed,
        "text": text,
        **({"error": text[:300]} if failed else {}),
    }


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

def available() -> bool:
    return bool(armory.all_tools())


def _parse_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull tool calls out of an Ollama / OpenAI-shaped assistant message."""
    calls: list[dict[str, Any]] = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        args = fn.get("arguments")
        # Ollama returns a dict; OpenAI returns a JSON string.
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append({"id": raw.get("id") or "", "name": name, "arguments": args})
    return calls


def run(
    messages: list[dict[str, Any]],
    *,
    model: str,
    conversation_id: str = "",
    timeout: float = 300.0,
) -> Iterator[dict[str, Any]]:
    """
    Drive the conversation with tools, yielding events for the SSE layer.

    Events: ``tool_call``, ``tool_result``, ``tool_approval``, ``token``,
    ``answer``. The caller turns them into SSE frames.
    """
    from . import llm  # imported here: llm imports this module for its catalogue

    definitions, lookup = catalogue()
    if not definitions:
        raise ToolChatError("no connected MCP server exposes any tools")

    work = _with_guidance(list(messages), definitions)
    yield {"type": "tools", "count": len(definitions),
           "names": [d["function"]["name"] for d in definitions]}

    for round_no in range(1, MAX_ROUNDS + 1):
        # Streamed, so the answer appears as it is written. Tokens are passed
        # straight through; the assembled message at the end is what the loop
        # reasons about. Before this, the loop asked for the whole message at
        # once and the user watched a blank screen for the entire generation.
        reply: dict[str, Any] = {}
        streamed = False
        for event in llm.chat_stream_full(work, model=model, tools=definitions,
                                          timeout=timeout):
            if event["type"] == "token":
                streamed = True
                yield {"type": "token", "text": event["text"]}
            else:
                reply = event["message"]

        calls = _parse_calls(reply)
        content = (reply.get("content") or "").strip()

        if not calls:
            # The model answered in words: that is the end of the loop. The
            # text has already been sent token by token, so say so rather than
            # repeating it — `streamed` tells the caller not to send it twice.
            yield {"type": "answer", "text": content, "rounds": round_no - 1,
                   "streamed": streamed}
            return

        # Keep the assistant's own turn in the transcript, or the tool
        # messages that follow have nothing to attach to.
        work.append({
            "role": "assistant",
            "content": content,
            "tool_calls": reply.get("tool_calls") or [],
        })

        for call in calls:
            entry = lookup.get(call["name"])
            if entry is None:
                text = f"ERROR: there is no tool called {call['name']}."
                yield {"type": "tool_result", "name": call["name"], "ran": False, "text": text}
                work.append({"role": "tool", "content": text, "name": call["name"]})
                continue

            yield {
                "type": "tool_call",
                "name": call["name"],
                "tool": entry["tool"],
                "connection_id": entry["connection_id"],
                "writes": entry["writes"],
                "arguments": call["arguments"],
            }

            outcome = execute(entry, call["arguments"], conversation_id=conversation_id)
            event = {
                "type": "tool_result",
                "name": call["name"],
                "tool": entry["tool"],
                "ran": outcome["ran"],
                "text": outcome["text"][:4000],
            }
            if outcome.get("ms") is not None:
                event["ms"] = outcome.get("ms")
            if outcome.get("needs_approval"):
                event["needs_approval"] = True
                event["pending"] = outcome["pending"]
                yield {"type": "tool_approval", "pending": outcome["pending"],
                       "tool": entry["tool"], "arguments": call["arguments"]}
            yield event

            work.append({
                "role": "tool",
                "content": outcome["text"][:8000],
                "name": call["name"],
            })

    yield {
        "type": "answer",
        "text": "",
        "rounds": MAX_ROUNDS,
        "truncated": True,
    }

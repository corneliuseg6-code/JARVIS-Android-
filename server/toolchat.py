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
        billed = bool(tool.get("billed"))
        lookup[exposed] = {
            "connection_id": conn_id,
            "tool": real,
            "connection": str(tool.get("connection_name") or ""),
            # A connection that spends money or credits (OpenArt) holds every
            # call for approval unless its name is plainly a read — and the
            # server's own readOnlyHint is not enough to waive that.
            "billed": billed,
            "writes": is_mutating(real, None if billed else tool.get("annotations")),
        }
    for exposed, spec in _builtins().items():
        definitions.append({
            "type": "function",
            "function": {"name": exposed, "description": spec["description"],
                         "parameters": spec["parameters"]},
        })
        lookup[exposed] = {"connection_id": "jarvis", "tool": exposed, "builtin": True,
                           "billed": False, "writes": False, "connection": "JARVIS"}
    return definitions, lookup


def _builtins() -> dict[str, dict[str, Any]]:
    """
    Tools JARVIS itself provides, offered only while they can work.

    Claude Code's tool *proposes* a coding task; it never runs one. The run
    waits in AI Connections for the user to read the exact command and
    approve it, so the model calling this tool spends nothing and changes
    nothing.
    """
    from . import claude_code  # local: claude_code is heavier and optional

    # Always there: "my workspace" means JARVIS's own folder, which the Files
    # server (rooted at the home directory) cannot name for the model. Without
    # this a small model lists the home directory, or guesses a path and fails.
    out: dict[str, dict[str, Any]] = {"jarvis__workspace_list": WORKSPACE_TOOL}
    try:
        if claude_code.ready_cached():
            out["jarvis__claude_code"] = claude_code.TOOL_SPEC
    except Exception:  # noqa: BLE001 - a broken optional tool must not break chat
        pass
    return out


WORKSPACE_TOOL: dict[str, Any] = {
    "description": (
        "List the folders and files in the user's JARVIS workspace — what they "
        "mean by 'my workspace' or 'my files'. Sections: Projects, Research, "
        "Documents, Notes, Tasks, Results, Journal. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Optional folder inside the workspace, e.g. 'Projects'. "
                                    "Leave empty for the top level."},
        },
    },
}


def _workspace_list(arguments: dict[str, Any]) -> str:
    from . import workspace

    raw = str(arguments.get("path") or arguments.get("folder") or "").strip()
    # Small models write "/", ".", "workspace" or "~/workspace" for the top.
    if raw.strip("/. ").lower() in ("", "workspace", "~/workspace", "jarvis", "my workspace"):
        raw = ""
    data = workspace.listing(raw.strip("/"))
    entries = data["entries"]
    where = data["path"] if data["path"] not in ("", ".") else "the workspace"
    if not entries:
        return f"{where} is empty."
    lines = [f"{len(entries)} entries in {where}:"]
    for e in entries[:200]:
        lines.append(f"- {e['name']}/" if e["kind"] == "folder" else f"- {e['name']} ({e['size']} bytes)")
    if len(entries) > 200:
        lines.append(f"… and {len(entries) - 200} more")
    return "\n".join(lines)


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


def propose(conn_id: str, tool: str, arguments: dict[str, Any], *, conversation_id: str = "",
            billed: bool = False, connection: str = "") -> dict[str, Any]:
    """Record a held call awaiting approval. Nothing runs."""
    with _LOCK:
        _sweep()
        record = {
            "billed": billed,
            "connection": connection,
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
            elif block.get("type") in ("image", "audio", "video") and block.get("data"):
                # Megabytes of base64 help no model; the file is saved and its
                # path appended by execute().
                parts.append(f"[{block['type']} returned: {block.get('mimeType', '?')}]")
            elif block.get("type") == "resource_link" or (
                    block.get("type") == "resource" and isinstance(block.get("resource"), dict)):
                res = block.get("resource") if block.get("type") == "resource" else block
                parts.append(f"[{block['type']}: {res.get('uri', '')} {res.get('mimeType', '')}]".strip())
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

    if entry.get("builtin") and tool == "jarvis__workspace_list":
        from . import workspace

        started = time.time()
        try:
            text = _workspace_list(arguments)
        except workspace.WorkspaceError as exc:
            return {"ran": False, "error": str(exc), "ms": 0, "text": f"ERROR: {exc}"}
        return {"ran": True, "ms": int((time.time() - started) * 1000), "text": text}

    if entry.get("builtin"):
        from . import claude_code

        started = time.time()
        try:
            record = claude_code.propose_from_tool(arguments)
        except claude_code.CodeError as exc:
            return {"ran": False, "error": exc.detail, "ms": 0,
                    "text": f"ERROR: the coding task was not queued — {exc.detail}"}
        return {
            "ran": True,
            "ms": int((time.time() - started) * 1000),
            "code_request": record,
            "text": (
                f"QUEUED, NOT RUN: Claude Code request {record['id']} ({record['mode']} mode) "
                f"is waiting for the user's approval in AI Connections → Claude Code. "
                "Nothing has been changed yet. Tell the user it is waiting for them, "
                "and do not describe any result."
            ),
        }

    held = entry["writes"] or entry.get("billed")
    if held and not _take_approval(conn_id, tool, arguments):
        if entry.get("billed") or not writes_allowed():
            record = propose(conn_id, tool, arguments, conversation_id=conversation_id,
                             billed=bool(entry.get("billed")),
                             connection=entry.get("connection", ""))
            why = ("may spend credits on the user's account" if entry.get("billed")
                   else "modifies data")
            return {
                "ran": False,
                "needs_approval": True,
                "pending": record,
                "text": (
                    f"REFUSED: `{tool}` {why} and has not been approved. "
                    "It was NOT run. The user can approve and run it from the card "
                    "in the chat. Do not describe its effect as having happened."
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
    media: list[dict[str, Any]] = []
    if not failed:
        try:
            from . import media as media_store
            media = media_store.keep(result, tool=tool, connection=entry.get("connection", ""))
        except Exception:  # noqa: BLE001 - saving a copy must never hide the result
            media = []
    if media:
        text += "\n" + "\n".join(f"[saved to workspace: {m['path']}]" for m in media)
    return {
        "ran": not failed,
        "ms": elapsed,
        "text": text,
        **({"media": media} if media else {}),
        **({"error": text[:300]} if failed else {}),
    }


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

def available() -> bool:
    """
    Whether a turn goes through the tool loop at all.

    Only when a real tool source is there: a connected MCP server, or a ready
    Claude Code. The workspace tool rides along with those rather than
    turning every plain chat into a tool turn on its own.
    """
    return bool(armory.all_tools()) or any(k != "jarvis__workspace_list" for k in _builtins())


def run_approved(pending_id: str) -> dict[str, Any]:
    """
    Run one approved call now, from its card, instead of asking the model to
    try again. The approval is consumed exactly as a model-driven call would
    consume it, so the same rules hold: one approval, one run, same arguments.
    """
    with _LOCK:
        _sweep()
        record = _PENDING.get(pending_id)
        if record is None:
            raise ToolChatError("that request has expired or never existed")
        if record["used"]:
            raise ToolChatError("that request has already run")
        record["approved"] = True
        conn_id, tool, arguments = record["connection_id"], record["tool"], record["arguments"]
    entry = {
        "connection_id": conn_id, "tool": tool, "writes": True,
        "billed": record.get("billed", False), "connection": record.get("connection", ""),
    }
    outcome = execute(entry, arguments, conversation_id=record.get("conversation_id", ""))
    with _LOCK:
        _PENDING.pop(pending_id, None)
    return {"id": pending_id, "tool": tool, **{k: v for k, v in outcome.items() if k != "pending"}}


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
        calls.append({"id": raw.get("id") or f"call_{len(calls)}", "name": name, "arguments": args})
    return calls


def resolve_name(name: str, lookup: dict[str, dict[str, Any]]) -> str | None:
    """
    The exposed name a model meant, or None.

    Small local models routinely drop the connection prefix JARVIS adds
    (`449fb5b27a93__list_directory` comes back as `list_directory`) or change
    its case. Accept the real tool name when exactly one connection has it;
    refuse when it is ambiguous rather than guess between two servers.
    """
    if name in lookup:
        return name

    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (text or "").lower())

    wanted = norm(name.split("__", 1)[-1])
    matches = [key for key, entry in lookup.items()
               if norm(key) == norm(name) or norm(entry["tool"].split("__", 1)[-1]) == wanted]
    return matches[0] if len(matches) == 1 else None


#: Said after the tools, when the model is asked for its answer. Plain and
#: short, because it is mostly read by small local models.
FINAL_NUDGE = ("Answer my question now, in plain words, using the tool results above. "
               "Do not call any more tools.")

#: Appended to the system message whenever tools are offered.
TOOL_GUIDANCE = (
    "\n\nYou can use tools. Call one only when you need it. After a tool result "
    "arrives, answer the user from it; never call the same tool again with the same "
    "arguments. For the user's workspace use jarvis__workspace_list."
)

#: The fallback sentence when a model returns nothing at all after a tool
#: ran: the user still gets the result, in their own language.
_FALLBACK = {
    "ro": "Iată ce a returnat `{tool}`:",
    "en": "Here is what `{tool}` returned:",
    "fr": "Voici ce qu'a renvoyé `{tool}` :",
    "de": "Das hat `{tool}` geliefert:",
    "es": "Esto es lo que devolvió `{tool}`:",
    "it": "Ecco cosa ha restituito `{tool}`:",
}

#: How much of one tool result a local model is shown. Ollama silently drops
#: the *start* of a prompt that outgrows its context, which is where the
#: user's question is — so a long result must not push it out.
LOCAL_RESULT_CHARS = 2500


def _with_tool_guidance(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            patched = dict(message)
            patched["content"] = str(message.get("content") or "") + TOOL_GUIDANCE
            return [*messages[:index], patched, *messages[index + 1:]]
    return [{"role": "system", "content": TOOL_GUIDANCE.strip()}, *messages]


def _fallback_answer(results: list[dict[str, Any]], language: str) -> str:
    """The last successful result, introduced in the user's language."""
    for item in reversed(results):
        if item["ran"] and item["text"].strip():
            head = _FALLBACK.get(language, _FALLBACK["en"]).format(tool=item["tool"].split("__")[-1])
            return f"{head}\n\n{item['text'].strip()[:3000]}"
    return ""


def run(
    messages: list[dict[str, Any]],
    *,
    model: str,
    conversation_id: str = "",
    timeout: float = 300.0,
    which: str | None = None,
    language: str = "en",
) -> Iterator[dict[str, Any]]:
    """
    Drive the conversation with tools, yielding events for the SSE layer.

    Events: ``tool_call``, ``tool_result``, ``tool_approval``, ``token``,
    ``answer``. The caller turns them into SSE frames.

    A turn always ends with an answer or an explicit ``empty`` flag — never
    silently. A model that loops (calls the same tool with the same
    arguments again) or runs out of rounds is asked once more, without
    tools, to answer from what it has; if it still says nothing, the user
    gets the tool's own result. Before this, either case ended the turn with
    no text and no error, and the interface simply went back to standby.
    """
    from . import llm  # imported here: llm imports this module for its catalogue

    definitions, lookup = catalogue()
    if not definitions:
        raise ToolChatError("no connected MCP server exposes any tools")

    local = (which or llm.provider()) == llm.OLLAMA
    work = _with_tool_guidance(_with_guidance(list(messages), definitions))
    yield {"type": "tools", "count": len(definitions),
           "names": [d["function"]["name"] for d in definitions]}

    results: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for round_no in range(1, MAX_ROUNDS + 1):
        # Streamed, so the answer appears as it is written. Tokens are passed
        # straight through; the assembled message at the end is what the loop
        # reasons about. Before this, the loop asked for the whole message at
        # once and the user watched a blank screen for the entire generation.
        reply: dict[str, Any] = {}
        streamed = False
        for event in llm.chat_stream_full(work, model=model, tools=definitions,
                                          timeout=timeout, which=which):
            if event["type"] == "token":
                streamed = True
                yield {"type": "token", "text": event["text"]}
            elif event["type"] == "message":
                reply = event["message"]
                if event.get("cost"):
                    yield {"type": "cost", **event["cost"]}

        calls = _parse_calls(reply)
        content = (reply.get("content") or "").strip()

        if not calls:
            if content or not results:
                # The model answered in words: that is the end of the loop. The
                # text has already been sent token by token, so say so rather
                # than repeating it — `streamed` tells the caller not to send it
                # twice. Empty with no tool run is reported as empty, not hidden.
                yield {"type": "answer", "text": content, "rounds": round_no - 1,
                       "streamed": streamed, **({} if content else {"empty": True})}
                return
            # Tools ran and the model said nothing: ask for the answer.
            break

        # Keep the assistant's own turn in the transcript, or the tool
        # messages that follow have nothing to attach to.
        turn = {
            "role": "assistant",
            "content": content,
            "tool_calls": reply.get("tool_calls") or [],
        }
        if reply.get("_native"):
            turn["_native"] = reply["_native"]
        work.append(turn)

        repeated = False
        for call in calls:
            resolved = resolve_name(call["name"], lookup)
            entry = lookup.get(resolved) if resolved else None
            if entry is None:
                names = ", ".join(sorted(lookup))
                text = (f"ERROR: there is no tool called {call['name']}. "
                        f"Available tools: {names}.")
                yield {"type": "tool_result", "name": call["name"], "ran": False, "text": text}
                work.append({"role": "tool", "content": text, "name": call["name"],
                             "tool_call_id": call["id"], "is_error": True})
                continue

            key = json.dumps([resolved, call["arguments"]], sort_keys=True, default=str)
            if key in seen:
                # The same call again: the result is already in the transcript.
                # Running it again cannot help and is how a small model spins
                # through every round and ends with nothing to say.
                # The earlier result is handed back again — not re-run — because
                # a small model attends to the newest message most.
                repeated = True
                work.append({"role": "tool", "content": seen[key],
                             "name": call["name"], "tool_call_id": call["id"]})
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
            if outcome.get("media"):
                event["media"] = outcome["media"]
            if outcome.get("code_request"):
                event["code_request"] = outcome["code_request"]
                yield {"type": "code_request", "request": outcome["code_request"]}
            if outcome.get("needs_approval"):
                event["needs_approval"] = True
                event["pending"] = outcome["pending"]
                yield {"type": "tool_approval", "pending": outcome["pending"],
                       "tool": entry["tool"], "arguments": call["arguments"],
                       "billed": bool(entry.get("billed")),
                       "connection": entry.get("connection", "")}
            yield event

            results.append({"tool": entry["tool"], "ran": outcome["ran"], "text": outcome["text"]})
            shown = outcome["text"][:LOCAL_RESULT_CHARS if local else 8000]
            seen[key] = shown
            work.append({
                "role": "tool",
                "content": shown,
                "name": call["name"],
                "tool_call_id": call["id"],
                "is_error": not outcome["ran"],
            })

        if repeated:
            break

    # ---- the answer, which every turn gets ---------------------------------
    # Asked once more, with an explicit request to answer. A local model is
    # given no tools this time, so it cannot loop again; a paid API keeps the
    # definitions (its history holds tool calls) and any further call is
    # ignored.
    work.append({"role": "user", "content": FINAL_NUDGE})
    text_parts: list[str] = []
    for event in llm.chat_stream_full(work, model=model, tools=None if local else definitions,
                                      timeout=timeout, which=which):
        if event["type"] == "token":
            text_parts.append(event["text"])
            yield {"type": "token", "text": event["text"]}
        elif event["type"] == "message":
            if not text_parts and (event["message"].get("content") or "").strip() \
                    and not event["message"].get("tool_calls"):
                text_parts.append(event["message"]["content"])
                yield {"type": "token", "text": event["message"]["content"]}
            if event.get("cost"):
                yield {"type": "cost", **event["cost"]}
    final = "".join(text_parts).strip()
    if final:
        yield {"type": "answer", "text": final, "rounds": MAX_ROUNDS, "streamed": True}
        return
    fallback = _fallback_answer(results, language)
    if fallback:
        yield {"type": "answer", "text": fallback, "rounds": MAX_ROUNDS, "fallback": True}
        return
    yield {"type": "answer", "text": "", "rounds": MAX_ROUNDS, "empty": True}

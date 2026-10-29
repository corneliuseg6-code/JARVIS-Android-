"""Bridge to the Claude Code CLI, for coding help that touches real files.

What this is, exactly
---------------------
JARVIS does not reimplement Claude Code. It looks for the ``claude`` binary on
PATH and, when one is there, runs it headlessly for a single request. If there
is no binary, the feature reports that and does nothing — there is no mock
mode, and the switch stays off.

**Android is not an Anthropic-supported platform for Claude Code.** The
documented systems are macOS 13+, Windows 10+, Ubuntu 20.04+, Debian 10+ and
Alpine 3.19+, and the published builds are glibc or musl Linux; Termux is
Android with Bionic libc, so the official installers do not produce a working
binary there. Community routes exist (a patched loader, glibc-runner, a proot
Ubuntu chroot) and this bridge will use whatever they leave on PATH, but
nothing here can make an unsupported platform supported. ``detect()`` says so
in as many words so the interface can show the reason rather than a dead
button.

Authentication
--------------
JARVIS never handles Claude Code's credentials. ``claude`` authenticates
itself — a subscription login, or its own environment — and this module runs
it with the environment it was started with. No key of ours is injected, and
none is read back out.

Approval
--------
Nothing runs without a round trip. JARVIS's chat can *propose* a task through
its ``jarvis__claude_code`` tool, but only the user's tap in AI Connections
runs it. ``propose()`` records a request and returns
the exact argument vector that would be executed; only ``approve()`` starts it.
Edits are a second gate on top: they need the setting enabled *and*
``allow_edits`` on the approval, and the default run is plan-only, which
cannot write. ``--dangerously-skip-permissions`` is never passed.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import config

#: Permission modes this bridge is willing to use, in increasing order of
#: what Claude Code may do. bypassPermissions is deliberately absent.
PLAN = "plan"            # read and reason; cannot modify anything
EDIT = "edit"            # may edit files in the working directory
MODES = (PLAN, EDIT)

_CLI_MODE = {PLAN: "plan", EDIT: "acceptEdits"}

#: How long a single run may take before it is killed.
RUN_TIMEOUT = 600.0

#: Requests kept in memory for the interface to poll. Nothing is persisted:
#: a restart clears the queue, which is the safe direction to fail.
_MAX_KEPT = 20

_LOCK = threading.Lock()
_REQUESTS: "dict[str, dict[str, Any]]" = {}
_ORDER: list[str] = []


class CodeError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def _is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or Path("/data/data/com.termux").exists()


def platform_name() -> str:
    if _is_termux():
        return "termux"
    return platform.system().lower() or "unknown"


#: Platforms Anthropic documents as supported for Claude Code.
_SUPPORTED = ("linux", "darwin", "windows")

_TERMUX_NOTE = (
    "Android/Termux is not an Anthropic-supported platform for Claude Code. "
    "The published builds are glibc or musl Linux and Termux uses Bionic, so "
    "the official installer does not produce a working binary. If you have set "
    "one up another way (a patched loader, glibc-runner, or a proot Ubuntu "
    "chroot) and `claude` is on PATH, JARVIS will use it."
)

_MISSING_NOTE = (
    "No `claude` binary was found on PATH. Install Claude Code and make sure "
    "`claude --version` works in the same shell that starts JARVIS."
)


def detect(timeout: float = 8.0) -> dict[str, Any]:
    """
    Is there a usable Claude Code CLI here, and if not, why not?

    This runs the real binary rather than trusting PATH: on the unsupported
    platforms the file often exists and fails to execute, which is a different
    answer from "not installed" and deserves a different message.
    """
    where = platform_name()
    supported = where in _SUPPORTED
    info: dict[str, Any] = {
        "platform": where,
        "officially_supported": supported,
        "installed": False,
        "path": "",
        "version": "",
        "reason": "",
        "note": _TERMUX_NOTE if where == "termux" else "",
    }

    binary = shutil.which("claude")
    if not binary:
        info["reason"] = _MISSING_NOTE
        return info
    info["path"] = binary

    try:
        done = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        info["reason"] = "`claude --version` did not finish; the binary is not responding."
        return info
    except OSError as exc:
        # The usual shape of an ABI mismatch on Termux: the file is there and
        # the kernel refuses to run it.
        info["reason"] = f"`claude` could not be executed: {exc.strerror or exc}."
        return info

    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        info["reason"] = (
            "`claude --version` exited with "
            f"{done.returncode}: {detail[0][:200] if detail else 'no output'}"
        )
        return info

    info["installed"] = True
    info["version"] = (done.stdout or "").strip().splitlines()[0][:80] if done.stdout else ""
    return info


def enabled() -> bool:
    return bool(config.get_config().get("claude_code_enabled"))


def allow_edits_configured() -> bool:
    return bool(config.get_config().get("claude_code_allow_edits"))


# --------------------------------------------------------------------------
# the working directory
# --------------------------------------------------------------------------

def workdir() -> Path | None:
    """The one directory Claude Code is allowed to touch, or None if unset."""
    raw = str(config.get_config().get("claude_code_workdir") or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.is_dir() else None


def _resolve_target(directory: str) -> Path:
    """
    Validate a requested directory against the configured root.

    A request may name a subdirectory, never a sibling and never the whole
    filesystem: the resolved path has to sit inside the resolved root, which
    is what stops `..` from walking out of it.
    """
    root = workdir()
    if root is None:
        raise CodeError(
            "no_workdir",
            "No working directory is set. Choose one in AI Connections first.",
        )
    if not directory:
        return root
    try:
        target = (root / directory).resolve() if not Path(directory).is_absolute() \
            else Path(directory).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise CodeError("bad_directory", str(exc)[:200]) from exc
    if target != root and root not in target.parents:
        raise CodeError(
            "outside_workdir",
            f"{target} is outside the configured working directory {root}.",
        )
    if not target.is_dir():
        raise CodeError("bad_directory", f"{target} is not a directory.")
    return target


# --------------------------------------------------------------------------
# what this CLI build supports, and whether it is signed in
# --------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}
CACHE_TTL = 60.0


def _cached(key: str, compute, ttl: float = CACHE_TTL):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = compute()
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _help_text(binary: str) -> str:
    try:
        done = subprocess.run([binary, "--help"], capture_output=True, text=True,
                              timeout=15, check=False)
        return (done.stdout or "") + (done.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def supports(binary: str, flag: str) -> bool:
    """
    Does this build of the CLI know `flag`?

    Builds on community ports lag behind, and an unknown option makes the
    whole run fail. When the help text is not recognisably Claude Code's,
    the flag is assumed present, so a stub or wrapper is not crippled.
    """
    text = _cached(f"help:{binary}", lambda: _help_text(binary), ttl=3600.0)
    if "--print" not in text:
        return True
    return flag in text


def auth_status(binary: str | None = None) -> dict[str, Any]:
    """
    ``claude auth status`` — the CLI's own answer to "is it signed in, and how".

    JARVIS reports this; it never reads the credentials themselves. A
    subscription sign-in and an API key bill differently, and which one is in
    use is the CLI's business, so the interface shows `auth_method` as the CLI
    reports it rather than guessing.
    """
    binary = binary or shutil.which("claude")
    if not binary:
        return {"known": False, "logged_in": False, "method": "", "detail": "not installed"}

    def run() -> dict[str, Any]:
        try:
            done = subprocess.run([binary, "auth", "status", "--json"], capture_output=True,
                                  text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"known": False, "logged_in": False, "method": "", "detail": str(exc)[:200]}
        try:
            data = json.loads(done.stdout or "{}")
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict) or "loggedIn" not in data:
            text = (done.stderr or done.stdout or "").strip().splitlines()
            return {"known": False, "logged_in": False, "method": "",
                    "detail": (text[0][:200] if text else "this CLI cannot report its sign-in")}
        return {"known": True, "logged_in": bool(data.get("loggedIn")),
                "method": str(data.get("authMethod") or ""),
                "api_provider": str(data.get("apiProvider") or ""), "detail": ""}

    return _cached(f"auth:{binary}", run)


def ready_cached() -> bool:
    """Cheap readiness for the chat catalogue: no subprocess per message."""
    if not enabled() or workdir() is None:
        return False
    return bool(_cached("detect", detect)["installed"])


# --------------------------------------------------------------------------
# the request queue
# --------------------------------------------------------------------------

def _public(record: dict[str, Any]) -> dict[str, Any]:
    """What the browser may see: everything except live handles."""
    return {k: v for k, v in record.items() if not k.startswith("_")}


def _remember(record: dict[str, Any]) -> None:
    with _LOCK:
        _REQUESTS[record["id"]] = record
        _ORDER.append(record["id"])
        while len(_ORDER) > _MAX_KEPT:
            _REQUESTS.pop(_ORDER.pop(0), None)


def get(request_id: str) -> dict[str, Any]:
    with _LOCK:
        record = _REQUESTS.get(request_id)
    if record is None:
        raise CodeError("unknown_request", "That request has expired or never existed.")
    return _public(record)


def recent() -> list[dict[str, Any]]:
    with _LOCK:
        return [_public(_REQUESTS[i]) for i in reversed(_ORDER) if i in _REQUESTS]


def _argv(binary: str, prompt: str, target: Path, mode: str, model: str, turns: int,
          *, resume: str = "") -> list[str]:
    cfg = config.get_config()
    argv = [
        binary,
        "-p", prompt,
        # stream-json: one JSON event per line, so the interface can show the
        # run as it happens and the final event carries the cost.
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", _CLI_MODE[mode],
        "--max-turns", str(turns),
        "--add-dir", str(target),
    ]
    extra_tools = str(cfg.get("claude_code_allowed_tools") or "").strip()
    if supports(binary, "--restricted"):
        # Restricted removes the tools that run commands, confines file tools
        # to the working directory and refuses bypassPermissions. A plan run
        # is always restricted; an edit run too, unless the user has granted
        # specific shell commands in settings.
        if mode == PLAN or "Bash" not in extra_tools:
            argv.append("--restricted")
    if mode == EDIT and extra_tools:
        argv += ["--allowedTools", extra_tools]
    if supports(binary, "--permission-prompts"):
        # Headless: nobody is there to answer a prompt, so anything that would
        # ask is refused rather than left hanging.
        argv += ["--permission-prompts", "none"]
    try:
        budget = float(cfg.get("claude_code_max_budget_usd") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    if budget > 0 and supports(binary, "--max-budget-usd"):
        argv += ["--max-budget-usd", f"{budget:.2f}"]
    if model:
        argv += ["--model", model]
    if resume:
        argv += ["--resume", resume]
    return argv


TOOL_SPEC: dict[str, Any] = {
    "description": (
        "Queue a software task for Claude Code, the coding agent, to work on a "
        "project in the user's configured projects folder: explain or review "
        "code (mode 'plan', read-only) or develop, edit and repair it (mode "
        "'edit'). This does NOT run anything: the user must read and approve the "
        "exact command in AI Connections first. Use it when the user asks for "
        "real work on their code; tell them it is waiting for their approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What Claude Code should do, in full."},
            "mode": {"type": "string", "enum": ["plan", "edit"],
                     "description": "plan = read and report only; edit = may change files."},
            "directory": {"type": "string",
                          "description": "Optional project subfolder, relative to the projects folder."},
        },
        "required": ["task"],
    },
}


def propose_from_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    """The chat tool's entry point: same gates as the interface, plus a tag."""
    mode = str(arguments.get("mode") or PLAN).lower()
    if mode not in MODES:
        mode = PLAN
    if mode == EDIT and not allow_edits_configured():
        # Downgrade rather than fail: the user can still see a plan, and the
        # card says edits are off.
        mode = PLAN
    record = propose(str(arguments.get("task") or arguments.get("prompt") or ""),
                     directory=str(arguments.get("directory") or ""), mode=mode)
    with _LOCK:
        if record["id"] in _REQUESTS:
            _REQUESTS[record["id"]]["origin"] = "chat"
    return {**record, "origin": "chat"}


def propose(prompt: str, *, directory: str = "", mode: str = PLAN,
            resume: str = "") -> dict[str, Any]:
    """
    Record what JARVIS would like to run. Nothing is executed.

    The returned record carries the exact argument vector, so the interface can
    show the user the real command rather than a description of one.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise CodeError("empty_prompt", "There is nothing to ask.")
    if mode not in MODES:
        raise CodeError("bad_mode", f"Unknown mode {mode!r}.")
    if not enabled():
        raise CodeError("disabled", "Claude Code is switched off in AI Connections.")

    found = detect()
    if not found["installed"]:
        raise CodeError("not_installed", found["reason"] or "Claude Code is not available here.")
    if mode == EDIT and not allow_edits_configured():
        raise CodeError(
            "edits_not_allowed",
            "File edits are switched off. Turn them on in AI Connections first.",
        )
    if resume and not all(c.isalnum() or c == "-" for c in resume):
        raise CodeError("bad_session", "That session id is not valid.")

    target = _resolve_target(directory)
    cfg = config.get_config()
    try:
        turns = max(1, min(20, int(cfg.get("claude_code_max_turns") or 6)))
    except (TypeError, ValueError):
        turns = 6

    record = {
        "id": uuid.uuid4().hex[:12],
        "created": time.time(),
        "prompt": prompt,
        "directory": str(target),
        "mode": mode,
        "state": "pending",
        "argv": _argv(
            found["path"], prompt, target, mode,
            str(cfg.get("claude_code_model") or "").strip(), turns, resume=resume,
        ),
        "version": found["version"],
        "origin": "user",
        "resume": resume,
        "result": "",
        "error": "",
        "progress": [],
        "session_id": "",
        "cost_usd": None,
        "changes": None,
        "finished": 0.0,
        "_proc": None,
        "_before": None,
    }
    _remember(record)
    return _public(record)


def cancel(request_id: str) -> dict[str, Any]:
    with _LOCK:
        record = _REQUESTS.get(request_id)
        if record is None:
            raise CodeError("unknown_request", "That request has expired or never existed.")
        if record["state"] != "pending":
            raise CodeError("not_pending", "That request is no longer waiting for approval.")
        record["state"] = "cancelled"
        record["finished"] = time.time()
        return _public(record)


def stop(request_id: str) -> dict[str, Any]:
    """Stop a running request. Whatever it already changed stays changed."""
    with _LOCK:
        record = _REQUESTS.get(request_id)
        if record is None:
            raise CodeError("unknown_request", "That request has expired or never existed.")
        if record["state"] != "running":
            raise CodeError("not_running", "That request is not running.")
        proc = record.get("_proc")
        record["stopped"] = True
    if proc is not None:
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass
    return get(request_id)


def approve(request_id: str, *, allow_edits: bool = False) -> dict[str, Any]:
    """
    Run a pending request. This is the only path that starts a process.

    An edit-mode request needs ``allow_edits`` here as well as the setting:
    the setting says edits are possible at all, this says the user approved
    this one.
    """
    with _LOCK:
        record = _REQUESTS.get(request_id)
        if record is None:
            raise CodeError("unknown_request", "That request has expired or never existed.")
        if record["state"] != "pending":
            raise CodeError("not_pending", f"That request is already {record['state']}.")
        if record["mode"] == EDIT:
            if not allow_edits_configured():
                raise CodeError("edits_not_allowed", "File edits are switched off.")
            if not allow_edits:
                raise CodeError(
                    "edits_not_confirmed",
                    "This request would modify files and was not explicitly confirmed.",
                )
        record["state"] = "running"
        record["started"] = time.time()
        thread = threading.Thread(target=_run, args=(record,), daemon=True)
    thread.start()
    return _public(record)


# --------------------------------------------------------------------------
# running, and seeing what changed
# --------------------------------------------------------------------------

def _git(directory: str, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", directory, *args], capture_output=True, text=True,
                              timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def _snapshot(directory: str) -> dict[str, Any]:
    """
    The state of the project before a run, to compare against after it.

    In a git repository: which paths were already dirty. Elsewhere: size and
    mtime of up to 5000 files, which is enough to say what changed.
    """
    top = _git(directory, "rev-parse", "--show-toplevel")
    if top is not None:
        status = _git(directory, "status", "--porcelain", "-uall", "--", ".") or ""
        return {"git": True, "top": top.strip(), "dirty": sorted(_porcelain_paths(status))}
    files: dict[str, tuple[int, float]] = {}
    base = Path(directory)
    for path in base.rglob("*"):
        if len(files) >= 5000:
            break
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        try:
            if path.is_file():
                st = path.stat()
                files[path.relative_to(base).as_posix()] = (st.st_size, st.st_mtime)
        except OSError:
            continue
    return {"git": False, "files": files}


def _porcelain_paths(status: str) -> set[str]:
    out = set()
    for line in status.splitlines():
        if len(line) > 3:
            name = line[3:]
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            out.add(name.strip().strip('"'))
    return out


def _changes(directory: str, before: dict[str, Any] | None) -> dict[str, Any] | None:
    if not before:
        return None
    if before.get("git"):
        status = _git(directory, "status", "--porcelain", "-uall", "--", ".") or ""
        now_dirty = _porcelain_paths(status)
        touched = sorted(now_dirty - set(before["dirty"]))
        untracked = {line[3:].strip() for line in status.splitlines() if line.startswith("??")}
        diff = _git(directory, "diff", "--", *touched) if touched else ""
        return {
            "git": True,
            "files": touched,
            "new_files": sorted(set(touched) & untracked),
            # Files that were already modified before the run cannot be told
            # apart from the run's own edits, so they are named, not diffed.
            "already_dirty": before["dirty"][:50],
            "diff": (diff or "")[:30000],
            "revertible": bool(touched),
        }
    after = _snapshot(directory)
    old, new = before.get("files") or {}, after.get("files") or {}
    changed = sorted(p for p in new if p not in old or new[p] != old[p])
    removed = sorted(p for p in old if p not in new)
    return {"git": False, "files": changed, "removed": removed, "diff": "", "revertible": False}


def _run(record: dict[str, Any]) -> None:
    if record["mode"] == EDIT:
        record["_before"] = _snapshot(record["directory"])
    try:
        proc = subprocess.Popen(
            record["argv"],
            cwd=record["directory"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            # The binary's own environment, unchanged: Claude Code owns its
            # credentials and JARVIS has no business adding to or reading them.
            env=os.environ.copy(),
        )
    except OSError as exc:
        _finish(record, "error", error=f"Could not run Claude Code: {exc.strerror or exc}")
        return
    with _LOCK:
        record["_proc"] = proc

    # stderr is drained on its own thread: a chatty CLI filling the pipe
    # while stdout is being read would otherwise deadlock both.
    err_parts: list[str] = []
    drain = threading.Thread(target=lambda: err_parts.append(proc.stderr.read() if proc.stderr else ""),
                             daemon=True)
    drain.start()
    timer = threading.Timer(RUN_TIMEOUT, lambda: proc.kill())
    timer.daemon = True
    timer.start()
    final: dict[str, Any] = {}
    raw_lines: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            raw_lines.append(line)
            del raw_lines[:-200]
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            _progress(record, event)
            if event.get("type") == "result" or ("result" in event and "type" not in event):
                final = event
        code = proc.wait()
        drain.join(timeout=5)
        stderr = "".join(err_parts)
    finally:
        timer.cancel()

    record["changes"] = _changes(record["directory"], record.get("_before"))
    if final.get("total_cost_usd") is not None:
        try:
            record["cost_usd"] = round(float(final["total_cost_usd"]), 4)
        except (TypeError, ValueError):
            pass
    if final.get("session_id"):
        record["session_id"] = str(final["session_id"])

    if record.get("stopped"):
        _finish(record, "stopped", result=str(final.get("result") or ""),
                error="Stopped by the user. Anything already changed stays changed.")
        return
    if code != 0 or final.get("is_error"):
        detail = str(final.get("result") or "") or _STOP_REASONS.get(str(final.get("subtype") or ""), "")
        if not detail and isinstance(final.get("errors"), list) and final["errors"]:
            detail = "; ".join(str(e) for e in final["errors"])[:1000]
        detail = detail or (stderr or "").strip() or "\n".join(raw_lines[-3:])
        if code == -9 or code == 137:
            detail = f"The run exceeded {int(RUN_TIMEOUT)}s and was stopped. " + detail
        _finish(record, "error", result="", error=detail[:2000] or f"Exited with {code}.")
        return
    text = final.get("result")
    if not isinstance(text, str):
        text = "\n".join(raw_lines)[-20000:]
    _finish(record, "done", result=text[:20000])


#: The CLI's own stop reasons, said in words. A run that hits a limit stops
#: cleanly; what it already did stays done.
_STOP_REASONS = {
    "error_max_budget_usd": "Stopped at the spending cap (AI Connections → Claude Code → "
                            "budget). Raise it if this task needs more.",
    "error_max_turns": "Stopped at the turn limit before finishing. Raise the turn limit "
                       "or split the task.",
    "error_during_execution": "Claude Code hit an error while working.",
}


def _progress(record: dict[str, Any], event: dict[str, Any]) -> None:
    """One short line per step, so the user can watch the agent work."""
    line = ""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        record["session_id"] = str(event.get("session_id") or "")
        line = f"started ({event.get('model', 'default model')})"
    elif kind == "assistant":
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                args = block.get("input") or {}
                target = args.get("file_path") or args.get("path") or args.get("pattern") \
                    or args.get("command") or ""
                line = f"{block.get('name', 'tool')} {str(target)[:120]}".strip()
            elif block.get("type") == "text" and block.get("text"):
                line = str(block["text"]).strip().splitlines()[0][:160]
    if line:
        with _LOCK:
            record["progress"] = (record.get("progress") or []) + [line]
            del record["progress"][:-40]


def _finish(record: dict[str, Any], state: str, *, result: str = "", error: str = "") -> None:
    with _LOCK:
        record["state"] = state
        record["result"] = result
        record["error"] = error
        record["finished"] = time.time()
        record["_proc"] = None


def revert(request_id: str, *, confirm: bool = False) -> dict[str, Any]:
    """
    Undo one edit run's changes, in a git repository only.

    Only files the run itself changed are touched — files that were already
    modified before it are left exactly as they were. Tracked files go back to
    their committed content; files the run created are deleted.
    """
    if not confirm:
        raise CodeError("confirm_required", "Reverting must be confirmed.")
    with _LOCK:
        record = _REQUESTS.get(request_id)
    if record is None:
        raise CodeError("unknown_request", "That request has expired or never existed.")
    changes = record.get("changes") or {}
    if record["state"] == "running":
        raise CodeError("running", "Wait for the run to finish first.")
    if not changes.get("git") or not changes.get("revertible"):
        raise CodeError("not_revertible", "Only an edit run inside a git repository can be reverted.")
    directory = record["directory"]
    root = workdir()
    new_files = set(changes.get("new_files") or [])
    restored, deleted = [], []
    for name in changes.get("files") or []:
        path = (Path(directory) / name).resolve()
        if root is None or (path != root and root not in path.parents):
            continue
        if name in new_files:
            try:
                path.unlink()
                deleted.append(name)
            except OSError:
                pass
        elif _git(directory, "checkout", "--", name) is not None:
            restored.append(name)
    with _LOCK:
        record["changes"] = {**changes, "revertible": False, "reverted": True,
                             "restored": restored, "deleted": deleted}
    return _public(record)


def _text_of(stdout: str) -> str:
    """Pull the answer out of `--output-format json` (kept for older callers)."""
    raw = (stdout or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:20000]
    if isinstance(payload, dict):
        for key in ("result", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value[:20000]
    return raw[:20000]


# --------------------------------------------------------------------------
# status for the interface
# --------------------------------------------------------------------------

def status(timeout: float = 8.0) -> dict[str, Any]:
    info = detect(timeout=timeout)
    with _CACHE_LOCK:
        _CACHE["detect"] = (time.monotonic(), info)
    root = workdir()
    cfg = config.get_config()
    auth = auth_status(info["path"]) if info["installed"] else \
        {"known": False, "logged_in": False, "method": "", "detail": ""}
    info.update({
        "enabled": enabled(),
        "workdir": str(root) if root else "",
        "workdir_set": root is not None,
        "allow_edits": allow_edits_configured(),
        "model": str(cfg.get("claude_code_model") or ""),
        "max_budget_usd": cfg.get("claude_code_max_budget_usd", 0),
        "allowed_tools": cfg.get("claude_code_allowed_tools", ""),
        "auth": auth,
        "pending": sum(1 for r in recent() if r["state"] == "pending"),
        # A request can only run when all of these are true; the interface
        # shows whichever one is missing instead of a generic failure.
        "ready": bool(info["installed"] and enabled() and root is not None),
    })
    return info

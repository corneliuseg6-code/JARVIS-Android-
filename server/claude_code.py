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
Nothing runs without a round trip. ``propose()`` records a request and returns
the exact argument vector that would be executed; only ``approve()`` starts it.
Edits are a second gate on top: they need the setting enabled *and*
``allow_edits`` on the approval, and the default run is plan-only, which
cannot write. ``--dangerously-skip-permissions`` is never passed.
"""

from __future__ import annotations

import os
import platform
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
# the request queue
# --------------------------------------------------------------------------

def _public(record: dict[str, Any]) -> dict[str, Any]:
    """What the browser may see: everything except the live process handle."""
    return {k: v for k, v in record.items() if k != "_thread"}


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


def _argv(binary: str, prompt: str, target: Path, mode: str, model: str, turns: int) -> list[str]:
    argv = [
        binary,
        "-p", prompt,
        "--output-format", "json",
        "--permission-mode", _CLI_MODE[mode],
        "--max-turns", str(turns),
        "--add-dir", str(target),
    ]
    if mode == PLAN:
        # Plan mode already refuses to write. --restricted also takes away the
        # tools that run commands, so a plan-only request cannot shell out.
        argv.append("--restricted")
    if model:
        argv += ["--model", model]
    return argv


def propose(prompt: str, *, directory: str = "", mode: str = PLAN) -> dict[str, Any]:
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
            str(cfg.get("claude_code_model") or "").strip(), turns,
        ),
        "version": found["version"],
        "result": "",
        "error": "",
        "finished": 0.0,
        "_thread": None,
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
        record["_thread"] = thread
    thread.start()
    return _public(record)


def _run(record: dict[str, Any]) -> None:
    try:
        done = subprocess.run(
            record["argv"],
            cwd=record["directory"],
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT,
            check=False,
            # The binary's own environment, unchanged: Claude Code owns its
            # credentials and JARVIS has no business adding to or reading them.
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        _finish(record, "error", error=f"The run exceeded {int(RUN_TIMEOUT)}s and was stopped.")
        return
    except OSError as exc:
        _finish(record, "error", error=f"Could not run Claude Code: {exc.strerror or exc}")
        return

    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        _finish(record, "error", error=detail[:2000] or f"Exited with {done.returncode}.")
        return
    _finish(record, "done", result=_text_of(done.stdout))


def _text_of(stdout: str) -> str:
    """
    Pull the answer out of `--output-format json`.

    The documented shape carries the reply on `result`; anything else is
    returned verbatim rather than being silently dropped.
    """
    import json as _json

    raw = (stdout or "").strip()
    if not raw:
        return ""
    try:
        payload = _json.loads(raw)
    except _json.JSONDecodeError:
        return raw[:20000]
    if isinstance(payload, dict):
        for key in ("result", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value[:20000]
    return raw[:20000]


def _finish(record: dict[str, Any], state: str, *, result: str = "", error: str = "") -> None:
    with _LOCK:
        record["state"] = state
        record["result"] = result
        record["error"] = error
        record["finished"] = time.time()
        record["_thread"] = None


# --------------------------------------------------------------------------
# status for the interface
# --------------------------------------------------------------------------

def status(timeout: float = 8.0) -> dict[str, Any]:
    info = detect(timeout=timeout)
    root = workdir()
    cfg = config.get_config()
    info.update({
        "enabled": enabled(),
        "workdir": str(root) if root else "",
        "workdir_set": root is not None,
        "allow_edits": allow_edits_configured(),
        "model": str(cfg.get("claude_code_model") or ""),
        "pending": sum(1 for r in recent() if r["state"] == "pending"),
        # A request can only run when all of these are true; the interface
        # shows whichever one is missing instead of a generic failure.
        "ready": bool(info["installed"] and enabled() and root is not None),
    })
    return info

"""JARVIS's workspace: a real folder on disk that it owns.

Seven sections, created on first use and never silently emptied:

    Projects  Research  Documents  Notes  Tasks  Results  Journal

Everything here is an ordinary file the user can open, edit or delete with
any file manager — this is a directory, not a database pretending to be one.
The folder network in the interface draws exactly what `tree()` reports, so a
folder on screen is a folder on disk.

Two rules hold everywhere in this module:

* **Nothing escapes the root.** Every path is resolved and checked against
  `paths.WORKSPACE_DIR`; `..`, an absolute path and a symlink pointing out
  are all refused. JARVIS may write here without asking, which is only safe
  because "here" cannot be talked into meaning somewhere else.
* **Deleting is not writing.** `write()` is ordinary work. `delete()` is kept
  separate and the API gates it, because losing a file the user wrote is not
  the same kind of mistake as adding one.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from . import paths

#: The sections, in the order the interface shows them. The key is the folder
#: name on disk; the label is what the user sees before translation.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("Projects", "Work with a beginning and an end"),
    ("Research", "What JARVIS found, with its sources"),
    ("Documents", "Finished things worth keeping"),
    ("Notes", "Working notes, short-lived by nature"),
    ("Tasks", "What is open and what is done"),
    ("Results", "Output: exports, reports, generated files"),
    ("Journal", "What JARVIS did, in order"),
)

#: Files larger than this are listed but never read into a response.
MAX_READ_BYTES = 400_000

#: How deep the tree walk goes. A workspace is a desk, not a filesystem.
MAX_DEPTH = 4

_SAFE_SEGMENT = re.compile(r"^[^\x00/\\]+$")


class WorkspaceError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# the root, and staying inside it
# --------------------------------------------------------------------------

def root() -> Path:
    base = paths.WORKSPACE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure() -> Path:
    """Create the sections if they are missing, and leave what is there."""
    base = root()
    for name, _description in SECTIONS:
        (base / name).mkdir(exist_ok=True)
    readme = base / "README.md"
    if not readme.exists():
        lines = [
            "# JARVIS workspace",
            "",
            "JARVIS keeps its own notes, research and task checkpoints here.",
            "These are ordinary files: open, edit or delete them with anything.",
            "",
        ]
        lines += [f"- **{name}/** — {description}" for name, description in SECTIONS]
        readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return base


def resolve(relative: str) -> Path:
    """
    A path inside the workspace, or a refusal.

    `resolve()` follows symlinks, so a link planted inside the workspace that
    points at the user's photos is caught here rather than being read as if
    it belonged to JARVIS.
    """
    base = ensure().resolve()
    raw = (relative or "").strip()
    # Checked before the slashes are trimmed: stripping first would turn
    # "/etc/passwd" into "etc/passwd" and quietly answer a different question
    # than the one that was asked.
    if raw.startswith(("/", "\\")) or Path(raw).is_absolute() or "\x00" in raw:
        raise WorkspaceError(f"{relative!r} is not a path inside the workspace")
    text = raw.strip("/")
    if not text:
        return base
    for segment in Path(text).parts:
        if segment in ("..", "."):
            raise WorkspaceError(f"{relative!r} may not step outside the workspace")
        if not _SAFE_SEGMENT.match(segment):
            raise WorkspaceError(f"{segment!r} is not a usable name")
    target = (base / text).resolve()
    if target != base and base not in target.parents:
        raise WorkspaceError(f"{relative!r} resolves outside the workspace")
    return target


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ensure().resolve()).as_posix()
    except ValueError:
        return path.name


# --------------------------------------------------------------------------
# reading the shape of it
# --------------------------------------------------------------------------

def _entry(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": _relative(path),
        "kind": "folder" if path.is_dir() else "file",
        "size": 0 if path.is_dir() else stat.st_size,
        "modified": int(stat.st_mtime),
    }


def _count(path: Path) -> tuple[int, int]:
    """(files, folders) directly inside, without walking the whole subtree."""
    files = folders = 0
    try:
        for child in path.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                folders += 1
            else:
                files += 1
    except OSError:
        pass
    return files, folders


def tree(depth: int = 2) -> dict[str, Any]:
    """
    The whole workspace as nodes and the links between them.

    Shaped for the folder network: `nodes` carries one entry per folder with
    its parent, so the interface can draw the links without walking the disk
    itself, and `sections` says which nodes are the seven top-level ones.
    """
    base = ensure()
    depth = max(1, min(MAX_DEPTH, int(depth or 2)))
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []

    nodes.append({
        "id": "", "name": "JARVIS", "path": "", "parent": None,
        "kind": "root", "files": 0, "folders": len(SECTIONS), "modified": 0,
    })

    def walk(path: Path, parent_id: str, level: int) -> None:
        if level > depth:
            return
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if child.name.startswith("."):
                continue
            if not child.is_dir():
                continue
            files, folders = _count(child)
            node_id = _relative(child)
            try:
                modified = int(child.stat().st_mtime)
            except OSError:
                modified = 0
            nodes.append({
                "id": node_id,
                "name": child.name,
                "path": node_id,
                "parent": parent_id,
                "kind": "section" if level == 1 else "folder",
                "files": files,
                "folders": folders,
                "modified": modified,
            })
            links.append({"from": parent_id, "to": node_id})
            walk(child, node_id, level + 1)

    walk(base, "", 1)
    return {
        "root": str(base),
        "nodes": nodes,
        "links": links,
        "sections": [name for name, _ in SECTIONS],
        "generated": int(time.time()),
    }


def listing(relative: str = "") -> dict[str, Any]:
    """What is directly inside one folder: the files a tapped node reveals."""
    target = resolve(relative)
    if not target.is_dir():
        raise WorkspaceError(f"{relative or '/'} is not a folder")
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            entries.append(_entry(child))
    except OSError as exc:
        raise WorkspaceError(str(exc)) from exc
    return {"path": _relative(target), "entries": entries}


def read(relative: str) -> dict[str, Any]:
    target = resolve(relative)
    if not target.is_file():
        raise WorkspaceError(f"{relative} is not a file")
    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise WorkspaceError(
            f"{relative} is {size // 1000}kB; too large to open here")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise WorkspaceError(f"{relative} is not text") from None
    return {"path": _relative(target), "text": text, "size": size}


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def write(relative: str, text: str) -> dict[str, Any]:
    """Create or replace a file. JARVIS may do this without asking."""
    target = resolve(relative)
    if target == ensure().resolve():
        raise WorkspaceError("a path is required")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text if isinstance(text, str) else str(text), encoding="utf-8")
    return _entry(target)


def make_folder(relative: str) -> dict[str, Any]:
    target = resolve(relative)
    target.mkdir(parents=True, exist_ok=True)
    return _entry(target)


def delete(relative: str) -> dict[str, Any]:
    """
    Remove a file or an empty folder.

    A section is never removed: the seven are the shape of the workspace, and
    `ensure()` would recreate an empty one anyway, which would look like the
    delete had silently failed.
    """
    target = resolve(relative)
    base = ensure().resolve()
    if target == base:
        raise WorkspaceError("the workspace itself cannot be deleted")
    if target.parent == base and target.name in {name for name, _ in SECTIONS}:
        raise WorkspaceError(f"{target.name} is a workspace section and stays")
    if not target.exists():
        raise WorkspaceError(f"{relative} does not exist")
    if target.is_dir():
        if any(target.iterdir()):
            raise WorkspaceError(f"{relative} is not empty")
        target.rmdir()
    else:
        target.unlink()
    return {"deleted": _relative(target)}


def journal(entry: str, *, kind: str = "note") -> dict[str, Any]:
    """
    Append a line to today's work journal.

    One file per day, appended to rather than rewritten, so a crash loses at
    most the line being written.
    """
    ensure()
    day = time.strftime("%Y-%m-%d")
    target = resolve(f"Journal/{day}.md")
    stamp = time.strftime("%H:%M")
    line = f"- **{stamp}** ({kind}) {entry.strip()}\n"
    if not target.exists():
        target.write_text(f"# Work journal — {day}\n\n", encoding="utf-8")
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return {"path": _relative(target), "line": line.strip()}


def status() -> dict[str, Any]:
    """A short summary for the status rail and the health endpoint."""
    base = ensure()
    files = folders = 0
    total = 0
    for path in base.rglob("*"):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            folders += 1
        else:
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return {
        "root": str(base),
        "state": "connected",
        "files": files,
        "folders": folders,
        "bytes": total,
        "sections": len(SECTIONS),
    }


# --------------------------------------------------------------------------
# which project is being worked on
# --------------------------------------------------------------------------

_ACTIVE: dict[str, Any] = {"path": "", "reason": "", "at": 0.0}


def set_active(path: str, reason: str = "") -> dict[str, Any]:
    """
    Mark the folder JARVIS is working in, for the interface to highlight.

    Only a folder that exists can be active: an invented name would light up
    a node that is not there, which is exactly the kind of decoration this
    workspace is meant to avoid.
    """
    cleaned = (path or "").strip().strip("/")
    if cleaned:
        target = resolve(cleaned)
        if not target.is_dir():
            raise WorkspaceError(f"{cleaned} is not a folder in the workspace")
        cleaned = _relative(target)
    _ACTIVE.update({"path": cleaned, "reason": reason[:200], "at": time.time()})
    return dict(_ACTIVE)


def active() -> dict[str, Any]:
    return dict(_ACTIVE)


def detect(text: str) -> str:
    """
    The workspace folder a piece of text is about, or "".

    Matched against real folder names only, longest first so "Mars Rover"
    wins over "Mars". This is how a node lights up while JARVIS talks about a
    project: the signal is the project's own name appearing, not a guess.
    """
    if not text:
        return ""
    haystack = text.lower()
    candidates: list[tuple[int, str]] = []
    for node in tree(depth=MAX_DEPTH)["nodes"]:
        name = (node.get("name") or "").strip()
        if node.get("kind") == "root" or len(name) < 3:
            continue
        if name.lower() in haystack:
            candidates.append((len(name), node["path"]))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def export() -> dict[str, Any]:
    """Everything in the workspace as one JSON document, for a backup."""
    base = ensure()
    files = []
    for path in sorted(base.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                files.append({"path": _relative(path), "skipped": "too large"})
                continue
            files.append({"path": _relative(path), "text": path.read_text(encoding="utf-8")})
        except (OSError, UnicodeDecodeError):
            files.append({"path": _relative(path), "skipped": "not text"})
    return {"root": str(base), "exported": int(time.time()), "files": files}


def backup(destination: str | None = None) -> dict[str, Any]:
    """Copy the whole workspace beside itself, stamped with the time."""
    base = ensure()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(destination) if destination else base.parent / f"workspace-backup-{stamp}"
    if target.exists():
        raise WorkspaceError(f"{target} already exists")
    shutil.copytree(base, target)
    return {"backup": str(target), "at": int(time.time())}


def write_json(relative: str, data: Any) -> dict[str, Any]:
    return write(relative, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

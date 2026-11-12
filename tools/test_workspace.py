#!/usr/bin/env python3
"""Checks for JARVIS's workspace: server/workspace.py.

Run with: python3 tools/test_workspace.py

The workspace is the one place JARVIS may write without being asked, so most
of what matters here is the boundary: a path that resolves outside the root
is a path to the user's own files.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_HOME = Path(tempfile.mkdtemp(prefix="jarvis-ws-"))
os.environ["JARVIS_HOME"] = str(_HOME)
os.environ["JARVIS_WORKSPACE"] = str(_HOME / "workspace")

from server import workspace  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not cond else ""))


def refuses(label: str, fn, *, contains: str = "") -> None:
    try:
        fn()
    except workspace.WorkspaceError as exc:
        ok(label, contains.lower() in str(exc).lower(), f"said: {exc}")
    except Exception as exc:  # noqa: BLE001
        ok(label, False, f"{type(exc).__name__}: {exc}")
    else:
        ok(label, False, "it was allowed")


def section(name: str) -> None:
    print(f"\n{name}")


def test_shape() -> None:
    section("The workspace creates itself")
    base = workspace.ensure()
    ok("the root exists", base.is_dir(), str(base))
    for name, _ in workspace.SECTIONS:
        ok(f"{name}/ exists", (base / name).is_dir())
    ok("a README explains what it is", (base / "README.md").is_file())
    ok("it is under JARVIS_HOME, not in the source tree",
       str(ROOT) not in str(base.resolve()), str(base))

    # Running twice must not empty anything.
    (base / "Notes" / "keep.md").write_text("mine\n", encoding="utf-8")
    workspace.ensure()
    ok("a second ensure() keeps existing files",
       (base / "Notes" / "keep.md").read_text() == "mine\n")


def test_boundary() -> None:
    section("Nothing escapes the root")
    base = workspace.ensure()
    refuses("a parent step is refused",
            lambda: workspace.resolve("../secrets.json"), contains="outside")
    refuses("a deep parent step is refused",
            lambda: workspace.resolve("Notes/../../../etc/passwd"), contains="outside")
    refuses("an absolute path is refused",
            lambda: workspace.resolve("/etc/passwd"), contains="inside the workspace")
    refuses("a null byte is refused",
            lambda: workspace.resolve("Notes/a\x00b"), contains="not")

    # A symlink is the interesting one: the name looks innocent.
    outside = _HOME / "private.txt"
    outside.write_text("the user's own file\n", encoding="utf-8")
    link = base / "Notes" / "innocent.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        ok("(symlinks unavailable on this filesystem; skipped)", True)
    else:
        refuses("a symlink pointing out is refused",
                lambda: workspace.read("Notes/innocent.txt"), contains="outside")
        link.unlink()

    ok("a plain path inside resolves", workspace.resolve("Notes").is_dir())
    ok("the empty path is the root itself",
       workspace.resolve("") == base.resolve())


def test_files() -> None:
    section("Reading and writing")
    workspace.write("Projects/Mars Rover/plan.md", "# Plan\n\nBuild it.\n")
    ok("a nested file is created",
       (workspace.ensure() / "Projects" / "Mars Rover" / "plan.md").is_file())
    ok("its parent folder was made on the way",
       (workspace.ensure() / "Projects" / "Mars Rover").is_dir())

    back = workspace.read("Projects/Mars Rover/plan.md")
    ok("it reads back exactly", back["text"] == "# Plan\n\nBuild it.\n", back["text"])
    ok("the path is reported relative to the root",
       back["path"] == "Projects/Mars Rover/plan.md", back["path"])

    entries = workspace.listing("Projects/Mars Rover")["entries"]
    ok("the listing shows the file", any(e["name"] == "plan.md" for e in entries))
    ok("with its real size", next(e for e in entries if e["name"] == "plan.md")["size"] > 0)

    refuses("reading a folder as a file is refused",
            lambda: workspace.read("Projects"), contains="not a file")
    refuses("listing a file as a folder is refused",
            lambda: workspace.listing("Projects/Mars Rover/plan.md"), contains="not a folder")
    refuses("a missing file is reported",
            lambda: workspace.read("Projects/nope.md"), contains="not a file")

    big = workspace.ensure() / "Notes" / "big.txt"
    big.write_text("x" * (workspace.MAX_READ_BYTES + 10), encoding="utf-8")
    refuses("an oversized file is not read into a response",
            lambda: workspace.read("Notes/big.txt"), contains="too large")
    big.unlink()


def test_tree() -> None:
    section("The folder network is the real folder tree")
    workspace.make_folder("Research/Mars")
    workspace.make_folder("Research/Mars/sources")
    data = workspace.tree(depth=3)
    ids = {n["id"] for n in data["nodes"]}

    ok("the root node is there", "" in ids)
    for name, _ in workspace.SECTIONS:
        ok(f"{name} is a node", name in ids)
    ok("a nested folder is a node", "Research/Mars" in ids)
    ok("and so is one below it", "Research/Mars/sources" in ids)
    ok("files are not nodes", not any(n["id"].endswith("plan.md") for n in data["nodes"]))

    links = {(l["from"], l["to"]) for l in data["links"]}
    ok("every section links to the root",
       all(("", name) in links for name, _ in workspace.SECTIONS))
    ok("a nested folder links to its parent", ("Research", "Research/Mars") in links)
    ok("every link joins two real nodes",
       all(a in ids and b in ids for a, b in links))
    ok("every node but the root has a parent in the set",
       all(n["parent"] in ids for n in data["nodes"] if n["parent"] is not None))

    mars = next(n for n in data["nodes"] if n["id"] == "Projects/Mars Rover")
    ok("a folder reports how many files it holds", mars["files"] == 1, str(mars))

    shallow = workspace.tree(depth=1)
    ok("depth limits the walk",
       not any(n["id"] == "Research/Mars" for n in shallow["nodes"]))
    deep = workspace.tree(depth=99)
    ok("an absurd depth is clamped rather than refused", len(deep["nodes"]) > 0)


def test_active() -> None:
    section("Highlighting the project being worked on")
    state = workspace.set_active("Projects/Mars Rover", "writing the plan")
    ok("the active folder is recorded", state["path"] == "Projects/Mars Rover")
    ok("with the reason", state["reason"] == "writing the plan")
    ok("and it reads back", workspace.active()["path"] == "Projects/Mars Rover")

    refuses("a folder that does not exist cannot be highlighted",
            lambda: workspace.set_active("Projects/Invented"), contains="not a folder")
    refuses("a file cannot be highlighted",
            lambda: workspace.set_active("Projects/Mars Rover/plan.md"), contains="not a folder")
    ok("clearing it is allowed", workspace.set_active("")["path"] == "")

    section("Detecting which project is being discussed")
    ok("a project named in a sentence is found",
       workspace.detect("I will update the Mars Rover plan now") == "Projects/Mars Rover",
       workspace.detect("I will update the Mars Rover plan now"))
    ok("the longest matching name wins, not the first",
       workspace.detect("about Mars Rover") == "Projects/Mars Rover",
       workspace.detect("about Mars Rover"))
    ok("an unrelated sentence matches nothing",
       workspace.detect("what is the weather in Lisbon") == "",
       workspace.detect("what is the weather in Lisbon"))
    ok("empty text matches nothing", workspace.detect("") == "")
    ok("a name that is not a folder is not invented",
       workspace.detect("the Jupiter Probe project") == "",
       workspace.detect("the Jupiter Probe project"))


def test_delete() -> None:
    section("Deleting is deliberate")
    workspace.write("Notes/throwaway.md", "junk\n")
    out = workspace.delete("Notes/throwaway.md")
    ok("a file can be removed", out["deleted"] == "Notes/throwaway.md", str(out))
    ok("and it is really gone",
       not (workspace.ensure() / "Notes" / "throwaway.md").exists())

    for name, _ in workspace.SECTIONS:
        refuses(f"the {name} section cannot be deleted",
                lambda n=name: workspace.delete(n), contains="section")

    workspace.make_folder("Notes/full")
    workspace.write("Notes/full/a.md", "a\n")
    refuses("a folder with something in it is not silently emptied",
            lambda: workspace.delete("Notes/full"), contains="not empty")
    workspace.delete("Notes/full/a.md")
    ok("an empty folder can go", workspace.delete("Notes/full")["deleted"] == "Notes/full")
    refuses("the workspace itself cannot be deleted",
            lambda: workspace.delete(""), contains="cannot be deleted")


def test_journal_and_backup() -> None:
    section("The work journal")
    first = workspace.journal("started the Mars Rover plan", kind="work")
    ok("a journal file is created", first["path"].startswith("Journal/"), first["path"])
    workspace.journal("finished it", kind="work")
    text = workspace.read(first["path"])["text"]
    ok("both lines are there, in order",
       text.index("started") < text.index("finished"), text)
    ok("the day's heading is written once", text.count("# Work journal") == 1)

    section("Export and backup")
    dump = workspace.export()
    ok("the export carries real file contents",
       any(f.get("text", "").startswith("# Plan") for f in dump["files"]),
       str([f["path"] for f in dump["files"]])[:160])
    out = workspace.backup()
    ok("a backup folder is written", Path(out["backup"]).is_dir(), str(out))
    ok("with the files in it",
       (Path(out["backup"]) / "Projects" / "Mars Rover" / "plan.md").is_file())
    ok("the original is untouched",
       (workspace.ensure() / "Projects" / "Mars Rover" / "plan.md").is_file())

    stats = workspace.status()
    ok("status counts real files", stats["files"] >= 2, str(stats))
    ok("and real folders", stats["folders"] >= len(workspace.SECTIONS), str(stats))


def main() -> int:
    print("JARVIS — workspace self-test (offline)")
    try:
        test_shape()
        test_boundary()
        test_files()
        test_tree()
        test_active()
        test_delete()
        test_journal_and_backup()
    finally:
        shutil.rmtree(_HOME, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  failed: " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

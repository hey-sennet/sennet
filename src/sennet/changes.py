"""What a task changed, in one view: each file it changed, with lines added and removed, its net
diff, and whether the change is committed.

From git where it can be: the diff from the commit the task started at (`start_commit`, kept when
its first turn starts, or its worktree was made) to the files as they are now, so a file edited
five times is one diff. In its own worktree (worktrees.py) every change there is the task's,
however it was made, a formatter or `sed` included. Sharing the project's checkout, only the files
its edits name are counted, so another task's or your own edits aren't; the files that changed
anyway since it started are listed apart, as `elsewhere`, without diffs.

A task from before start commits were kept counts from the commit its project was at when it was
made (`estimated`). Each file says whether it's `committed`: no different from what's committed now.

Without git, or once a worktree is removed, it falls back to the task's own edit events: each file's diffs one after another, `net` false, since they can't be added up.

Diffs come one file at a time, as one is opened (`diff`), and only for a file in the list.
"""
from __future__ import annotations

import re
from pathlib import Path

from .worktrees import WorktreeError, current_branch, git, is_repo, run

MAX_FILES = 300                 # files listed; more are counted, not shown
MAX_ELSEWHERE = 50
MAX_DIFF_LINES = 5000           # of one file's diff; the client shows fewer until asked
KINDS = {"A": "add", "D": "delete", "M": "update", "T": "update"}


class ChangesError(Exception):
    pass


def edited_paths(events: list[dict], cwd: Path) -> list[str]:
    """The files the task's edits name, relative to its directory, in the order first edited; one
    outside it (an absolute path elsewhere) is left out."""
    found: dict[str, None] = {}
    for event in events:
        if event["kind"] != "file_change" or event["data"].get("status") == "declined":
            continue
        for change in event["data"].get("changes") or []:
            if path := _relative(change.get("path") or "", cwd):
                found[path] = None
    return list(found)


def _relative(path: str, cwd: Path) -> str | None:
    if not path:
        return None
    if Path(path).is_absolute():
        try:
            path = str(Path(path).relative_to(cwd))
        except ValueError:
            return None
    path = path.removeprefix("./")
    return None if path.startswith("../") or path == ".." else path


def from_events(events: list[dict]) -> dict:
    """Each file the task's edits name, with its diffs one after another."""
    files: dict[str, dict] = {}
    for event in events:
        if event["kind"] != "file_change" or event["data"].get("status") == "declined":
            continue
        for change in event["data"].get("changes") or []:
            path = change.get("path") or ""
            if not path:
                continue
            entry = files.setdefault(path, {"path": path, "kind": change.get("kind") or "update", "added": 0,
                                            "removed": 0, "binary": False, "diffs": []})
            if change.get("kind") == "delete":
                entry["kind"] = "delete"
            diff = change.get("diff") or ""
            if diff:
                entry["diffs"].append(diff)
                added, removed = _counted(diff)
                entry["added"] += added
                entry["removed"] += removed
    return {"source": "events", "net": False, "files": list(files.values())}


def _counted(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


async def from_git(cwd: Path, since: str, only: list[str] | None) -> list[dict]:
    """The files changed from `since` to the working tree, untracked ones included; only these
    paths, if given."""
    if only is not None and not only:
        return []
    paths = ["--", *only] if only is not None else []
    numstat = await git("diff", "--no-renames", "--numstat", "-z", since, *paths, cwd=cwd)
    status = await git("diff", "--no-renames", "--name-status", "-z", since, *paths, cwd=cwd)
    kinds = dict(zip(*[iter(status.split("\0"))] * 2))  # status, path, status, path...
    kinds = {path: KINDS.get(code[:1], "update") for code, path in kinds.items() if path}
    files = []
    for entry in numstat.split("\0"):
        if not entry:
            continue
        added, removed, path = entry.split("\t", 2)
        binary = added == "-"
        files.append({"path": path, "kind": kinds.get(path, "update"), "added": 0 if binary else int(added),
                      "removed": 0 if binary else int(removed), "binary": binary})
    untracked = await git("ls-files", "--others", "--exclude-standard", "-z", *paths, cwd=cwd)
    for path in untracked.split("\0"):
        if path:
            added, binary = _lines(cwd / path)
            files.append({"path": path, "kind": "add", "added": added, "removed": 0, "binary": binary})
    if only is not None:  # in the order the task first edited them
        order = {path: i for i, path in enumerate(only)}
        files.sort(key=lambda f: order.get(f["path"], len(order)))
    return files


def _lines(path: Path) -> tuple[int, bool]:
    """A new file's lines, and whether it's binary."""
    try:
        data = path.read_bytes()
    except OSError:
        return 0, False
    if b"\0" in data[:8000]:
        return 0, True
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0), False


async def state(cwd: Path, since: str, files: list[str], branch: str | None, base: str | None) -> str | None:
    """Where the change is: `uncommitted` (any of it), `committed`, `pushed` (in the branch's
    upstream), or `merged` (a worktree's branch, in its base); None with nothing changed."""
    if not files:
        return None
    if (await git("status", "--porcelain", "--untracked-files=all", "--", *files, cwd=cwd)).strip():
        return "uncommitted"
    if branch and base and branch != base and (await run("merge-base", "--is-ancestor", branch, base, cwd=cwd))[0] == 0:
        return "merged"
    code, upstream, _ = await run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=cwd)
    if code == 0 and upstream.strip() and (await run("merge-base", "--is-ancestor", "HEAD", "@{upstream}", cwd=cwd))[0] == 0:
        return "pushed"
    return "committed"


async def changes(task: dict, project: dict, events: list[dict]) -> dict:
    """What the task changed (see the module's docstring): `source` (git or events), `net`, the
    `files`, `elsewhere` (changed since it started but not by its edits), `branch`, `base`, `state`."""
    worktree = Path(task["worktree"]) if task["worktree"] else None
    cwd = worktree if worktree and worktree.is_dir() else Path(project["path"])
    since = task["start_commit"]
    if since is None and worktree and task["branch"] and task["base"] and is_repo(project["path"]):
        # A worktree made before start commits were kept: from where its branch left its base.
        code, out, _ = await run("merge-base", task["base"], task["branch"], cwd=Path(project["path"]))
        since = out.strip() if code == 0 else None
    estimated = False
    if since is None and not worktree and is_repo(cwd):
        # A task from before start commits were kept: the commit the project was at when it was made.
        code, out, _ = await run("rev-list", "-1", "--first-parent", f"--before={int(task['created_at'])}", "HEAD", cwd=cwd)
        since, estimated = (out.strip() or None, True) if code == 0 else (None, False)
    worktree_gone = worktree is not None and not worktree.is_dir()
    if since is None or not is_repo(cwd) or worktree_gone:
        found = from_events(events)
        return {**found, "files": [_without_diffs(f) for f in found["files"]][:MAX_FILES], "more": max(len(found["files"]) - MAX_FILES, 0),
                "elsewhere": [], "branch": task["branch"], "base": task["base"], "state": None,
                "estimated": False, "gone": worktree_gone}
    try:
        only = None if worktree else edited_paths(events, cwd)
        files = await from_git(cwd, since, only)
        elsewhere = []
        if only is not None:
            every = await from_git(cwd, since, None)
            mine = set(only)
            elsewhere = [f["path"] for f in every if f["path"] not in mine]
        branch = task["branch"] if worktree else await current_branch(cwd)
        where = await state(cwd, since, [f["path"] for f in files], branch, task["base"] if worktree else None)
        uncommitted = await _uncommitted(cwd, [f["path"] for f in files])
        for entry in files:
            entry["committed"] = entry["path"] not in uncommitted
    except WorktreeError as error:
        raise ChangesError(str(error)) from None
    return {"source": "git", "net": True, "files": files[:MAX_FILES], "more": max(len(files) - MAX_FILES, 0),
            "elsewhere": elsewhere[:MAX_ELSEWHERE], "branch": branch, "base": task["base"], "state": where,
            "since": since[:12], "estimated": estimated, "gone": False}


async def _uncommitted(cwd: Path, files: list[str]) -> set[str]:
    """Those of the files that differ from what's committed, or aren't in git yet."""
    if not files:
        return set()
    out = await git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--", *files, cwd=cwd)
    return {entry[3:] for entry in out.split("\0") if len(entry) > 3}


def _without_diffs(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "diffs"}


async def diff(task: dict, project: dict, events: list[dict], path: str) -> dict:
    """One file's diff, as `changes` counts it; only a file it lists. `truncated` past MAX_DIFF_LINES."""
    found = await changes(task, project, events)
    if path not in {f["path"] for f in found["files"]}:
        raise ChangesError("no such file in what the task changed")
    if found["source"] == "events":
        text = "\n".join(next(f for f in from_events(events)["files"] if f["path"] == path)["diffs"])
    else:
        worktree = Path(task["worktree"]) if task["worktree"] else None
        cwd = worktree if worktree else Path(project["path"])
        since = found["since"]
        try:
            text = await git("diff", "--no-renames", "--no-color", since, "--", path, cwd=cwd)
            if not text:  # untracked: against nothing
                code, text, err = await run("diff", "--no-index", "--no-color", "--", "/dev/null", path, cwd=cwd)
                if code > 1:
                    raise WorktreeError(err.strip() or "git diff failed")
        except WorktreeError as error:
            raise ChangesError(str(error)) from None
        text = re.sub(r"^diff --git .*\n(?:(?:new|deleted) file mode .*\n|index .*\n)*", "", text, flags=re.M)
    # The file is named above its diff: its headers would only say it again.
    lines = [line for line in text.splitlines() if not re.match(r"(---|\+\+\+) (a/|b/|/dev/null)", line)]
    return {"path": path, "diff": "\n".join(lines[:MAX_DIFF_LINES]), "lines": len(lines),
            "truncated": len(lines) > MAX_DIFF_LINES}

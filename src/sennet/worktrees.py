"""A git worktree for each task, for a project with `worktrees` on.

A new task gets a worktree of its own, on a new branch (`sennet/<id>-<words from its title>`)
from the branch the project has checked out, its base. The worktree is under Sennet's data
directory, and the task's agent runs there, so tasks side by side don't edit the same files.
Approvals work as they do in the project: "Always" rules are the project's, and `workspace`
access is the worktree, the agent's working directory.

When the task is done its branch can be merged into its base, in the project's own checkout.
What the agent left uncommitted is committed first, titled with the task; then it's a
fast-forward if it can be, else a merge commit. A merge that would conflict isn't started, so
the checkout is never left half-merged: the conflicting files are named instead, for the agent
or you to resolve on the branch. The worktree can then be removed, with its branch or without;
with the branch kept, a follow-up puts the worktree back where it was, where the agent's
conversation expects its files.

Only a repository's top directory can have them (its `.git` is there), so the worktree's top is
the agent's directory, as the project's is.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

GIT_TIMEOUT_S = 60
BRANCH_PREFIX = "sennet/"
BRANCH_WORDS = 40       # characters of the title in a branch's name


class WorktreeError(Exception):
    pass


def is_repo(path: str | Path) -> bool:
    """The top directory of a git repository."""
    try:
        return os.path.exists(os.path.join(path, ".git"))
    except OSError:
        return False


def branch_name(task_id: int, title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:BRANCH_WORDS].rstrip("-")
    return f"{BRANCH_PREFIX}{task_id}-{words}" if words else f"{BRANCH_PREFIX}{task_id}"


async def run(*args: str, cwd: Path) -> tuple[int, str, str]:
    """Git in `cwd`: its exit code and what it printed. Paths given are paths, not patterns."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "LC_ALL": "C", "GIT_LITERAL_PATHSPECS": "1"}
    if not cwd.is_dir():
        raise WorktreeError(f"{cwd} doesn't exist")
    try:
        process = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise WorktreeError("git isn't installed") from None
    try:
        out, err = await asyncio.wait_for(process.communicate(), GIT_TIMEOUT_S)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise WorktreeError(f"git {args[0]} took more than {GIT_TIMEOUT_S} seconds") from None
    return process.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def git(*args: str, cwd: Path) -> str:
    """What git printed; WorktreeError, in git's words, if it failed."""
    code, out, err = await run(*args, cwd=cwd)
    if code != 0:
        said = [line.removeprefix("error: ").removeprefix("fatal: ").strip()
                for line in (err or out).strip().splitlines() if line.strip()]
        raise WorktreeError("; ".join(said[:3]) or f"git {args[0]} failed")
    return out


async def _branch_exists(repo: Path, branch: str) -> bool:
    code, _, _ = await run("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo)
    return code == 0


async def current_branch(repo: Path) -> str | None:
    """The branch the checkout is on; None when it isn't on one."""
    code, out, _ = await run("symbolic-ref", "--quiet", "--short", "HEAD", cwd=repo)
    return out.strip() if code == 0 else None


async def add(repo: Path, where: Path, task_id: int, title: str) -> dict:
    """A worktree at `where` on a new branch from what the project has checked out: its
    `worktree`, `branch`, `base` (the branch it came from; None if the checkout was on none) and
    `start_commit`, the commit it starts at, which what it changed is counted from (changes.py)."""
    if not is_repo(repo):
        raise WorktreeError(f"{repo} isn't the top of a git repository")
    if (await run("rev-parse", "--verify", "--quiet", "HEAD", cwd=repo))[0] != 0:
        raise WorktreeError("the repository has no commits yet")
    where = where.absolute()  # git runs in the project, which a relative path would be taken from
    base = await current_branch(repo)
    branch = branch_name(task_id, title)
    for n in range(2, 100):
        if not await _branch_exists(repo, branch):
            break
        branch = f"{branch_name(task_id, title)}-{n}"
    where.parent.mkdir(parents=True, exist_ok=True)
    start = (await git("rev-parse", "HEAD", cwd=repo)).strip()
    await git("worktree", "add", "--quiet", "-b", branch, str(where), start, cwd=repo)
    return {"worktree": str(where), "branch": branch, "base": base, "start_commit": start}


async def restore(repo: Path, where: Path, branch: str | None):
    """Put a removed worktree back, on its branch, where it was."""
    if branch is None or not await _branch_exists(repo, branch):
        raise WorktreeError("its worktree and branch were removed, so it can't go on; start a new task")
    await git("worktree", "prune", cwd=repo)
    where.parent.mkdir(parents=True, exist_ok=True)
    await git("worktree", "add", "--quiet", str(where), branch, cwd=repo)


async def status(repo: Path, where: Path, branch: str | None, base: str | None) -> dict:
    """Whether the worktree and branch are still there, the files changed from the base (left
    uncommitted or not), the commits not in the base yet, and what the project has checked out."""
    exists = where.is_dir()
    has_branch = branch is not None and await _branch_exists(repo, branch)
    has_base = base is not None and await _branch_exists(repo, base)
    files: set[str] = set()
    uncommitted = commits = 0
    if exists:
        porcelain = await git("status", "--porcelain", "--untracked-files=all", cwd=where)
        uncommitted = len(porcelain.splitlines())
    if has_branch and has_base:
        commits = int((await git("rev-list", "--count", f"{base}..{branch}", cwd=repo)).strip() or 0)
        fork = (await git("merge-base", base, branch, cwd=repo)).strip()
        if exists:  # the worktree's files as they are, committed or not
            files.update((await git("diff", "--name-only", fork, cwd=where)).splitlines())
            files.update((await git("ls-files", "--others", "--exclude-standard", cwd=where)).splitlines())
        else:
            files.update((await git("diff", "--name-only", fork, branch, cwd=repo)).splitlines())
    return {"worktree": str(where), "exists": exists, "branch": branch if has_branch else None, "base": base,
            "checked_out": await current_branch(repo) if is_repo(repo) else None,
            "files": sorted(f for f in files if f), "uncommitted": uncommitted, "commits": commits}


async def commit(where: Path, message: str) -> bool:
    """Commit everything left in the worktree, untracked files too; whether there was anything."""
    if not (await git("status", "--porcelain", cwd=where)).strip():
        return False
    await git("add", "--all", cwd=where)
    await git("commit", "--quiet", "-m", message, cwd=where)
    return True


async def merge(repo: Path, where: Path, branch: str, base: str | None, title: str) -> dict:
    """Merge the task's branch into its base in the project's checkout, which has to be on the
    base. `merged` says whether it was; if not, `conflicts` names the files that would conflict."""
    if base is None:
        raise WorktreeError("the project wasn't on a branch when the task started, so there's nothing to merge into")
    if not await _branch_exists(repo, branch):
        raise WorktreeError(f"the branch {branch} is gone")
    on = await current_branch(repo)
    if on != base:
        raise WorktreeError(f"the project has {on or 'no branch'} checked out; check out {base} to merge into it")
    committed = await commit(where, title) if where.is_dir() else False
    commits = int((await git("rev-list", "--count", f"{base}..{branch}", cwd=repo)).strip() or 0)
    if commits == 0:
        return {"merged": True, "commits": 0, "committed": committed, "fast_forward": False, "conflicts": []}
    # Tried apart from any checkout first (git 2.38 and later): 0 merges cleanly, 1 conflicts.
    code, out, _ = await run("merge-tree", "--write-tree", "--name-only", "--no-messages", base, branch, cwd=repo)
    if code == 1:
        conflicts = [line for line in out.splitlines()[1:] if line.strip()]
        return {"merged": False, "commits": commits, "committed": committed, "fast_forward": False,
                "conflicts": sorted(set(conflicts))}
    fast_forward = (await run("merge-base", "--is-ancestor", base, branch, cwd=repo))[0] == 0
    try:
        await git("merge", "--no-edit", "-m", f"Merge {branch}: {title}", branch, cwd=repo)
    except WorktreeError:
        # Changes in the checkout in the way are refused before anything is touched; a conflict
        # merge-tree couldn't foresee (an older git) is backed out.
        if (await run("rev-parse", "--verify", "--quiet", "MERGE_HEAD", cwd=repo))[0] == 0:
            await run("merge", "--abort", cwd=repo)
        raise
    return {"merged": True, "commits": commits, "committed": committed, "fast_forward": fast_forward, "conflicts": []}


async def remove(repo: Path, where: Path, branch: str | None, delete_branch: bool):
    """Remove the worktree, with anything left uncommitted in it, and its branch if asked. What's
    already gone is skipped."""
    if where.is_dir():
        await git("worktree", "remove", "--force", str(where), cwd=repo)
    await git("worktree", "prune", cwd=repo)
    if delete_branch and branch is not None and await _branch_exists(repo, branch):
        await git("branch", "-D", branch, cwd=repo)

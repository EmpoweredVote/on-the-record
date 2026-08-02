"""Locate the ev-accounts DATABASE_URL from anywhere in the on-the-record checkout.

`essentials.quotes` lives in the **ev-accounts** DB, a different database from the
on-the-record pipeline. Its connection string is not in this repo — it is read from the
sibling checkout's `ev-accounts/backend/.env`.

Finding that sibling means finding the repo root, and "N levels up from __file__" is wrong
the moment a skill runs from a git worktree: worktrees are created at
`<repo>/.claude/worktrees/<name>/`, so the naive walk lands on `.claude/worktrees/` and
looks for `.claude/worktrees/ev-accounts/backend/.env`, which does not exist.

Instead we walk up collecting every ancestor that has a `.git` entry, and — because a
worktree's `.git` is a *file* pointing at `<main>/.git/worktrees/<name>` — also the main
worktree each pointer leads back to. That covers worktrees nested inside the repo and
worktrees created outside it, without shelling out to git.

Usage from a skill script:

    import pathlib, sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "_shared"))
    from ev_env import ev_accounts_database_url

    url = ev_accounts_database_url(__file__)
"""
import os
import pathlib
import re

ENV_RELATIVE_PATH = pathlib.PurePath("ev-accounts", "backend", ".env")

_GITDIR_RE = re.compile(r"\s*gitdir:\s*(.+?)\s*$")
_DATABASE_URL_RE = re.compile(r'\s*(?:export\s+)?DATABASE_URL\s*=\s*"?([^"\n]+?)"?\s*$')


class EvAccountsEnvNotFound(RuntimeError):
    """No DATABASE_URL in the environment and none findable on disk."""


def _main_worktree(git_entry: pathlib.Path):
    """The main checkout a worktree's `.git` FILE points back to, or None.

    `.git` holds `gitdir: <main>/.git/worktrees/<name>`; the main checkout is three
    levels above that.
    """
    if not git_entry.is_file():
        return None
    try:
        match = _GITDIR_RE.match(git_entry.read_text().strip())
    except OSError:
        return None
    if not match:
        return None
    gitdir = pathlib.Path(match.group(1))
    if not gitdir.is_absolute():
        gitdir = git_entry.parent / gitdir
    gitdir = pathlib.Path(os.path.normpath(gitdir))
    if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
        return gitdir.parent.parent.parent
    return None


def repo_roots(start) -> list:
    """Every plausible repo root at or above `start`, nearest first, deduplicated.

    Each ancestor holding a `.git` entry counts, plus the main checkout that any worktree
    pointer resolves to.
    """
    start = pathlib.Path(start).resolve()
    roots = []

    def add(path):
        if path not in roots:
            roots.append(path)

    for directory in (start, *start.parents):
        git_entry = directory / ".git"
        if not git_entry.exists():
            continue
        add(directory)
        main = _main_worktree(git_entry)
        if main is not None:
            add(main)
    return roots


def candidate_env_paths(start) -> list:
    """The `ev-accounts/backend/.env` sibling of each candidate repo root."""
    return [root.parent / ENV_RELATIVE_PATH for root in repo_roots(start)]


def find_ev_accounts_env(start) -> pathlib.Path:
    """First existing ev-accounts/.env at or above `start`. Raises naming every path tried."""
    tried = candidate_env_paths(start)
    for path in tried:
        if path.is_file():
            return path
    raise EvAccountsEnvNotFound(_not_found_message(start, tried))


def parse_database_url(text: str):
    """DATABASE_URL from .env text, quoted or not, or None."""
    for line in text.splitlines():
        match = _DATABASE_URL_RE.match(line)
        if match:
            return match.group(1)
    return None


def ev_accounts_database_url(start) -> str:
    """The ev-accounts connection string.

    A `DATABASE_URL` in the environment always wins and needs no file on disk; otherwise
    the sibling `ev-accounts/backend/.env` is located relative to `start` (pass `__file__`).
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_path = find_ev_accounts_env(start)
    url = parse_database_url(env_path.read_text())
    if url:
        return url
    raise EvAccountsEnvNotFound(
        f"No DATABASE_URL line in {env_path}. Set the DATABASE_URL environment variable to "
        f"override, or add the line to that file."
    )


def _not_found_message(start, tried) -> str:
    paths = "\n".join(f"  - {p}" for p in tried) or f"  (no git checkout at or above {start})"
    return (
        "Could not find the ev-accounts backend .env holding DATABASE_URL.\n"
        f"Searched, starting from {pathlib.Path(start).resolve()}:\n"
        f"{paths}\n"
        "Set the DATABASE_URL environment variable to override, or check out ev-accounts "
        "alongside on-the-record."
    )

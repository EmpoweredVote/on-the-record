"""The ev-accounts .env lookup must survive being run from a git worktree.

The skills live at <repo>/.claude/skills/<skill>/scripts/, and worktrees are created at
<repo>/.claude/worktrees/<name>/ — so a naive "repo root is N levels up" walk lands on
.claude/worktrees/ and looks for a sibling ev-accounts that isn't there.
"""
import pytest

from ev_env import EvAccountsEnvNotFound, ev_accounts_database_url, find_ev_accounts_env

URL = "postgresql://user:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"


def _make_repo(root, *, url=URL, sibling=True):
    """<root>/on-the-record (a real repo) + <root>/ev-accounts/backend/.env."""
    repo = root / "on-the-record"
    (repo / ".git").mkdir(parents=True)
    scripts = repo / ".claude" / "skills" / "audit-quotes" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "db.py").write_text("")
    if sibling:
        backend = root / "ev-accounts" / "backend"
        backend.mkdir(parents=True)
        (backend / ".env").write_text(f'SECRET=x\nDATABASE_URL="{url}"\nOTHER=y\n')
    return repo


def _add_worktree(repo, name, *, at=None):
    """A git worktree whose .git is a FILE pointing into the main repo's .git/worktrees/<name>."""
    wt = at or (repo / ".claude" / "worktrees" / name)
    scripts = wt / ".claude" / "skills" / "audit-quotes" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "db.py").write_text("")
    (wt / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / name}\n")
    return wt


def test_database_url_environment_variable_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://from-env/db")
    # No repo, no .env file anywhere — the override must not need one.
    assert ev_accounts_database_url(tmp_path / "nowhere" / "db.py") == "postgresql://from-env/db"


def test_resolves_the_sibling_env_from_the_main_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = _make_repo(tmp_path)
    start = repo / ".claude" / "skills" / "audit-quotes" / "scripts" / "db.py"
    assert ev_accounts_database_url(start) == URL


def test_resolves_the_sibling_env_from_inside_a_worktree(tmp_path, monkeypatch):
    """The repro: .claude/worktrees/<name>/ must not resolve to .claude/worktrees/ev-accounts."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = _make_repo(tmp_path)
    wt = _add_worktree(repo, "upbeat-keller-a9d516")
    start = wt / ".claude" / "skills" / "audit-quotes" / "scripts" / "db.py"
    assert ev_accounts_database_url(start) == URL


def test_resolves_from_a_worktree_created_outside_the_repo(tmp_path, monkeypatch):
    """git worktree add ../elsewhere — only the .git pointer file leads back to the main repo."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = _make_repo(tmp_path)
    wt = _add_worktree(repo, "elsewhere", at=tmp_path / "detached" / "elsewhere")
    start = wt / ".claude" / "skills" / "audit-quotes" / "scripts" / "db.py"
    assert ev_accounts_database_url(start) == URL


def test_reads_an_unquoted_value(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = _make_repo(tmp_path)
    (tmp_path / "ev-accounts" / "backend" / ".env").write_text(f"DATABASE_URL={URL}\n")
    start = repo / ".claude" / "skills" / "audit-quotes" / "scripts" / "db.py"
    assert ev_accounts_database_url(start) == URL


def test_error_names_every_path_it_tried(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = _make_repo(tmp_path, sibling=False)
    wt = _add_worktree(repo, "upbeat-keller-a9d516")
    start = wt / ".claude" / "skills" / "audit-quotes" / "scripts" / "db.py"

    with pytest.raises(EvAccountsEnvNotFound) as excinfo:
        find_ev_accounts_env(start)

    message = str(excinfo.value)
    assert str(tmp_path / "ev-accounts" / "backend" / ".env") in message
    assert str(repo / ".claude" / "worktrees" / "ev-accounts" / "backend" / ".env") in message
    assert "DATABASE_URL" in message  # points at the override


def test_error_when_the_env_file_has_no_database_url(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = _make_repo(tmp_path)
    env = tmp_path / "ev-accounts" / "backend" / ".env"
    env.write_text("SECRET=x\n")
    start = repo / ".claude" / "skills" / "audit-quotes" / "scripts" / "db.py"

    with pytest.raises(EvAccountsEnvNotFound) as excinfo:
        ev_accounts_database_url(start)
    assert str(env) in str(excinfo.value)

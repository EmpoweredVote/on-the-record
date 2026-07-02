from __future__ import annotations

import os


def test_load_env_local_setdefaults(tmp_path, monkeypatch):
    from gui.env import load_env_local
    envfile = tmp_path / ".env.local"
    envfile.write_text("DATABASE_URL=postgres://x\n# comment\nRENDER_DEPLOY_HOOK_URL=https://h\nBLANK=\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    load_env_local(envfile)
    assert os.environ["DATABASE_URL"] == "postgres://x"
    assert os.environ["RENDER_DEPLOY_HOOK_URL"] == "https://h"


def test_load_env_local_does_not_override_existing(tmp_path, monkeypatch):
    from gui.env import load_env_local
    envfile = tmp_path / ".env.local"
    envfile.write_text("DATABASE_URL=fromfile\n")
    monkeypatch.setenv("DATABASE_URL", "preset")
    load_env_local(envfile)
    assert os.environ["DATABASE_URL"] == "preset"  # setdefault semantics


def test_load_env_local_missing_file_is_noop(tmp_path):
    from gui.env import load_env_local
    load_env_local(tmp_path / "nope.env")  # must not raise

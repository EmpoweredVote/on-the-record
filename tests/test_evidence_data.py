import os
from src.evidence.data import database_url


def test_database_url_prefers_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://from-env/x")
    assert database_url() == "postgres://from-env/x"


def test_database_url_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p = tmp_path / ".env"
    p.write_text('DATABASE_URL="postgres://from-file/y"\nOTHER=1\n')
    assert database_url(str(p)) == "postgres://from-file/y"

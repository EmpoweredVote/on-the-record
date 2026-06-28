from __future__ import annotations

import run_local


class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.executed = sql
    def fetchall(self): return self._rows


class _Conn:
    def __init__(self, rows): self._rows = rows
    def cursor(self): return _Cur(self._rows)
    def close(self): pass


def test_published_meeting_slugs(monkeypatch):
    import psycopg2
    monkeypatch.setattr("src.publish._require_db_url", lambda: "postgresql://x")
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: _Conn([("m1",), ("m2",)]))
    assert run_local._published_meeting_slugs() == {"m1", "m2"}

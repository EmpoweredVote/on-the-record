from __future__ import annotations

import gui.publish_api as pub


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return self._rows.pop(0) if self._rows else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)
        self.committed = False
    def cursor(self): return self.cursor_obj
    def commit(self): self.committed = True
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_db(monkeypatch, rows):
    conn = _FakeConn(list(rows))
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(pub.psycopg2, "connect", lambda url: conn)
    return conn


def test_meeting_published_id_found(monkeypatch):
    conn = _patch_db(monkeypatch, [("uuid-123",)])
    assert pub.meeting_published_id("2026-02-04-council") == "uuid-123"


def test_meeting_published_id_absent(monkeypatch):
    _patch_db(monkeypatch, [])  # no row
    assert pub.meeting_published_id("ghost") is None


def test_meeting_published_id_no_db_url(monkeypatch):
    monkeypatch.setattr(pub, "_db_url", lambda: None)
    assert pub.meeting_published_id("x") is None  # not configured -> None, no crash


def test_update_supabase_metadata_updates_when_published(monkeypatch):
    conn = _patch_db(monkeypatch, [("uuid-123",)])  # SELECT id finds a row
    ok = pub.update_supabase_metadata("2026-02-04-council", {
        "title": "Fixed Title", "city": "Bloomington", "date": "2026-02-04",
        "meeting_type": "Special Session", "event_kind": "council"})
    assert ok is True
    assert conn.committed is True
    # an UPDATE ... WHERE slug was issued
    sqls = " ".join(sql for sql, _ in conn.cursor_obj.executed).lower()
    assert "update meetings.meetings" in sqls and "where slug" in sqls


def test_update_supabase_metadata_skips_when_unpublished(monkeypatch):
    conn = _patch_db(monkeypatch, [])  # no row
    ok = pub.update_supabase_metadata("ghost", {"title": "x"})
    assert ok is False
    assert conn.committed is False

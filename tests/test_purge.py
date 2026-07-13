# tests/test_purge.py
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _local_meeting(mdir, *, audio_source=None):
    mdir.mkdir()
    payload = {"meeting_id": mdir.name}
    if audio_source is not None:
        payload["audio_source"] = audio_source
    (mdir / "transcript_named.json").write_text(json.dumps(payload))
    (mdir / "audio.opus").write_bytes(b"OPUS")


def test_purge_rejects_unsafe_id(tmp_meetings_dir, monkeypatch):
    from src import purge

    monkeypatch.setattr(purge, "_db_url", lambda: None)
    assert purge.purge_meeting("../escape")["status"] == "invalid"
    assert purge.purge_meeting(".")["status"] == "invalid"


def test_purge_not_found_when_no_dir_no_db(tmp_meetings_dir, monkeypatch):
    from src import purge

    monkeypatch.setattr(purge, "_db_url", lambda: None)
    result = purge.purge_meeting("ghost")
    assert result["status"] == "not_found"
    assert result["db_deleted"] is False
    assert result["local_deleted"] is False


def test_purge_local_only_deletes_dir(tmp_meetings_dir, monkeypatch):
    from src import purge

    monkeypatch.setattr(purge, "_db_url", lambda: None)  # no DB configured
    monkeypatch.setattr(purge, "_profile_contaminated", lambda slug: False)
    mdir = tmp_meetings_dir / "2026-02-04-council"
    _local_meeting(mdir)

    result = purge.purge_meeting("2026-02-04-council")

    assert result["status"] == "deleted"
    assert result["local_deleted"] is True
    assert result["db_deleted"] is False
    assert not mdir.exists()


def test_purge_sets_profile_contamination(tmp_meetings_dir, monkeypatch):
    from src import purge

    monkeypatch.setattr(purge, "_db_url", lambda: None)
    monkeypatch.setattr(purge, "_profile_contaminated", lambda slug: slug == "2026-02-04-council")
    mdir = tmp_meetings_dir / "2026-02-04-council"
    _local_meeting(mdir)

    result = purge.purge_meeting("2026-02-04-council")
    assert result["profile_contamination"] is True


class _FakeCursor:
    def __init__(self, script):
        self._script = script  # dict: sql-substring -> fetchone/fetchall result
        self.executed = []      # list of (sql, params)
        self.rowcount = 1
        self._last = None
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last = None
        for key, val in self._script.items():
            if key in sql:
                self._last = val
    def fetchone(self):
        return self._last
    def fetchall(self):
        return self._last or []
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False
    def cursor(self): return self._cursor
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True


def test_delete_meeting_db_deletes_children_then_parent(monkeypatch):
    from src import purge

    cur = _FakeCursor({"SELECT id FROM meetings.meetings": ("uuid-123",)})
    conn = _FakeConn(cur)
    monkeypatch.setattr(purge, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(purge.psycopg2, "connect", lambda url: conn)

    db_deleted, rows = purge._delete_meeting_db("2026-02-04-council")

    assert db_deleted is True
    assert conn.committed is True
    stmts = [sql for sql, _ in cur.executed]
    order = [s for s in stmts if "DELETE FROM" in s]
    assert order[-1].strip().startswith("DELETE FROM meetings.meetings")
    joined = " ".join(order)
    for child in ("segments", "speakers", "event_races", "meeting_topics", "event_orgs"):
        assert f"meetings.{child}" in joined
    for sql, params in cur.executed:
        if "meetings.event_orgs" in sql:
            assert params == ("2026-02-04-council",)
        if "meetings.segments" in sql:
            assert params == ("uuid-123",)


def test_delete_meeting_db_no_row(monkeypatch):
    from src import purge

    cur = _FakeCursor({"SELECT id FROM meetings.meetings": None})
    conn = _FakeConn(cur)
    monkeypatch.setattr(purge, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(purge.psycopg2, "connect", lambda url: conn)

    db_deleted, rows = purge._delete_meeting_db("ghost")
    assert db_deleted is False
    assert rows == {}


def test_find_orphan_quotes_matches_by_youtube_id(monkeypatch):
    from src import purge

    rows = [(1, "pol-1", "housing", "https://youtu.be/abc123?t=5s", "We must build more homes")]
    cur = _FakeCursor({"FROM essentials.quotes": rows})
    conn = _FakeConn(cur)
    monkeypatch.setattr(purge, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(purge.psycopg2, "connect", lambda url: conn)

    found = purge._find_orphan_quotes("https://www.youtube.com/watch?v=abc123")
    assert len(found) == 1
    assert found[0]["id"] == 1 and found[0]["politician_id"] == "pol-1"
    like_param = [p for sql, p in cur.executed if "essentials.quotes" in sql][0]
    assert like_param == ("%abc123%",)


def test_purge_skips_local_delete_when_db_delete_raises(tmp_meetings_dir, monkeypatch):
    from src import purge

    mdir = tmp_meetings_dir / "2026-02-04-council"
    _local_meeting(mdir)

    class _RaisingCursor(_FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "DELETE FROM meetings.segments" in sql:
                raise RuntimeError("db boom")

    cur = _RaisingCursor({"SELECT id FROM meetings.meetings": ("uuid-123",)})
    conn = _FakeConn(cur)
    monkeypatch.setattr(purge, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(purge.psycopg2, "connect", lambda url: conn)
    monkeypatch.setattr(purge, "_profile_contaminated", lambda slug: False)

    with pytest.raises(RuntimeError, match="db boom"):
        purge.purge_meeting("2026-02-04-council")

    assert conn.rolled_back is True
    assert mdir.exists()  # local folder NOT deleted after a failed DB delete


def test_find_orphan_quotes_is_read_only(monkeypatch):
    from src import purge

    cur = _FakeCursor({"FROM essentials.quotes": []})
    conn = _FakeConn(cur)
    monkeypatch.setattr(purge, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(purge.psycopg2, "connect", lambda url: conn)

    purge._find_orphan_quotes("https://youtu.be/abc123")

    for sql, _ in cur.executed:
        assert "DELETE" not in sql.upper()
        assert "UPDATE" not in sql.upper()
    assert conn.committed is False  # read-only: no commit


def test_format_delete_summary_lists_blast_radius():
    import run_local

    result = {
        "meeting_id": "2026-02-04-council", "status": "deleted",
        "db_deleted": True, "rows_deleted": {"segments": 120, "meetings": 1},
        "local_deleted": True,
        "quotes_found": [{"id": 7, "politician_id": "p1", "topic_key": "housing",
                          "source_url": "u", "preview": "we must build"}],
        "profile_contamination": True,
    }
    text = run_local._format_delete_summary(result)
    assert "2026-02-04-council" in text
    assert "segments: 120" in text
    assert "1 quote" in text.lower() or "quotes: 1" in text.lower() or "found (not deleted): 1" in text.lower()
    assert "reenroll_profiles.py" in text  # profile warning surfaced

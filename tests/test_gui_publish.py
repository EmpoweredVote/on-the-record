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


import json
import pytest


def _write_meeting(mdir):
    from src.models import Meeting, Segment, SpeakerMapping
    m = Meeting(meeting_id=mdir.name, city="Bloomington", date="2026-02-04",
                meeting_type="Regular Session", title=None, event_kind="council",
                segments=[Segment(segment_id=0, start_time=0.0, end_time=5.0,
                                  speaker_label="SPEAKER_00", speaker_name="X")],
                speakers={"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="X")})
    (mdir / "transcript_named.json").write_text(json.dumps(m.to_dict()))


def test_apply_metadata_edit_writes_local_and_freezes_slug(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    # no DB configured -> Supabase skipped, local still written
    monkeypatch.setattr(pub, "_db_url", lambda: None)

    res = pub.apply_metadata_edit("2026-02-04-council",
                                  {"title": "Budget Hearing", "meeting_type": "Special Session"})
    assert res["local"] is True
    assert res["supabase"] is False

    data = json.loads((mdir / "transcript_named.json").read_text())
    assert data["title"] == "Budget Hearing"
    assert data["meeting_type"] == "Special Session"
    assert data["meeting_id"] == "2026-02-04-council"   # FROZEN — slug/id unchanged
    assert mdir.name == "2026-02-04-council"             # dir not renamed
    # pipeline_state display fields updated too
    from src.checkpoint import PipelineState
    assert PipelineState(mdir).meeting_type == "Special Session"


def test_apply_metadata_edit_pushes_to_supabase_when_published(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    calls = {}
    monkeypatch.setattr(pub, "update_supabase_metadata",
                        lambda mid, fields: (calls.__setitem__("args", (mid, fields)), True)[1])
    res = pub.apply_metadata_edit("2026-02-04-council", {"title": "New"})
    assert res["supabase"] is True
    assert calls["args"][0] == "2026-02-04-council"
    assert calls["args"][1]["title"] == "New"


def test_apply_metadata_edit_unknown_meeting(tmp_meetings_dir):
    assert pub.apply_metadata_edit("ghost", {"title": "x"}) is None

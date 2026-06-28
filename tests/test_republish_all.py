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


import json
import argparse
from src.models import Meeting, SpeakerMapping


def _write_meeting(meeting_dir, mid):
    meeting_dir.mkdir(parents=True, exist_ok=True)
    m = Meeting(meeting_id=mid, city="X", date="2026-04-01",
                speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="A")})
    (meeting_dir / "transcript_named.json").write_text(json.dumps(m.to_dict()))


def _args(**over):
    ns = argparse.Namespace(dry_run=False, reenroll=False, no_deploy=False)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _common(monkeypatch, tmp_path, published, publish_rec, deploy_rec, fail=()):
    meetings_root = tmp_path / "meetings"
    for mid in ["m1", "m2", "m3"]:
        _write_meeting(meetings_root / mid, mid)
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    monkeypatch.setattr(run_local, "_published_meeting_slugs", lambda: set(published))

    def fake_publish(meeting, body_slug=None, trigger_deploy=True):
        if meeting.meeting_id in fail:
            raise RuntimeError("boom")
        publish_rec.append((meeting.meeting_id, trigger_deploy))
    monkeypatch.setattr("src.publish.publish_meeting", fake_publish)
    monkeypatch.setattr(run_local, "_trigger_render_deploy", lambda: deploy_rec.append(1))


def test_republish_all_publishes_only_published_meetings(tmp_path, monkeypatch):
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1", "m3"], pub, dep)
    run_local._republish_all(_args())
    assert {p[0] for p in pub} == {"m1", "m3"}          # m2 (unpublished) skipped
    assert all(p[1] is False for p in pub)              # per-publish deploy suppressed
    assert dep == [1]                                   # exactly one deploy at the end


def test_republish_all_dry_run_writes_nothing(tmp_path, monkeypatch):
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1"], pub, dep)
    run_local._republish_all(_args(dry_run=True))
    assert pub == [] and dep == []


def test_republish_all_no_deploy(tmp_path, monkeypatch):
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1"], pub, dep)
    run_local._republish_all(_args(no_deploy=True))
    assert {p[0] for p in pub} == {"m1"} and dep == []


def test_republish_all_continues_past_failure_and_exits_nonzero(tmp_path, monkeypatch):
    import pytest
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1", "m2", "m3"], pub, dep, fail=("m2",))
    with pytest.raises(SystemExit) as ei:
        run_local._republish_all(_args())
    assert {p[0] for p in pub} == {"m1", "m3"}          # m2 failed but others ran
    assert dep == [1]                                   # deploy still fires
    assert ei.value.code != 0


def test_republish_all_reenroll_runs_subprocess(tmp_path, monkeypatch):
    pub, dep, sub = [], [], []
    _common(monkeypatch, tmp_path, ["m1"], pub, dep)
    monkeypatch.setattr(run_local.subprocess, "run",
                        lambda *a, **k: sub.append(a) or type("R", (), {"returncode": 0})())
    run_local._republish_all(_args(reenroll=True))
    assert sub  # reenroll subprocess invoked

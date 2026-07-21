# tests/test_gui_batch.py
from __future__ import annotations

import gui.batch as batch
from gui.runner import RunParams


def _params(**kw):
    base = dict(input="https://x/v", date="2026-05-01",
                meeting_type="Interview", event_kind="news_clip")
    base.update(kw)
    return RunParams(**base)


def _running(mid):   # a run_status dict for a live run
    return {"meeting_id": mid, "completed_stage": 2, "stage_label": "Speakers separated",
            "running": True, "exit_code": None, "log_tail": ""}


def _finished(mid):
    return {"meeting_id": mid, "completed_stage": 7, "stage_label": "Exported",
            "running": False, "exit_code": 0, "log_tail": ""}


def test_launch_or_enqueue_starts_when_under_cap(tmp_meetings_dir, monkeypatch):
    launched = []
    monkeypatch.setattr(batch.runner, "launch_run",
                        lambda p, **kw: launched.append(p) or "2026-05-01-x-interview")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    outcome, mid = batch.launch_or_enqueue(_params())
    assert outcome == "started" and mid == "2026-05-01-x-interview"
    assert len(launched) == 1


def test_launch_or_enqueue_pends_when_at_cap(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    assert batch.launch_or_enqueue(_params())[0] == "started"       # fills the 1 slot
    outcome, mid = batch.launch_or_enqueue(_params(input="https://x/v2"))
    assert outcome == "pending" and mid is None
    st = batch.status()
    assert st["counts"]["pending"] == 1
    assert st["pending"][0]["pending_id"] >= 1                      # stable id assigned


def test_running_count_prunes_finished(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(2)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "mA")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())                              # active=[mA] running
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _finished(mid))  # mA done
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "mB")
    outcome, mid = batch.launch_or_enqueue(_params(input="https://x/v2"))
    assert outcome == "started" and mid == "mB"                     # mA pruned, slot free

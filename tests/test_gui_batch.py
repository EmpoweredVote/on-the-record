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
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "mA")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())                              # active=[mA] running
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _finished(mid))  # mA done
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "mB")
    outcome, mid = batch.launch_or_enqueue(_params(input="https://x/v2"))
    assert outcome == "started" and mid == "mB"                     # mA pruned, slot free


# tests/test_gui_batch.py  (append; reuses _params/_running/_finished)
def test_tick_promotes_pending_when_slot_frees(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())                       # m1 running (slot full)
    batch.launch_or_enqueue(_params(input="https://x/v2"))   # pending
    batch._tick()                                            # slot still full -> no launch
    assert batch.status()["counts"]["pending"] == 1
    # m1 finishes -> tick promotes the pending item
    monkeypatch.setattr(batch.runner, "run_status",
                        lambda mid: _finished(mid) if mid == "m1" else _running(mid))
    launched = []
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: launched.append(p) or "m2")
    batch._tick()
    assert launched and batch.status()["counts"]["pending"] == 0


def test_tick_skip_and_continue_on_launch_error(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    batch.launch_or_enqueue(_params())                       # m1 running (cap full)
    batch.launch_or_enqueue(_params(input="bad"))            # pending #1
    batch.launch_or_enqueue(_params(input="good"))           # pending #2
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _finished(mid))  # all free
    calls = []
    def flaky(p, **kw):
        calls.append(p.input)
        if p.input == "bad":
            raise RuntimeError("bad source")
        return "mgood"
    monkeypatch.setattr(batch.runner, "launch_run", flaky)
    batch._tick()
    assert "bad" in calls and "good" in calls                # both tried (skip-and-continue)
    assert batch.status()["counts"]["pending"] == 0          # both drained


def test_remove_pending_and_clamp(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())
    batch.launch_or_enqueue(_params(input="https://x/v2"))   # pending
    pid = batch.status()["pending"][0]["pending_id"]
    assert batch.remove_pending(pid) is True
    assert batch.status()["counts"]["pending"] == 0
    assert batch.remove_pending(pid) is False                # already gone
    batch.set_max_concurrent(99)
    assert batch._load()["max_concurrent"] == 10             # clamped to _MAX_CAP


def test_start_scheduler_idempotent(tmp_meetings_dir):
    import threading
    batch.start_scheduler(interval=999)
    batch.start_scheduler(interval=999)                      # second call is a no-op
    assert [t.name for t in threading.enumerate()].count("batch-scheduler") == 1


# tests/test_gui_batch.py  (append)
from fastapi.testclient import TestClient
from gui.app import create_app


def test_batch_status_route(tmp_meetings_dir, monkeypatch):
    monkeypatch.setattr(batch, "status",
                        lambda: {"counts": {"running": 2, "pending": 1, "max": 8},
                                 "running": [{"meeting_id": "m1", "stage": 3,
                                              "stage_label": "Transcribed", "running": True,
                                              "exit_code": None}],
                                 "pending": [{"pending_id": 9, "label": "X",
                                              "event_kind": "news_clip", "derived_id": "d"}]})
    r = TestClient(create_app()).get("/batch/status")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["running"] == 2
    assert body["running"][0]["meeting_id"] == "m1"


def test_batch_max_route(tmp_meetings_dir):
    client = TestClient(create_app())
    r = client.post("/batch/max", data={"n": "5"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert batch._load()["max_concurrent"] == 5


def test_batch_remove_pending_route(tmp_meetings_dir, monkeypatch):
    removed = {}
    def fake_remove(pid):
        removed["pid"] = pid
        return True
    monkeypatch.setattr(batch, "remove_pending", fake_remove)
    r = TestClient(create_app()).post("/batch/pending/7/remove", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert removed["pid"] == 7


def test_status_survives_missing_meetings_dir(tmp_path, monkeypatch):
    missing = tmp_path / "no-such-dir"
    monkeypatch.setattr("src.config.MEETINGS_DIR", missing)
    out = batch.status()                       # must not raise
    assert out["counts"] == {"running": 0, "pending": 0, "max": 8}
    assert missing.exists()                    # _save created the dir

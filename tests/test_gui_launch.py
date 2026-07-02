from __future__ import annotations

import json

from fastapi.testclient import TestClient

from gui.app import create_app


def test_new_form_renders(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert "<form" in body and 'action="/new"' in body
    assert 'name="input"' in body and 'name="event_kind"' in body


def test_post_new_launches_and_redirects(tmp_meetings_dir, monkeypatch):
    from gui import runner
    launched = {}

    def fake_launch(p, **kw):
        launched["params"] = p
        return "2026-02-10-regular"

    monkeypatch.setattr(runner, "launch_run", fake_launch)
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "Bloomington", "compute": "local", "diarizer": "oss",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-02-10-regular/run"
    assert launched["params"].input == "https://x/v"


def test_post_new_missing_input_is_rejected(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.post("/new", data={"input": "", "date": "2026-02-10",
                                     "meeting_type": "Regular", "event_kind": "council"},
                       follow_redirects=False)
    assert resp.status_code == 400


def test_run_page_and_status_json(tmp_meetings_dir, tagged_meeting_dir, monkeypatch):
    # Seed a meeting with a run sidecar + state so run_status returns data.
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=2)
    (mdir / "gui_run.json").write_text(json.dumps({"pid": 1, "cmd": [], "status": "running"}))
    (mdir / "gui_run.log").write_text("STAGE 2: Speaker Diarization\n")
    client = TestClient(create_app())

    page = client.get("/meetings/2026-02-10-regular/run")
    assert page.status_code == 200
    assert "run.js" in page.text and "Diarization" in page.text or "stepper" in page.text.lower()

    st = client.get("/meetings/2026-02-10-regular/run/status")
    assert st.status_code == 200
    body = st.json()
    assert body["completed_stage"] == 2
    assert "STAGE 2" in body["log_tail"]

    assert client.get("/meetings/ghost/run/status").status_code == 404


def test_formmeta_covers_all_event_kinds():
    from gui.formmeta import EVENT_KIND_HELP, CITY_REQUIRED_KINDS
    from src.event_kinds import EVENT_KINDS
    # every controlled event kind has help text
    assert set(EVENT_KIND_HELP) == set(EVENT_KINDS)
    assert all(v.strip() for v in EVENT_KIND_HELP.values())
    # deliberative kinds require a city
    assert CITY_REQUIRED_KINDS == {"council", "school_board"}


def test_formmeta_compute_and_diarizer_help():
    from gui.formmeta import COMPUTE_HELP, DIARIZER_HELP
    assert set(COMPUTE_HELP) == {"local", "modal"}
    assert set(DIARIZER_HELP) == {"oss", "api", "vibevoice"}
    assert all(v.strip() for v in {**COMPUTE_HELP, **DIARIZER_HELP}.values())


def test_post_new_council_requires_city(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "",  # no city
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert "city" in resp.text.lower()


def test_post_new_council_with_city_launches(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-02-10-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "Bloomington",
    }, follow_redirects=False)
    assert resp.status_code == 303


def test_post_new_other_kind_needs_no_city(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-02-10-clip")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Clip",
        "event_kind": "news_clip", "city": "",
    }, follow_redirects=False)
    assert resp.status_code == 303  # news_clip doesn't require a city


def test_new_form_shows_help_and_preview(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    # event-kind help text is rendered (from formmeta)
    assert "deliberative, links to a Chamber" in body
    # compute + diarizer help present
    assert "Modal cloud GPU" in body
    assert "pyannote.ai Precision-2" in body
    # live preview + derived-id scaffolding present, wired via new_meeting.js
    assert 'id="preview"' in body
    assert 'id="derived-id"' in body
    assert "new_meeting.js" in body

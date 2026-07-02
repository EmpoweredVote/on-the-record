from __future__ import annotations

from gui.models import MeetingSummary, stage_label


def test_stage_label_maps_each_stage_to_friendly_text():
    assert stage_label(0) == "Not started"
    assert stage_label(1) == "Audio ingested"
    assert stage_label(2) == "Speakers separated"
    assert stage_label(3) == "Transcribed"
    assert stage_label(4) == "Identified — ready to review"
    assert stage_label(5) == "Summarized"
    assert stage_label(6) == "Voices enrolled"
    assert stage_label(7) == "Published"


def test_stage_label_tolerates_unknown_stage():
    assert stage_label(99) == "Unknown (99)"


def test_meeting_summary_display_name_prefers_title():
    s = MeetingSummary(
        meeting_id="2026-02-04-regular-session",
        title="Budget Hearing",
        city="Bloomington",
        meeting_type="Regular Session",
        date="2026-02-04",
        event_kind="council",
        completed_stage=4,
    )
    assert s.display_name == "Budget Hearing"
    assert s.stage_label == "Identified — ready to review"


def test_meeting_summary_display_name_falls_back_to_city_and_type():
    s = MeetingSummary(
        meeting_id="2026-02-04-regular-session",
        title=None,
        city="Bloomington",
        meeting_type="Regular Session",
        date="2026-02-04",
        event_kind="council",
        completed_stage=2,
    )
    assert s.display_name == "Bloomington Regular Session"


import json

from gui.library import scan_meetings


def test_scan_meetings_reads_state_and_sorts_by_date_desc(tagged_meeting_dir, tmp_meetings_dir):
    # tagged_meeting_dir writes pipeline_state.json with completed_stage + body_slug.
    older = tagged_meeting_dir(
        "bloomington-common-council",
        meeting_id="2026-01-10-regular-session",
        completed_stage=4,
    )
    newer = tagged_meeting_dir(
        "bloomington-common-council",
        meeting_id="2026-03-02-special-session",
        completed_stage=2,
    )
    # Enrich one state file with the newer metadata keys the GUI displays.
    state_path = older / "pipeline_state.json"
    data = json.loads(state_path.read_text())
    data.update({"city": "Bloomington", "meeting_type": "Regular Session",
                 "date": "2026-01-10", "event_kind": "council"})
    state_path.write_text(json.dumps(data))

    summaries = scan_meetings(tmp_meetings_dir)

    assert [s.meeting_id for s in summaries] == [
        "2026-03-02-special-session",  # newer date first
        "2026-01-10-regular-session",
    ]
    older_summary = summaries[1]
    assert older_summary.city == "Bloomington"
    assert older_summary.completed_stage == 4
    assert older_summary.stage_label == "Identified — ready to review"


def test_scan_meetings_reads_title_from_named_transcript(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    # transcript_named.json holds the Meeting dict; title lives there, not in state.
    (mdir / "transcript_named.json").write_text(json.dumps({"title": "Budget Hearing"}))

    summaries = scan_meetings(tmp_meetings_dir)

    assert summaries[0].title == "Budget Hearing"
    assert summaries[0].display_name == "Budget Hearing"


def test_scan_meetings_missing_dir_returns_empty(tmp_path):
    assert scan_meetings(tmp_path / "does-not-exist") == []


def test_scan_meetings_skips_dirs_without_state(tmp_meetings_dir):
    (tmp_meetings_dir / "stray-dir").mkdir()
    assert scan_meetings(tmp_meetings_dir) == []


def test_scan_meetings_skips_dir_with_invalid_json(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    bad = tmp_meetings_dir / "2026-05-01-broken-session"
    bad.mkdir()
    (bad / "pipeline_state.json").write_text("{ not json")

    summaries = scan_meetings(tmp_meetings_dir)

    # Bad dir skipped, no exception; only the valid meeting is returned.
    assert [s.meeting_id for s in summaries] == ["2026-02-04-regular-session"]


def test_scan_meetings_skips_dir_with_out_of_range_stage(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    # completed_stage 99 is out of range for PipelineStage and raises in _load().
    tagged_meeting_dir("x", meeting_id="2026-05-01-broken-session", completed_stage=99)

    summaries = scan_meetings(tmp_meetings_dir)

    assert [s.meeting_id for s in summaries] == ["2026-02-04-regular-session"]


from fastapi.testclient import TestClient

from gui.app import create_app


def test_library_route_renders_meetings(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    client = TestClient(create_app())

    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.text
    assert "2026-02-04-regular-session" in body
    assert "Identified — ready to review" in body


def test_library_route_empty_state(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No meetings processed yet" in resp.text


def test_library_route_survives_invalid_json_dir(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    bad = tmp_meetings_dir / "2026-05-01-broken-session"
    bad.mkdir()
    (bad / "pipeline_state.json").write_text("{ not json")
    client = TestClient(create_app())

    resp = client.get("/")

    assert resp.status_code == 200
    assert "2026-02-04-regular-session" in resp.text
    assert "2026-05-01-broken-session" not in resp.text


def test_library_route_survives_out_of_range_stage_dir(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    tagged_meeting_dir("x", meeting_id="2026-05-01-broken-session", completed_stage=99)
    client = TestClient(create_app())

    resp = client.get("/")

    assert resp.status_code == 200
    assert "2026-02-04-regular-session" in resp.text
    assert "2026-05-01-broken-session" not in resp.text


def test_main_module_exposes_app_factory():
    import gui.__main__ as entry
    assert hasattr(entry, "main")
    # create_app is importable and returns a FastAPI instance
    from gui.app import create_app
    from fastapi import FastAPI
    assert isinstance(create_app(), FastAPI)

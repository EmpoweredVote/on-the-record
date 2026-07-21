from __future__ import annotations

from gui.workspace import default_tab_for_stage


def test_default_tab_progress_until_identified():
    assert default_tab_for_stage(0) == "progress"
    assert default_tab_for_stage(3) == "progress"


def test_default_tab_review_once_identified():
    # Stage 4 == "Identified — ready to review" (gui.models._STAGE_LABELS).
    assert default_tab_for_stage(4) == "review"
    assert default_tab_for_stage(7) == "review"


from gui.workspace import panel_context


def test_panel_context_none_for_unknown_panel_or_meeting(tmp_meetings_dir):
    assert panel_context("bogus", "x") is None
    assert panel_context("review", "../escape") is None      # unsafe id
    assert panel_context("review", "ghost") is None          # no such meeting


def test_panel_context_progress_needs_only_the_dir(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=1)
    ctx = panel_context("progress", "2026-02-04-council")
    assert ctx["active_tab"] == "progress"
    assert ctx["meeting_id"] == "2026-02-04-council"
    assert ("diarize" in ctx["redo_stages"]) and ctx["stages"]  # stepper + redo data present


def test_panel_context_review_not_ready_before_identify(tagged_meeting_dir, tmp_meetings_dir):
    # completed_stage 2, no transcript_named.json -> placeholder, not None.
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=2)
    ctx = panel_context("review", "2026-02-04-council")
    assert ctx is not None
    assert ctx["page"] is None
    # Message is non-empty and mentions the current stage label (not hard-coded wording).
    from gui.models import stage_label
    assert ctx.get("not_ready") and stage_label(2) in ctx["not_ready"]


def test_panel_context_review_ready_returns_page(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    ctx = panel_context("review", "2026-02-04-council")
    assert ctx["page"] is not None
    assert ctx["page"].meeting_id == "2026-02-04-council"


def test_panel_context_details_and_publish(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.publish_api as pub
    monkeypatch.setattr(pub, "meeting_published_id", lambda mid: None)
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)

    d = panel_context("details", "2026-02-04-council")
    assert d["m"].city == "Bloomington" and "council" in d["event_kinds"]

    p = panel_context("publish", "2026-02-04-council")
    assert "review_status" in p and p["already_published"] is False


from gui.workspace import header_context


def test_header_context_none_for_unknown(tmp_meetings_dir):
    assert header_context("ghost") is None


def test_header_context_prestage4_no_attention(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=2)
    h = header_context("2026-02-04-council")
    assert h["completed_stage"] == 2
    assert h["attention_count"] == 0          # no speakers before Identify
    assert h["display_name"]                  # falls back to city/type/id
    assert h["is_live"] is None               # unknown unless caller passes it


def test_header_context_counts_attention_when_ready(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)  # SPEAKER_01 is unnamed -> needs attention
    h = header_context("2026-02-04-council", is_live=True)
    assert h["attention_count"] == 1
    assert h["is_live"] is True
    assert h["gate_badge"][0] in ("pass", "review", "failed", "none")


# --- Regression tests: malformed on-disk state must degrade, never raise. ---


def test_panel_context_review_malformed_transcript_is_not_ready(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    (mdir / "transcript_named.json").write_text("[]", encoding="utf-8")
    ctx = panel_context("review", "2026-02-04-council")
    assert ctx is not None
    assert ctx["page"] is None
    assert ctx.get("not_ready")


def test_panel_context_publish_malformed_transcript_is_not_ready(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    (mdir / "transcript_named.json").write_text("not json", encoding="utf-8")
    ctx = panel_context("publish", "2026-02-04-council")
    assert ctx is not None
    assert ctx.get("not_ready")


def test_panel_context_publish_malformed_pipeline_state_is_not_ready(tagged_meeting_dir, tmp_meetings_dir):
    # Valid transcript (so _load_meeting_ctx succeeds and we reach `PipelineState(ctx[1])`),
    # but a corrupt pipeline_state.json -- this is what actually exercises the new guard.
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    (mdir / "pipeline_state.json").write_text("{ not json", encoding="utf-8")
    ctx = panel_context("publish", "2026-02-04-council")
    assert ctx is not None
    assert ctx.get("not_ready")


def test_header_context_malformed_pipeline_state_returns_none(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=2)
    (mdir / "pipeline_state.json").write_text("{ not json", encoding="utf-8")
    assert header_context("2026-02-04-council") is None


def test_header_context_non_dict_transcript_does_not_crash(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    (mdir / "transcript_named.json").write_text("[]", encoding="utf-8")
    h = header_context("2026-02-04-council")
    assert h is not None
    assert h["display_name"]  # falls back to city/type/id, not the (absent) title


from fastapi.testclient import TestClient
from gui.app import create_app


def test_workspace_shell_renders_active_panel(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    r = client.get("/meetings/2026-02-04-council")   # no ?tab -> default (review @ stage 4)
    assert r.status_code == 200
    assert 'class="tabstrip"' in r.text
    assert "Needs attention" in r.text               # review panel rendered inline
    assert "workspace.js" in r.text


def test_workspace_shell_respects_tab_param(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council?tab=publish").text
    assert 'action="/meetings/2026-02-04-council/publish"' in body   # publish panel


def test_workspace_shell_404_unknown(tmp_meetings_dir):
    assert TestClient(create_app()).get("/meetings/ghost").status_code == 404


def test_panel_fragment_route(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    r = client.get("/meetings/2026-02-04-council/panel/review")
    assert r.status_code == 200
    assert "Needs attention" in r.text
    assert "<html" not in r.text.lower()             # fragment only, no shell
    assert client.get("/meetings/2026-02-04-council/panel/bogus").status_code == 404
    assert client.get("/meetings/ghost/panel/review").status_code == 404


def test_status_endpoint_augments_run_status(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.publish_api as pub
    monkeypatch.setattr(pub, "meeting_published_id", lambda mid: None)
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    st = TestClient(create_app()).get("/meetings/2026-02-04-council/status").json()
    assert st["completed_stage"] == 4
    assert st["attention_count"] == 1
    assert "review_status" in st and "is_live" in st


def test_workspace_shell_bad_tab_falls_back(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    r = TestClient(create_app()).get("/meetings/2026-02-04-council?tab=bogus")
    assert r.status_code == 200
    assert 'data-active-tab="review"' in r.text   # fell back to the stage-4 default


import pytest


@pytest.mark.parametrize("old,tab", [
    ("run", "progress"), ("review", "review"), ("edit", "details"), ("publish", "publish"),
])
def test_old_page_urls_redirect_to_workspace(tagged_meeting_dir, tmp_meetings_dir, old, tab):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    r = client.get(f"/meetings/2026-02-04-council/{old}", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"/meetings/2026-02-04-council?tab={tab}"


def test_workspace_js_wires_core_endpoints(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    # tab swap + panel fetch
    assert "/panel/" in js
    # live status poll
    assert "/status" in js
    # absorbed review.js behaviors
    assert "/api/politicians/search" in js
    assert "data-hls" in js
    # form interception opt-out
    assert "data-navigate" in js

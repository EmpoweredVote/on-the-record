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
    assert "not_ready" in ctx and "Identify" in ctx["not_ready"]


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

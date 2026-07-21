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

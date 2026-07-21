"""Assemble the context for the meeting workspace shell and its panels.

Pure-ish data assembly — no HTTP. Reuses the existing loaders (load_review_page,
_load_meeting_ctx, PipelineState) so the workspace and the pipeline agree on
how a meeting is read. The single source of truth for panel data, called by
both the GET /panel/{name} route and the shell route in gui.app."""
from __future__ import annotations

from typing import Optional

from src import config

from gui.models import stage_label
from gui.paths import is_safe_meeting_id


# Speakers are assigned during stage 4 ("Identified"); before that, Review is empty.
_REVIEW_READY_STAGE = 4


def default_tab_for_stage(completed_stage: int) -> str:
    """The tab a meeting opens on: Progress while still processing, Review once
    speakers have been identified (stage >= 4)."""
    return "review" if completed_stage >= _REVIEW_READY_STAGE else "progress"


_PANELS = ("progress", "review", "details", "publish")


def _meeting_dir(meeting_id: str):
    """The meeting dir if the id is safe and the meeting exists (has
    pipeline_state.json), else None."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    return meeting_dir


def _not_ready_message(meeting_dir) -> str:
    """Placeholder text for a panel whose data isn't available yet — either
    genuinely pre-stage-4, or the meeting's files are malformed."""
    from src.checkpoint import PipelineState
    try:
        stage = int(PipelineState(meeting_dir).completed_stage)
    except Exception:
        stage = 0
    return f"This step isn't available yet. Currently: {stage_label(stage)}."


def panel_context(name: str, meeting_id: str) -> Optional[dict]:
    """Jinja context for one workspace panel, or None if the panel name is
    unknown / the id is unsafe / the meeting doesn't exist. Panels that need the
    processed meeting return a 'not_ready' message before stage 4 instead of None."""
    if name not in _PANELS:
        return None
    meeting_dir = _meeting_dir(meeting_id)
    if meeting_dir is None:
        return None

    base = {"meeting_id": meeting_id, "active_tab": name}

    if name == "progress":
        from src.checkpoint import PipelineStage
        from gui import runner
        base["stages"] = [(s.value, stage_label(s.value))
                          for s in PipelineStage if s.value >= 1]
        base["redo_stages"] = list(runner.REDO_STAGES)
        return base

    # review / details / publish need the processed meeting (transcript_named.json).
    if not (meeting_dir / "transcript_named.json").exists():
        base["not_ready"] = _not_ready_message(meeting_dir)
        base["page"] = None  # panels/review.html reads page; None + not_ready -> placeholder
        return base

    if name == "review":
        from gui.review_api import load_review_page
        page = load_review_page(meeting_id)
        if page is None:
            base["not_ready"] = _not_ready_message(meeting_dir)
        base["page"] = page
        return base

    if name == "details":
        from gui.review_api import _load_meeting_ctx
        from src.event_kinds import EVENT_KINDS
        ctx = _load_meeting_ctx(meeting_id)
        if ctx is None:            # malformed transcript_named -> treat as not ready
            base["not_ready"] = _not_ready_message(meeting_dir)
            return base
        base["m"] = ctx[0]
        base["event_kinds"] = list(EVENT_KINDS)
        return base

    # publish
    from gui.review_api import _load_meeting_ctx
    from gui import publish_api
    from src.checkpoint import PipelineState
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        base["not_ready"] = _not_ready_message(meeting_dir)
        return base
    try:
        state = PipelineState(ctx[1])
    except Exception:
        base["not_ready"] = _not_ready_message(meeting_dir)
        return base
    base["review_status"] = state.review_status
    base["gate_pass"] = state.review_status == "pass"
    base["already_published"] = publish_api.meeting_published_id(meeting_id) is not None
    return base


def header_context(meeting_id: str, *, is_live: Optional[bool] = None) -> Optional[dict]:
    """Context for the persistent workspace header. None if the meeting is
    unknown. attention_count is 0 before stage 4 (no speakers yet)."""
    import json
    from src.checkpoint import PipelineState
    from gui.models import gate_badge

    meeting_dir = _meeting_dir(meeting_id)
    if meeting_dir is None:
        return None
    try:
        state = PipelineState(meeting_dir)
    except Exception:
        return None
    completed = int(state.completed_stage)

    # Title: prefer transcript_named.json title, else city + meeting_type, else id.
    title = None
    named = meeting_dir / "transcript_named.json"
    if named.exists():
        try:
            t = json.loads(named.read_text(encoding="utf-8")).get("title")
            title = t if isinstance(t, str) and t.strip() else None
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            title = None
    display_name = title or " ".join(
        p for p in (state.city, state.meeting_type) if p and p.strip()
    ) or meeting_id

    attention_count = 0
    if completed >= _REVIEW_READY_STAGE:
        from gui.review_api import load_review_page
        page = load_review_page(meeting_id)
        if page is not None:
            attention_count = len(page.needs_attention)

    return {
        "meeting_id": meeting_id,
        "display_name": display_name,
        "date": state.date,
        "event_kind": state.event_kind,
        "review_status": state.review_status,
        "completed_stage": completed,
        "gate_badge": gate_badge(state.review_status, state.trusted_coverage),
        "is_live": is_live,
        "attention_count": attention_count,
    }

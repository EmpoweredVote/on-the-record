"""Assemble the context for the meeting workspace shell and its panels.

Pure-ish data assembly — no HTTP. Reuses the existing loaders (load_review_page,
_load_meeting_ctx, run_status, PipelineState) so the workspace and the pipeline
agree on how a meeting is read. The single source of truth for panel data,
called by both the GET /panel/{name} route and the shell route in gui.app."""
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

from __future__ import annotations

from gui.workspace import default_tab_for_stage


def test_default_tab_progress_until_identified():
    assert default_tab_for_stage(0) == "progress"
    assert default_tab_for_stage(3) == "progress"


def test_default_tab_review_once_identified():
    # Stage 4 == "Identified — ready to review" (gui.models._STAGE_LABELS).
    assert default_tab_for_stage(4) == "review"
    assert default_tab_for_stage(7) == "review"

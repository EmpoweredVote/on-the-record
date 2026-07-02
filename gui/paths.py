"""Path-safety helpers for the GUI. Mirrors run_local._is_simple_meeting_id
without importing the heavy CLI module."""
from __future__ import annotations

from pathlib import Path


def is_safe_meeting_id(meeting_id: str) -> bool:
    """True iff meeting_id is a single, non-traversing path component."""
    return (
        bool(meeting_id)
        and meeting_id not in {".", ".."}
        and not Path(meeting_id).is_absolute()
        and Path(meeting_id).name == meeting_id
    )

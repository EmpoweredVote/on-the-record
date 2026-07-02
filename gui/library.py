"""Read the meetings directory into a sorted list of MeetingSummary rows.

Pure filesystem reads — no HTTP. Reuses src.checkpoint.PipelineState so the
GUI and the pipeline agree on how pipeline_state.json is parsed (and tolerate
older state files missing the newer metadata keys)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.checkpoint import PipelineState

from gui.models import MeetingSummary


def _title_from_named_transcript(meeting_dir: Path) -> Optional[str]:
    """Title is stored on the Meeting (transcript_named.json), not in state."""
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        data = json.loads(named.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    title = data.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _summarize(meeting_dir: Path) -> Optional[MeetingSummary]:
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    state = PipelineState(meeting_dir)
    return MeetingSummary(
        meeting_id=meeting_dir.name,
        title=_title_from_named_transcript(meeting_dir),
        city=state.city,
        meeting_type=state.meeting_type,
        date=state.date,
        event_kind=state.event_kind,
        completed_stage=int(state.completed_stage),
    )


def scan_meetings(meetings_dir: Path) -> list[MeetingSummary]:
    """All meetings under meetings_dir, newest date first (missing dates last)."""
    if not meetings_dir.exists():
        return []
    summaries: list[MeetingSummary] = []
    for child in sorted(meetings_dir.iterdir()):
        if not child.is_dir():
            continue
        summary = _summarize(child)
        if summary is not None:
            summaries.append(summary)
    # Sort newest first. Meeting IDs are date-prefixed (e.g.
    # "2026-03-02-special-session"), so when a state file lacks an explicit
    # `date` we fall back to the meeting_id — which keeps ordering sensible and
    # deterministic instead of dumping undated meetings last.
    summaries.sort(key=lambda s: (s.date or s.meeting_id, s.meeting_id), reverse=True)
    return summaries

"""GUI-facing view models. No HTTP, no I/O — pure data + display helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Friendly labels for src.checkpoint.PipelineStage values (0..7). Kept here (not
# imported from checkpoint) so the label wording is a GUI concern the pipeline
# can't accidentally change.
_STAGE_LABELS = {
    0: "Not started",
    1: "Audio ingested",
    2: "Speakers separated",
    3: "Transcribed",
    4: "Identified — ready to review",
    5: "Summarized",
    6: "Voices enrolled",
    7: "Published",
}


def stage_label(completed_stage: int) -> str:
    """Human label for a PipelineStage integer value."""
    return _STAGE_LABELS.get(completed_stage, f"Unknown ({completed_stage})")


@dataclass
class MeetingSummary:
    """One row in the meeting library. Built from pipeline_state.json (+ title
    from transcript_named.json when present)."""

    meeting_id: str
    title: Optional[str]
    city: Optional[str]
    meeting_type: Optional[str]
    date: Optional[str]
    event_kind: Optional[str]
    completed_stage: int

    @property
    def stage_label(self) -> str:
        return stage_label(self.completed_stage)

    @property
    def display_name(self) -> str:
        """Title if set, else 'City MeetingType', else the meeting_id."""
        title = (self.title or "").strip()
        if title:
            return title
        parts = [p for p in (self.city, self.meeting_type) if p and p.strip()]
        return " ".join(parts) if parts else self.meeting_id

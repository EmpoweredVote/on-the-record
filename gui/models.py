"""GUI-facing view models. No HTTP, no I/O — pure data + display helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
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


def gate_badge(review_status: Optional[str], trusted_coverage: Optional[float]) -> tuple[str, str]:
    """(level, text) for the confidence-gate badge. level is a CSS class token."""
    if review_status == "pass":
        if trusted_coverage is not None:
            return "pass", f"{round(trusted_coverage * 100)}% trusted"
        return "pass", "passed"
    if review_status == "review":
        return "review", "needs review"
    if review_status == "failed":
        return "failed", "failed"
    return "none", "—"


def duration_label(seconds: Optional[float]) -> str:
    """'2h 52m' / '47m' / '—' (— for None or non-positive)."""
    if not seconds or seconds <= 0:
        return "—"
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


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
    # Slice 1b: enrichment fields; all optional so older/partial meetings still build.
    speaker_count: Optional[int] = None
    duration_seconds: Optional[float] = None
    review_status: Optional[str] = None
    trusted_coverage: Optional[float] = None
    has_thumbnail: bool = False

    @property
    def stage_label(self) -> str:
        return stage_label(self.completed_stage)

    @property
    def speakers_label(self) -> str:
        return str(self.speaker_count) if self.speaker_count is not None else "—"

    @property
    def duration_label(self) -> str:
        return duration_label(self.duration_seconds)

    @property
    def gate_badge(self) -> tuple[str, str]:
        return gate_badge(self.review_status, self.trusted_coverage)

    @property
    def display_name(self) -> str:
        """Title if set, else 'City MeetingType', else the meeting_id."""
        title = (self.title or "").strip()
        if title:
            return title
        parts = [p for p in (self.city, self.meeting_type) if p and p.strip()]
        return " ".join(parts) if parts else self.meeting_id


# Confidence at/above which an identified speaker is auto-accepted (green) and
# not surfaced for attention. Mirrors the pipeline's gate threshold.
CONFIDENT_THRESHOLD = 0.85

_UNIDENTIFIED = "(unidentified)"


@dataclass
class SpeakerCard:
    """One speaker in the review page."""

    label: str
    name: Optional[str]
    confidence: float
    method: Optional[str]
    minutes: float
    seg_count: int
    sample_text: Optional[str] = None
    hints: list[tuple[str, float]] = field(default_factory=list)
    clip_seeks: list[float] = field(default_factory=list)
    politician_slug: Optional[str] = None
    politician_id: Optional[str] = None
    speaker_status: Optional[str] = None  # None | "unidentified" | "non_speaker"

    @property
    def display_name(self) -> str:
        return self.name if self.name and self.name.strip() else _UNIDENTIFIED

    @property
    def is_linked(self) -> bool:
        return bool(self.politician_slug)

    @property
    def is_confirmed(self) -> bool:
        return (
            bool(self.name)
            and self.name.strip() not in ("", _UNIDENTIFIED)
            and self.confidence >= CONFIDENT_THRESHOLD
        )

    @property
    def accept_name(self) -> Optional[str]:
        """Best one-click name to accept: the current name, else the top voice hint."""
        if self.name and self.name.strip() not in ("", _UNIDENTIFIED):
            return self.name.strip()
        if self.hints:
            return self.hints[0][0]
        return None


@dataclass
class ReviewPageData:
    meeting_id: str
    display_name: str
    media_kind: Optional[str]  # "video" | "audio" | None
    needs_attention: list[SpeakerCard] = field(default_factory=list)
    confirmed: list[SpeakerCard] = field(default_factory=list)

    @property
    def speaker_count(self) -> int:
        return len(self.needs_attention) + len(self.confirmed)

    @property
    def all_cards(self) -> list["SpeakerCard"]:
        return self.needs_attention + self.confirmed

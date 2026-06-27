"""Bulk review of unlinked named speakers across all meetings.

Pure logic + YAML (de)serialization backing `run_local.py --bulk-relink-scan`
and `--bulk-relink-apply`. No file or network I/O except the essentials name
search injected into `suggest_link`; the orchestrators in run_local.py do the
directory walk, file writes, profile DB, publish, and deploy. Mirrors how
`src/relink.py` keeps logic separate from the run_local orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DECISION_LINK = "link"
DECISION_REVIEW = "review"
DECISION_SKIP = "skip"
VALID_DECISIONS = (DECISION_LINK, DECISION_REVIEW, DECISION_SKIP)


@dataclass
class UnlinkedSpeaker:
    display_name: str
    normalized_name: str
    appearances: list[tuple[str, str]] = field(default_factory=list)  # (meeting_id, label)
    meeting_count: int = 0
    has_voice_profile: bool = False
    known_id: Optional[str] = None
    decision: str = DECISION_REVIEW
    candidates: list[dict] = field(default_factory=list)

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


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def enumerate_unlinked(meetings, profile_db) -> list[UnlinkedSpeaker]:
    """Group unlinked named speaker mappings across meetings, by normalized name.

    Includes a mapping when speaker_name is set, politician_id is None,
    speaker_status is normal (not 'unidentified'/'non_speaker'), and local_slug
    is None. Captures known_id = the politician_id the same name is already
    linked to elsewhere (None if zero or several distinct ids). Pure; the caller
    supplies loaded Meeting objects and the profile DB.
    """
    from src.enroll import _name_to_slug

    # First pass: ids each name is already linked to (from linked mappings).
    linked_ids: dict[str, set[str]] = {}
    for meeting in meetings:
        for mapping in meeting.speakers.values():
            if mapping.politician_id and mapping.speaker_name:
                linked_ids.setdefault(_normalize(mapping.speaker_name), set()).add(
                    mapping.politician_id
                )

    rows: dict[str, UnlinkedSpeaker] = {}
    for meeting in meetings:
        for mapping in meeting.speakers.values():
            if not mapping.speaker_name:
                continue
            if mapping.politician_id is not None:
                continue
            if mapping.speaker_status in ("unidentified", "non_speaker"):
                continue
            if mapping.local_slug is not None:
                continue
            key = _normalize(mapping.speaker_name)
            row = rows.get(key)
            if row is None:
                ids = linked_ids.get(key, set())
                row = UnlinkedSpeaker(
                    display_name=mapping.speaker_name.strip(),
                    normalized_name=key,
                    has_voice_profile=_name_to_slug(mapping.speaker_name) in profile_db.profiles,
                    known_id=next(iter(ids)) if len(ids) == 1 else None,
                )
                rows[key] = row
            row.appearances.append((meeting.meeting_id, mapping.speaker_label))
            row.meeting_count = len({m for m, _ in row.appearances})
    return list(rows.values())

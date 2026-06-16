from __future__ import annotations

from typing import Optional
from uuid import UUID


def _validate_uuid(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


_INTERVIEW_KINDS = {"news_clip", "press_conference"}


def validate_event_entities(
    event_kind: str,
    chamber_id: Optional[str],
    race_id: Optional[str],
) -> Optional[str]:
    chamber_id = _validate_uuid("chamber_id", chamber_id)
    race_id = _validate_uuid("race_id", race_id)

    if chamber_id is not None and race_id is not None and event_kind not in _INTERVIEW_KINDS:
        return "chamber_id and race_id cannot both be set"
    if event_kind in ("council", "school_board") and chamber_id is None:
        return f"chamber_id is required for event_kind {event_kind}"
    if event_kind in ("debate", "forum") and race_id is None:
        return f"race_id is required for event_kind {event_kind}"
    return None

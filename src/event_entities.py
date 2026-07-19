from __future__ import annotations

from typing import Optional
from uuid import UUID

from .event_kinds import INTERVIEW_KINDS as _INTERVIEW_KINDS


def _validate_uuid(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def validate_event_entities(
    event_kind: str,
    chamber_id: Optional[str],
    race_id: Optional[str],
) -> Optional[str]:
    chamber_id = _validate_uuid("chamber_id", chamber_id)
    race_id = _validate_uuid("race_id", race_id)

    if chamber_id is not None and race_id is not None and event_kind not in _INTERVIEW_KINDS:
        return "chamber_id and race_id cannot both be set"
    # chamber_id is optional, not required, for council/school_board: a multi-seat
    # body (e.g. Bloomington Common Council = 7 per-seat chambers sharing one slug)
    # has no single chamber to pin — _resolve_chamber_id returns None for it by
    # design — so a missing chamber must not block publishing. It is still set
    # when a body resolves to exactly one chamber.
    return None

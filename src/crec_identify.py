# src/crec_identify.py
"""Phase 4: bridge CREC alignment to Stage-4 SpeakerMappings + CLI orchestration.

Converts Phase-3 LabelResolutions into SpeakerMappings and orchestrates Phases
1-3 (fetch -> roster -> annotate -> align -> convert) for a floor session. The
essentials politician_id link is intentionally deferred: a resolved member gets
its name + bioguide (stashed in local_slug), not a politician_id.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import SpeakerMapping
from .crec_align import LabelResolution, align_crec_to_diarization

_ROLE_DISPLAY = {
    "presiding_officer": "The Presiding Officer",
    "speaker": "The Speaker",
    "president_pro_tempore": "The President pro tempore",
    "vice_president": "The Vice President",
    "chief_justice": "The Chief Justice",
    "chair": "The Chair",
    "clerk": "The Clerk",
}


def label_resolution_to_mapping(res: LabelResolution) -> Optional[SpeakerMapping]:
    """Convert a LabelResolution to a SpeakerMapping (or None if unresolved).

    Confident member -> name + `congress-<bioguide>` in local_slug (no politician_id).
    Role -> a human role display name. Ambiguous -> needs_review/unidentified.
    """
    if res.member is not None:
        return SpeakerMapping(
            speaker_label=res.speaker_label,
            speaker_name=res.member.full_name,
            confidence=res.confidence,
            id_method="congressional_record",
            needs_review=False,
            local_slug=f"congress-{res.member.bioguide}",
        )
    if res.role is not None:
        display = _ROLE_DISPLAY.get(res.role, res.role.replace("_", " ").title())
        return SpeakerMapping(
            speaker_label=res.speaker_label,
            speaker_name=display,
            confidence=res.confidence,
            id_method="congressional_record",
            needs_review=False,
        )
    if res.method == "ambiguous":
        return SpeakerMapping(
            speaker_label=res.speaker_label,
            needs_review=True,
            speaker_status="unidentified",
        )
    return None


def parse_crec_arg(value) -> Optional[tuple[str, str]]:
    """Validate a `--congressional-record DATE CHAMBER` arg.

    Returns (date, lowercased_chamber) or None when the flag is absent. Raises
    SystemExit with a clear message on a bad date or chamber.
    """
    if not value:
        return None
    date, chamber = value
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise SystemExit(
            f"--congressional-record DATE must be YYYY-MM-DD, got {date!r}")
    ch = chamber.lower()
    if ch not in ("house", "senate"):
        raise SystemExit(
            f"--congressional-record CHAMBER must be 'house' or 'senate', got {chamber!r}")
    return (date, ch)

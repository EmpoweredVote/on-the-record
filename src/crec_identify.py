# src/crec_identify.py
"""Phase 4: bridge CREC alignment to Stage-4 SpeakerMappings + CLI orchestration.

Converts Phase-3 LabelResolutions into SpeakerMappings and orchestrates Phases
1-3 (fetch -> roster -> annotate -> align -> convert) for a floor session. A
resolved member always gets its name + bioguide (stashed in local_slug); when a
single unambiguous essentials match is found (see crec_essentials), it is also
enriched with a politician_id/politician_slug.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import SpeakerMapping
from .crec_align import LabelResolution, align_crec_to_diarization
from .govinfo import fetch_congressional_record_turns
from .congress_roster import load_current_roster
from .crec_normalize import annotate_turns
from .crec_essentials import resolve_politician_id

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


def crec_speaker_mappings(
    date: str,
    chamber: str,
    segments,
    *,
    fetch=None,
    min_confidence: float = 0.5,
    cache_path=None,
    search=None,
) -> dict:
    """Resolve diarized speaker labels via the Congressional Record for a session.

    Orchestrates Phases 1-3: fetch CREC turns -> load the current-Congress roster
    -> annotate turns with identities -> align onto the diarized segments ->
    convert to SpeakerMappings. `fetch` (injectable) and `cache_path` are threaded
    to the network/cache layers for testing. Confident member mappings are then
    bridged to an essentials politician_id via `resolve_politician_id` (`search`
    is injectable for testing; when omitted, the real essentials search is used).
    Returns {} when there is no Record.
    """
    fkw = {"fetch": fetch} if fetch is not None else {}
    turns = fetch_congressional_record_turns(date, chamber, **fkw)
    if not turns:
        return {}
    roster = load_current_roster(chamber, cache_path=cache_path, **fkw)
    annotated = annotate_turns(turns, roster)
    resolutions = align_crec_to_diarization(segments, annotated, min_confidence=min_confidence)

    mappings: dict = {}
    for label, res in resolutions.items():
        m = label_resolution_to_mapping(res)
        if m is None:
            continue
        if res.member is not None:
            link = resolve_politician_id(
                res.member,
                **({"search": search} if search is not None else {}),
            )
            if link:
                m.politician_id, m.politician_slug = link
        mappings[label] = m
    return mappings

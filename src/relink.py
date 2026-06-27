"""Non-interactive relink of a speaker to an essentials politician.

Pure logic backing `run_local.py --relink-person`: resolve a target politician,
set the link on matching speaker mappings, and fold the person's voice profile
onto the id key. No file or network I/O except the essentials name search in
resolve_link_target. The orchestrator in run_local.py does the file/DB I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.essentials_client import EssentialsClientError, search_politicians


@dataclass
class ResolvedTarget:
    politician_id: str
    politician_slug: Optional[str]
    full_name: str


class RelinkAmbiguous(Exception):
    """A name query resolved to zero or several politicians; caller must pick."""

    def __init__(self, query: str, candidates: list[dict]):
        self.query = query
        self.candidates = candidates
        super().__init__(
            f"'{query}' matched {len(candidates)} politicians; pass --to-id to disambiguate"
        )


def relink_in_meeting(meeting, speaker_name, politician_id, politician_slug) -> list[str]:
    """Set politician identity on every mapping whose name matches speaker_name.

    Returns the labels actually changed (case-insensitive name match; skips
    mappings already linked to the same id+slug). Mutates meeting.speakers in
    place via review.link_speaker. Segments carry no politician fields, so
    publish derives them from these mappings — mappings are the only source.
    """
    from src.review import link_speaker

    want = speaker_name.strip().lower()
    changed: list[str] = []
    for label, mapping in list(meeting.speakers.items()):
        name = (mapping.speaker_name or "").strip().lower()
        if name != want:
            continue
        if mapping.politician_id == politician_id and mapping.politician_slug == politician_slug:
            continue  # already linked — no change
        link_speaker(meeting.speakers, label, politician_slug, politician_id)
        changed.append(label)
    return changed


def resolve_link_target(
    query: str, *, explicit_id: Optional[str] = None, base_url: Optional[str] = None
) -> ResolvedTarget:
    """Resolve a name (and/or explicit id) to a single essentials politician.

    With explicit_id: use it; the search is display-only, so a lookup failure is
    tolerated (slug/name fall back to None/query). Without explicit_id: the search
    is load-bearing — exactly one match → that politician; zero or several →
    RelinkAmbiguous. An essentials API error (EssentialsClientError) propagates so
    an outage is not silently reported as "no matches found".
    """
    if explicit_id is not None:
        try:
            matches = search_politicians(query, base_url=base_url)
        except EssentialsClientError:
            matches = []
        for m in matches:
            if m.get("politician_id") == explicit_id:
                return ResolvedTarget(explicit_id, m.get("politician_slug"), m.get("full_name") or query)
        return ResolvedTarget(explicit_id, None, query)

    matches = search_politicians(query, base_url=base_url)
    if len(matches) == 1:
        m = matches[0]
        return ResolvedTarget(m["politician_id"], m.get("politician_slug"), m.get("full_name") or query)
    raise RelinkAmbiguous(query, matches)

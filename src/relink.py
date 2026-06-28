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


def _is_strong_name_match(name: str, full_name: str) -> bool:
    """True when every whitespace token of `name` is a whole-word token of
    `full_name` (case-insensitive). Rejects substring fuzz ("Host" vs
    "Hostettler") and title prefixes ("Councilmember Rollo" vs "David R Rollo")."""
    name_tokens = set((name or "").lower().split())
    cand_tokens = set((full_name or "").lower().split())
    return bool(name_tokens) and name_tokens.issubset(cand_tokens)


def confident_target(
    name, *, search=search_politicians, known_id: Optional[str] = None
) -> Optional[ResolvedTarget]:
    """Resolve a name to a politician ONLY when confident enough to auto-link.

    known_id set -> that target (already linked elsewhere; highest confidence;
    slug/name filled best-effort from a search hit). Otherwise: exactly one
    search match AND a strong name match -> that target; zero/multiple/weak ->
    None. EssentialsClientError -> None (best-effort; never blocks a run).
    """
    if known_id:
        slug, full = None, name
        try:
            for m in search(name):
                if m.get("politician_id") == known_id:
                    slug = m.get("politician_slug")
                    full = m.get("full_name") or name
                    break
        except EssentialsClientError:
            pass
        return ResolvedTarget(known_id, slug, full)

    try:
        matches = search(name)
    except EssentialsClientError:
        return None
    if len(matches) == 1 and _is_strong_name_match(name, matches[0].get("full_name") or ""):
        m = matches[0]
        return ResolvedTarget(m["politician_id"], m.get("politician_slug"), m.get("full_name") or name)
    return None


def rekey_profile_for_link(db, speaker_name, *, politician_id, politician_slug, full_name) -> Optional[str]:
    """Fold the person's existing voice profile(s) onto the essentials:<id> key.

    Collects source profiles most-authoritative first: every profile already
    carrying this politician_id (same id == same person — folded in full), then
    the name-slug enrollment key, then — only if nothing else matched — a single
    profile whose display_name equals speaker_name. Folds each via
    promote_unidentified_handle (carries embeddings/meetings, no audio). Returns
    the target key, or None when no source profile exists (the DB link still
    publishes regardless). Display-name matching is a best-effort last resort and
    folds at most one profile.
    """
    from src.enroll import _name_to_slug, promote_unidentified_handle

    target_key = f"essentials:{politician_id}"

    sources: list[str] = []
    # 1. Authoritative: any profile already carrying this politician_id.
    for k, p in db.profiles.items():
        if k != target_key and p.politician_id == politician_id:
            sources.append(k)
    # 2. The person's name-slug enrollment key.
    name_slug = _name_to_slug(speaker_name)
    if name_slug in db.profiles and name_slug != target_key and name_slug not in sources:
        sources.append(name_slug)
    # 3. Last resort: a single profile whose display_name matches the name.
    if not sources:
        want = speaker_name.strip().lower()
        for k, p in db.profiles.items():
            if k != target_key and (p.display_name or "").strip().lower() == want:
                sources.append(k)
                break

    if not sources:
        return target_key if target_key in db.profiles else None

    for handle_key in sources:
        promote_unidentified_handle(
            db, handle_key, target_key,
            display_name=full_name, politician_slug=politician_slug, politician_id=politician_id,
        )
    return target_key

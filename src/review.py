"""Pure speaker-review operations shared by the CLI and (later) the GUI.

No prompts, no printing, no file writes — these functions transform in-memory
data (segments, mappings, embeddings) so they are directly unit-testable and
reusable. Persistence and interaction live in the callers (run_local.py).
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


def make_unidentified_slug(meeting_id: str, label: str) -> str:
    """Unique, deterministic handle for an unidentified speaker.

    Keyed by (meeting, diarization label) so two different unknowns never share a
    slug (no merge), while re-running review on the same meeting is idempotent.
    """
    base = _re.sub(r"[^a-z0-9]+", "-", f"{meeting_id}-{label}".lower()).strip("-")
    base = base or _re.sub(r"[^a-z0-9]+", "-", (label or "speaker").lower()).strip("-") or "speaker"
    return f"unidentified-{base}"[:100]


def identity_label(mapping) -> str:
    """One-word resolved identity for the review table."""
    if mapping is None:
        return "unlinked"
    if mapping.speaker_status == "non_speaker":
        return "non-speaker"
    if mapping.speaker_status == "unidentified":
        return "unidentified"
    if mapping.politician_slug:
        return f"essentials:{mapping.politician_slug}"
    if mapping.local_slug:
        return f"local:{mapping.local_slug}"
    return "unlinked"


@dataclass
class SpeakerView:
    label: str
    current_name: Optional[str]
    current_confidence: float
    current_method: Optional[str]
    seg_count: int
    total_speech_seconds: float
    clip_start: Optional[float]
    clip_candidates: list[float] = field(default_factory=list)
    sample_text: Optional[str] = None
    soft_hints: list[tuple[str, float, str]] = field(default_factory=list)
    needs_review: bool = False


def build_review_state(segments, mappings, embeddings, profile_db, *, show_text: bool) -> list[SpeakerView]:
    """Build one SpeakerView per speaker label, sorted by speech time desc.

    soft_hints come from voice-profile soft matching when embeddings + profiles
    are available; otherwise empty.
    """
    by_label: dict[str, list] = {}
    for seg in segments:
        by_label.setdefault(seg.speaker_label, []).append(seg)

    hints: dict[str, list[tuple[str, float, str]]] = {}
    if embeddings and getattr(profile_db, "profiles", None):
        from src.enroll import get_stored_centroids
        from src.identify import soft_match_voice_profiles

        centroids = get_stored_centroids(profile_db)
        if centroids:
            display_names = {pid: p.display_name for pid, p in profile_db.profiles.items()}
            hints = soft_match_voice_profiles(embeddings, centroids, display_names)

    views: list[SpeakerView] = []
    for label, segs in by_label.items():
        total = sum(s.end_time - s.start_time for s in segs)
        # Candidates: this speaker's segments by duration desc (longest turn is
        # the most identifying), capped at 8. Turns much longer than the ~40s
        # playback window also contribute in-turn start points (every 60s while
        # at least 30s of the turn remains) so cycling clips can sample beyond
        # the opening of a long monologue. The default clip + sample come from
        # the longest turn.
        ordered = sorted(segs, key=lambda s: s.end_time - s.start_time, reverse=True)
        clip_candidates: list[float] = []
        for s in ordered:
            if len(clip_candidates) >= 8:
                break
            clip_candidates.append(s.start_time)
            offset = 60.0
            while len(clip_candidates) < 8 and (s.end_time - s.start_time) - offset >= 30.0:
                clip_candidates.append(s.start_time + offset)
                offset += 60.0
        longest = ordered[0] if ordered else None
        mapping = mappings.get(label)
        sample_text = None
        if show_text and longest is not None and getattr(longest, "text", None) and longest.text.strip():
            sample_text = longest.text
        views.append(SpeakerView(
            label=label,
            current_name=getattr(mapping, "speaker_name", None) if mapping else None,
            current_confidence=getattr(mapping, "confidence", 0.0) if mapping else 0.0,
            current_method=getattr(mapping, "id_method", None) if mapping else None,
            seg_count=len(segs),
            total_speech_seconds=total,
            clip_start=longest.start_time if longest is not None else None,
            clip_candidates=clip_candidates,
            sample_text=sample_text,
            soft_hints=hints.get(label, []),
            needs_review=getattr(mapping, "needs_review", False) if mapping else False,
        ))

    views.sort(key=lambda v: v.total_speech_seconds, reverse=True)
    return views


@dataclass
class RenameResult:
    label: str
    old_name: Optional[str]
    new_name: str
    alias_suggestion: Optional[str]


def rename_speaker(mappings, segments, label: str, new_name: str, *, roster=None) -> RenameResult:
    """Assign new_name to a speaker label across its mapping and segments.

    If roster is given, the name is normalized via correct_speaker_name. Returns
    a RenameResult; alias_suggestion is the prior (wrong) name, to offer as an
    alias, or None when there was no prior name or it equals the new name.
    """
    from src.models import SpeakerMapping

    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    old_name = mapping.speaker_name

    final_name = new_name
    if roster is not None:
        from src.roster import correct_speaker_name
        final_name = correct_speaker_name(new_name, roster)

    mapping.speaker_name = final_name
    mapping.confidence = 1.0
    mapping.id_method = "human_review"
    mapping.needs_review = False

    # A human-assigned name is authoritative. Any prior identity link belonged to
    # the OLD name (e.g. a voice-profile collision that was then corrected by
    # hand), so it must not survive a name change — otherwise this voice enrolls
    # under the wrong person, since resolve_mapping_enrollment keys on
    # politician_slug ahead of the name. Re-derive the link from the new name when
    # a roster is available; otherwise drop it. A no-op rename leaves the (already
    # correct, possibly manually-pasted) link untouched.
    if final_name != old_name:
        mapping.local_slug = None
        mapping.local_role = None
        if roster is not None:
            from src.enroll import resolve_enrollment_key
            _key, pol_slug, pol_id = resolve_enrollment_key(final_name, roster)
            mapping.politician_slug = pol_slug
            mapping.politician_id = pol_id
        else:
            mapping.politician_slug = None
            mapping.politician_id = None

    mappings[label] = mapping

    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = final_name

    alias = old_name if (old_name and old_name != final_name) else None
    return RenameResult(label=label, old_name=old_name, new_name=final_name, alias_suggestion=alias)


@dataclass
class MergeResult:
    source_label: str
    target_label: str
    moved_segments: int
    combined_name: Optional[str]


def merge_speakers(segments, embeddings, mappings, source_label: str, target_label: str) -> MergeResult:
    """Full merge: fold source_label into target_label.

    - Relabels every source segment to the target.
    - Combines centroids weighted by each label's pre-merge speech time and
      recomputes the target centroid (if both embeddings present). If only one
      side has an embedding, the surviving embedding is carried to the target.
    - Drops the source from embeddings and mappings.
    - If the target has no name but the source does, the target adopts it.
    - All segments now labeled the target carry the merged speaker's name.

    Raises ValueError if labels are equal or the source has no segments/mapping.
    """
    if source_label == target_label:
        raise ValueError("Cannot merge a speaker into itself.")
    if source_label not in mappings and not any(s.speaker_label == source_label for s in segments):
        raise ValueError(f"Unknown source speaker: {source_label}")

    speech: dict[str, float] = {}
    for s in segments:
        speech[s.speaker_label] = speech.get(s.speaker_label, 0.0) + (s.end_time - s.start_time)

    moved = 0
    for s in segments:
        if s.speaker_label == source_label:
            s.speaker_label = target_label
            moved += 1

    if source_label in embeddings and target_label in embeddings:
        w_src = speech.get(source_label, 0.0)
        w_tgt = speech.get(target_label, 0.0)
        total = w_src + w_tgt
        if total > 0:
            embeddings[target_label] = (
                w_tgt * np.asarray(embeddings[target_label]) + w_src * np.asarray(embeddings[source_label])
            ) / total
        else:
            embeddings[target_label] = np.mean(
                [np.asarray(embeddings[target_label]), np.asarray(embeddings[source_label])], axis=0
            )
    elif source_label in embeddings and target_label not in embeddings:
        # Only the source has an embedding — carry it over so the merged
        # speaker keeps usable voice data instead of losing it.
        embeddings[target_label] = np.asarray(embeddings[source_label])
    embeddings.pop(source_label, None)

    src_map = mappings.pop(source_label, None)
    tgt_map = mappings.get(target_label)
    if tgt_map is not None and not getattr(tgt_map, "speaker_name", None) and src_map is not None and getattr(src_map, "speaker_name", None):
        tgt_map.speaker_name = src_map.speaker_name
        tgt_map.confidence = max(getattr(tgt_map, "confidence", 0.0), getattr(src_map, "confidence", 0.0))
        tgt_map.id_method = src_map.id_method
        tgt_map.needs_review = False

    combined_name = getattr(tgt_map, "speaker_name", None) if tgt_map is not None else None

    # Keep segment names consistent with the merged speaker.
    for s in segments:
        if s.speaker_label == target_label:
            s.speaker_name = combined_name

    return MergeResult(source_label=source_label, target_label=target_label, moved_segments=moved, combined_name=combined_name)


def speakers_needing_review(mappings) -> list[str]:
    """Labels whose mapping is flagged needs_review."""
    return [label for label, m in mappings.items() if getattr(m, "needs_review", False)]


def link_speaker(mappings, label, politician_slug, politician_id):
    """Set (or clear, when both are None) the politician identity on a mapping.

    Mutates `mappings` in place; returns the updated SpeakerMapping. Creates a
    bare mapping if the label has none yet.
    """
    from src.models import SpeakerMapping

    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    mapping.politician_slug = politician_slug
    mapping.politician_id = politician_id
    mappings[label] = mapping
    return mapping


def mark_unidentified(mappings, segments, label, meeting_id, display_label=None):
    """Mark a speaker as a distinct-but-unnamed person: unique handle, enrolled."""
    from src.models import SpeakerMapping
    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    name = (display_label or "").strip() or "Unidentified Speaker"
    mapping.speaker_name = name
    mapping.local_slug = make_unidentified_slug(meeting_id, label)
    mapping.local_role = None
    mapping.politician_slug = None
    mapping.politician_id = None
    mapping.speaker_status = "unidentified"
    mapping.id_method = "human_review"
    mapping.confidence = 1.0
    mapping.needs_review = False
    mappings[label] = mapping
    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = name


def mark_non_speaker(mappings, segments, label, display_label=None):
    """Mark a label as not-a-person (music/pledge/station ID); never enrolled."""
    from src.models import SpeakerMapping
    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    name = (display_label or "").strip() or "Non-speaker"
    mapping.speaker_name = name
    mapping.speaker_status = "non_speaker"
    mapping.politician_slug = None
    mapping.politician_id = None
    mapping.local_slug = None
    mapping.local_role = None
    mapping.id_method = "human_review"
    mapping.confidence = 1.0
    mapping.needs_review = False
    mappings[label] = mapping
    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = name


def link_to_unidentified_handle(mappings, segments, label, handle_key, display_name):
    """Link a speaker to an EXISTING unidentified handle (a returning unknown).

    handle_key is the stored profile key, e.g. 'local:unidentified-<m>-<lbl>'.
    Reuses that handle's slug so the recurring speaker enrolls into the same
    profile. Confirm-only — never called without reviewer action.
    """
    from src.models import SpeakerMapping
    slug = handle_key[len("local:"):] if handle_key.startswith("local:") else handle_key
    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    mapping.speaker_name = display_name or "Unidentified Speaker"
    mapping.local_slug = slug
    mapping.local_role = None
    mapping.politician_slug = None
    mapping.politician_id = None
    mapping.speaker_status = "unidentified"
    mapping.id_method = "human_confirmed"
    mapping.confidence = 1.0
    mapping.needs_review = False
    mappings[label] = mapping
    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = mapping.speaker_name


def parse_link_selection(token, n_matches):
    """Parse the reviewer's link-prompt input.

    Returns (action, index): action in {'pick','skip','search','none','invalid'}.
    'pick' carries a 0-based index into the match list.
    """
    t = (token or "").strip().lower()
    if t in ("", "s", "skip"):
        return ("skip", None)
    if t in ("m", "search"):
        return ("search", None)
    if t in ("n", "none"):
        return ("none", None)
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < n_matches:
            return ("pick", idx)
        return ("invalid", None)
    return ("invalid", None)


def format_match_line(match, index):
    """One-line rendering of a search_politicians() result for the link menu.

    No affiliation detail — the pipeline never surfaces it (antipartisan
    rule, tests/test_antipartisan.py).
    """
    tag = "incumbent" if match.get("is_incumbent") else "candidate"
    detail = []
    if match.get("office_title"):
        loc = match.get("government_name") or match.get("district_label") or ""
        detail.append(f"{match['office_title']}{', ' + loc if loc else ''}")
    elif match.get("district_label"):
        detail.append(match["district_label"])
    suffix = f" · {' · '.join(detail)}" if detail else ""
    name = match.get("full_name") or "(unknown)"
    return f"  {index + 1}. {name}{suffix} [{tag}]"


def _ew_name_tokens(s):
    stop = {"councilmember", "council", "president", "vice", "mayor", "clerk",
            "the", "of", "common", "city", "member", "district", "association",
            "office", "at", "large"}
    return set(_re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()) - stop


def _ew_slug_tokens(slug):
    return set(_re.sub(r"[^a-z0-9]+", " ", (slug or "").lower()).split()) - {"h", "j", "s"}


def enrollment_warnings(mappings, roster=None) -> list[dict]:
    """Flag suspicious states before enrollment. Returns [{kind, label, detail}].
    kinds: name_slug_mismatch, duplicate_name, unlinked_roster_match."""
    warns: list[dict] = []
    # name/slug mismatch (linked slug shares no token with the name)
    for label, m in mappings.items():
        if m.politician_slug and m.speaker_name:
            nt, st = _ew_name_tokens(m.speaker_name), _ew_slug_tokens(m.politician_slug)
            if nt and st and not (nt & st):
                warns.append({"kind": "name_slug_mismatch", "label": label,
                              "detail": f"{m.speaker_name!r} linked to {m.politician_slug!r}"})
    # duplicate name across labels (excluding non-speakers)
    by_name: dict[str, list[str]] = {}
    for label, m in mappings.items():
        if m.speaker_name and m.speaker_status not in ("non_speaker", "unidentified"):
            by_name.setdefault(m.speaker_name.strip().lower(), []).append(label)
    for nm, labels in by_name.items():
        if len(labels) > 1:
            warns.append({"kind": "duplicate_name", "label": ",".join(sorted(labels)),
                          "detail": f"{len(labels)} labels named {nm!r} (merge?)"})
    # named but unlinked, yet matches a roster member
    if roster is not None:
        from src.roster import correct_speaker_name
        for label, m in mappings.items():
            if (m.speaker_name and not m.politician_slug and not m.local_slug
                    and m.speaker_status not in ("non_speaker", "unidentified")):
                corrected = correct_speaker_name(m.speaker_name, roster)
                if any(corrected == mem.name and mem.politician_slug for mem in roster.members):
                    warns.append({"kind": "unlinked_roster_match", "label": label,
                                  "detail": f"{m.speaker_name!r} matches a roster member but isn't linked"})
    return warns

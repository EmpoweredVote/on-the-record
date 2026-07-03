"""Load an already-processed meeting into a ReviewPageData, and (Slice 2b)
write review edits back to disk.

Mirrors run_local's --review loading: Meeting.from_dict(transcript_named.json),
embeddings.json, load_profiles(), then review.build_review_state(). Write-back
(persist_review / apply_rename) mirrors run_local's --review save + _apply_gate:
mutations go through src.review, then transcript_named.json is written
(authoritative) with best-effort re-export + gate recompute."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

from src import config
from src.models import Meeting

from gui.models import CONFIDENT_THRESHOLD, ENROLL_MIN_SPEECH_SECONDS, ReviewPageData, SpeakerCard
from gui.paths import is_safe_meeting_id

# Video container preference order (same set run_local.find_video_file checks).
_VIDEO_EXTS = (".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov")
_LEAD_IN = 3.0  # seconds of context before a clip, mirroring run_local._review_seek


def find_meeting_media(meeting_dir: Path) -> Optional[tuple[str, str]]:
    """(kind, filename) for the best playable media: video if present, else
    audio.wav, else None. kind is 'video' or 'audio'."""
    for ext in _VIDEO_EXTS:
        candidate = meeting_dir / f"source{ext}"
        if candidate.exists():
            return "video", candidate.name
    if (meeting_dir / "audio.wav").exists():
        return "audio", "audio.wav"
    return None


def _seek(candidate: float, *, is_video: bool, clip_offset: float) -> float:
    """Seek position in the SERVED media. audio.wav is clip-local; the source
    video is the full recording, so clip-local candidates need clip_offset added."""
    base = max(0.0, candidate - _LEAD_IN)
    return base + (clip_offset if is_video else 0.0)


def _load_roster_for(meeting_dir: Path):
    """Load the meeting's roster (by persisted body_slug) for name normalization,
    or None. Best-effort — never raises."""
    state_file = meeting_dir / "pipeline_state.json"
    body_slug = None
    if state_file.exists():
        try:
            body_slug = json.loads(state_file.read_text(encoding="utf-8")).get("body_slug")
        except (ValueError, OSError, AttributeError):
            body_slug = None
    if not body_slug:
        return None
    try:
        from src.roster import load_roster
        return load_roster(body_slug=body_slug)
    except Exception:
        return None


def _load_meeting_ctx(meeting_id: str):
    """(meeting, meeting_dir, roster) for a write-back, or None if unsafe/missing/malformed."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        meeting = Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None
    return meeting, meeting_dir, _load_roster_for(meeting_dir)


def _atomic_write_text(path: Path, text: str) -> None:
    """Crash-safe write: temp file in the same dir, then os.replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_embeddings(meeting_dir: Path) -> dict:
    """embeddings.json -> {label: np.ndarray}, or {} if absent/malformed."""
    emb_path = meeting_dir / "embeddings.json"
    if not emb_path.exists():
        return {}
    try:
        return {k: np.array(v) for k, v in json.loads(emb_path.read_text()).items()}
    except (ValueError, OSError, TypeError, AttributeError):
        return {}


def persist_review(meeting, meeting_dir: Path, embeddings: dict | None = None) -> None:
    """Persist review edits. Always: sync segments + write transcript_named.json
    (authoritative). When embeddings is given (a merge relabeled segments +
    combined embeddings), also rewrite diarization.json + embeddings.json,
    mirroring run_local._persist_after_review. Export + gate are best-effort."""
    for seg in meeting.segments:
        m = meeting.speakers.get(seg.speaker_label)
        if m and m.speaker_name:
            seg.speaker_name = m.speaker_name
            seg.confidence = m.confidence
            seg.id_method = m.id_method

    _atomic_write_text(
        meeting_dir / "transcript_named.json",
        json.dumps(meeting.to_dict(), indent=2),
    )

    if embeddings is not None:
        # Merge changed segment labels + embeddings — keep the caches consistent.
        try:
            _atomic_write_text(
                meeting_dir / "diarization.json",
                json.dumps([s.to_dict() for s in meeting.segments], indent=2),
            )
            emb_out = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in embeddings.items()}
            _atomic_write_text(meeting_dir / "embeddings.json", json.dumps(emb_out))
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to rewrite diarization/embeddings for %s after merge", meeting_dir.name,
                exc_info=True,
            )

    try:
        from src.export import export_all
        export_all(meeting, meeting_dir / "exports")
    except Exception:
        pass  # exports regenerate at publish time; never block a save

    try:
        from src import quality
        from src.checkpoint import PipelineState
        report = quality.evaluate_meeting(meeting)
        _atomic_write_text(meeting_dir / "quality.json", json.dumps(report, indent=2))
        state = PipelineState(meeting_dir)
        state.review_status = report.get("verdict")
        state.trusted_coverage = report.get("trusted_coverage")
        state.save()
    except Exception:
        logging.getLogger(__name__).warning(
            "Gate recompute failed for %s; library badge may be stale", meeting_dir.name,
            exc_info=True,
        )


def apply_rename(meeting_id: str, label: str, new_name: str) -> bool:
    """Rename a speaker (human-authoritative) and persist. Returns False on
    unsafe/unknown meeting, unknown label, or empty name (caller maps to 404/no-op)."""
    name = (new_name or "").strip()
    if not name:
        return False
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, roster = ctx

    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False

    from src import review
    review.rename_speaker(meeting.speakers, meeting.segments, label, name, roster=roster)
    persist_review(meeting, meeting_dir)
    return True


def search_politicians_safe(q: str, *, limit: int = 10) -> dict:
    """Best-effort essentials name search. Returns {"results": [...], "error": None|str}
    — never raises, so a network/HTTP/short-query failure just yields no results."""
    from src.essentials_client import EssentialsClientError, search_politicians
    try:
        raw = search_politicians(q, limit=limit)
    except EssentialsClientError as exc:
        return {"results": [], "error": str(exc)}
    except Exception as exc:  # transport/unexpected — stay best-effort
        return {"results": [], "error": f"search failed: {exc}"}
    results = [
        {
            "politician_slug": r.get("politician_slug"),
            "politician_id": r.get("politician_id"),
            "full_name": r.get("full_name"),
            "office_title": r.get("office_title"),
            "district_label": r.get("district_label"),
            "government_name": r.get("government_name"),
        }
        for r in raw
    ]
    return {"results": results, "error": None}


def apply_link(meeting_id: str, label: str, politician_slug: str, politician_id: str) -> bool:
    """Link a speaker to an essentials politician/candidate and persist. Accepts a
    slug OR an id (candidates have an id but no slug). False on unsafe/unknown
    meeting or label, or when BOTH slug and id are empty."""
    slug = (politician_slug or "").strip()
    pid = (politician_id or "").strip()
    if not slug and not pid:
        return False
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review
    review.link_speaker(meeting.speakers, label, slug or None, pid or None)
    persist_review(meeting, meeting_dir)
    return True


def apply_unlink(meeting_id: str, label: str) -> bool:
    """Clear a speaker's politician link and persist. False on unsafe/unknown."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review
    review.link_speaker(meeting.speakers, label, None, None)
    persist_review(meeting, meeting_dir)
    return True


def apply_merge(meeting_id: str, source_label: str, target_label: str) -> bool:
    """Merge source speaker into target and persist (incl. diarization+embeddings).
    False on unsafe/unknown meeting, unknown/equal labels, or merge failure."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if source_label not in known or target_label not in known or source_label == target_label:
        return False
    embeddings = _load_embeddings(meeting_dir)
    from src import review
    try:
        review.merge_speakers(meeting.segments, embeddings, meeting.speakers, source_label, target_label)
    except ValueError:
        return False
    persist_review(meeting, meeting_dir, embeddings=embeddings)
    return True


def _mark(meeting_id: str, label: str, fn) -> bool:
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    fn(meeting, meeting_dir)
    persist_review(meeting, meeting_dir)
    return True


def apply_mark_unidentified(meeting_id: str, label: str, display_label: str = "") -> bool:
    from src import review

    def fn(meeting, meeting_dir):
        review.mark_unidentified(
            meeting.speakers, meeting.segments, label,
            meeting_dir.name, display_label=(display_label or "").strip() or None,
        )
    return _mark(meeting_id, label, fn)


def apply_mark_non_speaker(meeting_id: str, label: str, display_label: str = "") -> bool:
    from src import review

    def fn(meeting, meeting_dir):
        review.mark_non_speaker(
            meeting.speakers, meeting.segments, label,
            display_label=(display_label or "").strip() or None,
        )
    return _mark(meeting_id, label, fn)


def apply_enroll(meeting_id: str, label: str) -> bool:
    """Enroll a named speaker's voice into the profile DB (idempotent per meeting).
    False on unsafe/unknown meeting, unknown label, no name, non-speaker, or no embedding."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, roster = ctx
    mapping = meeting.speakers.get(label)
    if mapping is None or not (mapping.speaker_name and mapping.speaker_name.strip()):
        return False
    if getattr(mapping, "speaker_status", None) == "non_speaker":
        return False
    embeddings = _load_embeddings(meeting_dir)
    emb = embeddings.get(label)
    if emb is None:
        return False

    from src.enroll import _enroll_mapping, load_profiles, resolve_mapping_enrollment, save_profiles
    db = load_profiles()
    key, _slug, _id = resolve_mapping_enrollment(mapping, roster)
    prof = db.profiles.get(key)
    if prof is not None and meeting_dir.name in getattr(prof, "meetings_seen", []):
        return True  # already enrolled from this meeting — idempotent no-op (no duplicate record)

    seg_count = sum(1 for s in meeting.segments if s.speaker_label == label)
    _enroll_mapping(db, mapping, emb, meeting_dir.name, seg_count, roster=roster)
    save_profiles(db)
    return True


def load_review_page(meeting_id: str) -> Optional[ReviewPageData]:
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        meeting = Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None

    import numpy as np
    from src.enroll import load_profiles, resolve_mapping_enrollment
    from src import review

    emb_path = meeting_dir / "embeddings.json"
    embeddings = {}
    if emb_path.exists():
        try:
            embeddings = {k: np.array(v) for k, v in json.loads(emb_path.read_text()).items()}
        except (ValueError, OSError, TypeError, AttributeError):
            embeddings = {}
    profile_db = load_profiles()
    # Load roster so enrollment keys resolve identically to apply_enroll — the
    # is_enrolled display must match what apply_enroll wrote for remapped names.
    roster = _load_roster_for(meeting_dir)

    views = review.build_review_state(
        meeting.segments, meeting.speakers, embeddings, profile_db, show_text=True
    )

    media = find_meeting_media(meeting_dir)
    media_kind = media[0] if media else None
    is_video = media_kind == "video"
    clip_offset = meeting.clip_start_seconds or 0.0

    confirmed: list[SpeakerCard] = []
    needs: list[SpeakerCard] = []
    for v in views:
        mapping = meeting.speakers.get(v.label)
        has_emb = v.label in embeddings
        named = bool(mapping and mapping.speaker_name and mapping.speaker_name.strip())
        not_nonspeaker = not (mapping and getattr(mapping, "speaker_status", None) == "non_speaker")
        is_enrollable = named and not_nonspeaker and has_emb
        is_enrolled = False
        profile_meetings = 0
        profile_samples = 0
        if named and not_nonspeaker:
            key, _slug, _id = resolve_mapping_enrollment(mapping, roster)
            prof = profile_db.profiles.get(key)
            if prof is not None:
                seen = getattr(prof, "meetings_seen", []) or []
                is_enrolled = meeting_dir.name in seen
                # Count only OTHER meetings until this one is enrolled, so the hint
                # shows the profile's existing strength (what enrolling would add to).
                profile_meetings = len(seen) - (1 if is_enrolled else 0)
                profile_samples = len(getattr(prof, "embeddings", []) or [])
        card = SpeakerCard(
            label=v.label,
            name=v.current_name,
            confidence=v.current_confidence,
            method=v.current_method,
            minutes=v.total_speech_seconds / 60.0,
            seg_count=v.seg_count,
            sample_text=v.sample_text,
            hints=[(h[0], h[1]) for h in v.soft_hints[:3]],
            clip_seeks=[_seek(c, is_video=is_video, clip_offset=clip_offset)
                        for c in v.clip_candidates],
            politician_slug=getattr(mapping, "politician_slug", None) if mapping else None,
            politician_id=getattr(mapping, "politician_id", None) if mapping else None,
            speaker_status=getattr(mapping, "speaker_status", None) if mapping else None,
            is_enrollable=is_enrollable,
            is_enrolled=is_enrolled,
            thin_sample=v.total_speech_seconds < ENROLL_MIN_SPEECH_SECONDS,
            profile_meetings=profile_meetings,
            profile_samples=profile_samples,
        )
        (confirmed if card.is_confirmed else needs).append(card)

    display_name = meeting.title or " ".join(
        p for p in (meeting.city, meeting.meeting_type) if p
    ) or meeting_id

    return ReviewPageData(
        meeting_id=meeting_id,
        display_name=display_name,
        media_kind=media_kind,
        needs_attention=needs,
        confirmed=confirmed,
    )

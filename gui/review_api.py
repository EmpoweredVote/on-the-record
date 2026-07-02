"""Load an already-processed meeting into a ReviewPageData, and (Slice 2b)
write review edits back to disk.

Mirrors run_local's --review loading: Meeting.from_dict(transcript_named.json),
embeddings.json, load_profiles(), then review.build_review_state(). Write-back
(persist_review / apply_rename) mirrors run_local's --review save + _apply_gate:
mutations go through src.review, then transcript_named.json is written
(authoritative) with best-effort re-export + gate recompute."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src import config
from src.models import Meeting

from gui.models import CONFIDENT_THRESHOLD, ReviewPageData, SpeakerCard
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


def persist_review(meeting, meeting_dir: Path) -> None:
    """Persist review edits: sync segment fields from mappings, write
    transcript_named.json, then best-effort re-export + gate recompute.

    Mirrors run_local's --review save + _apply_gate. The transcript write is
    authoritative and must succeed; export and gate are best-effort so a quirk
    in either can't lose the user's correction."""
    for seg in meeting.segments:
        m = meeting.speakers.get(seg.speaker_label)
        if m and m.speaker_name:
            seg.speaker_name = m.speaker_name
            seg.confidence = m.confidence
            seg.id_method = m.id_method

    (meeting_dir / "transcript_named.json").write_text(
        json.dumps(meeting.to_dict(), indent=2), encoding="utf-8"
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
        (meeting_dir / "quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        state = PipelineState(meeting_dir)
        state.review_status = report.get("verdict")
        state.trusted_coverage = report.get("trusted_coverage")
        state.save()
    except Exception:
        pass  # gate is best-effort; the transcript write above is the source of truth


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
    from src.enroll import load_profiles
    from src import review

    emb_path = meeting_dir / "embeddings.json"
    embeddings = {}
    if emb_path.exists():
        try:
            embeddings = {k: np.array(v) for k, v in json.loads(emb_path.read_text()).items()}
        except (ValueError, OSError, TypeError, AttributeError):
            embeddings = {}
    profile_db = load_profiles()

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

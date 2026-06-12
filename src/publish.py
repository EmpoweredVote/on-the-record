"""Publish processed meetings to Supabase for the public transcript site.

Idempotent by meeting_id: meetings/people/speaker rows are upserted; segments
are delete-then-inserted so re-publishes after segment merges or renumbering
never leave orphan rows. Word-level timestamps are deliberately not published
(they stay in transcript.json on disk).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .models import Meeting

SEGMENT_BATCH_SIZE = 500

# All site tables live in their own Postgres schema (shared Supabase project).
DB_SCHEMA = "civic"

_DIRECT_FILE_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")

_YOUTUBE_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")


def extract_youtube_id(url: str) -> Optional[str]:
    """Return the YouTube video id for any common YouTube URL shape, else None."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")

    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None

    if host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [""])[0]
            return vid or None
        for prefix in _YOUTUBE_PATH_PREFIXES:
            if parsed.path.startswith(prefix):
                vid = parsed.path[len(prefix):].split("/")[0]
                return vid or None

    return None


def resolve_playback(audio_source: str) -> tuple[Optional[str], Optional[str]]:
    """Map an audio_source to a (playback_kind, playback_url) pair for the site.

    Kinds: 'youtube' (url is the video id), 'file' (direct media URL for a
    native <video> element — covers CATS TV blob storage), 'hls' (.m3u8).
    Unknown providers and local paths return (None, None); the site then
    renders transcript-only with a plain source link.
    """
    source = (audio_source or "").strip()
    if not source.startswith(("http://", "https://")):
        return None, None

    video_id = extract_youtube_id(source)
    if video_id:
        return "youtube", video_id

    parsed = urlparse(source)
    path = parsed.path.lower()

    # CATS TV page URLs resolve to a direct blob media URL (network fetch).
    if "catstv.net" in parsed.netloc:
        try:
            from .download import _extract_blob_url_from_page

            return "file", _extract_blob_url_from_page(source)
        except Exception:
            return None, None

    if path.endswith(_DIRECT_FILE_EXTENSIONS):
        return "file", source

    if path.endswith(".m3u8"):
        return "hls", source

    return None, None


@dataclass
class PublishResult:
    meeting_id: str
    segments: int
    speakers: int
    people: int


def _require_env() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    # New-style secret key (sb_secret_...); legacy service_role JWT still
    # accepted as a fallback for older projects.
    key = (
        os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if not url or not key:
        raise RuntimeError(
            "Publishing requires SUPABASE_URL and SUPABASE_SECRET_KEY "
            "(add them to .env.local)."
        )
    return url, key


def _validate_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise RuntimeError(
            f"Meeting date {date_str!r} is not YYYY-MM-DD; cannot publish. "
            "Fix the date in transcript_named.json and retry."
        )


def build_meeting_row(meeting: Meeting, body_slug: Optional[str]) -> dict:
    source = (meeting.audio_source or "").strip()
    is_url = source.startswith(("http://", "https://"))
    kind, playback_url = resolve_playback(source)

    # For CATS TV blob sources the media URL doubles as the citation link;
    # there's no separate landing page recorded.
    return {
        "meeting_id": meeting.meeting_id,
        "city": meeting.city,
        "body_slug": body_slug,
        "meeting_type": meeting.meeting_type,
        "meeting_date": _validate_date(meeting.date),
        "source_url": source if is_url else None,
        "playback_kind": kind,
        "playback_url": playback_url,
        "duration_seconds": meeting.duration_seconds or None,
        "summary": meeting.summary.to_dict() if meeting.summary else None,
        "processing_metadata": meeting.processing_metadata.to_dict(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_people_rows(meeting: Meeting, body_slug: Optional[str]) -> list[dict]:
    """One row per identified speaker with a politician_slug, enriched from the roster."""
    district_by_slug: dict[str, Optional[str]] = {}
    roster_city = None
    if body_slug:
        try:
            from .roster import load_roster

            roster = load_roster(body_slug=body_slug)
            if roster:
                roster_city = roster.city or None
                district_by_slug = {
                    m.politician_slug: m.district_label
                    for m in roster.members
                    if m.politician_slug
                }
        except Exception:
            pass  # roster enrichment is best-effort

    rows = {}
    for mapping in meeting.speakers.values():
        slug = mapping.politician_slug
        if not slug:
            continue
        rows[slug] = {
            "politician_slug": slug,
            "politician_id": mapping.politician_id,
            "display_name": mapping.speaker_name or slug,
            "district_label": district_by_slug.get(slug),
            "city": roster_city or meeting.city,
        }
    return list(rows.values())


def build_speaker_rows(meeting: Meeting) -> list[dict]:
    return [
        {
            "meeting_id": meeting.meeting_id,
            "speaker_label": mapping.speaker_label,
            "display_name": mapping.speaker_name,
            "politician_slug": mapping.politician_slug,
            "confidence": mapping.confidence,
            "id_method": mapping.id_method,
        }
        for mapping in meeting.speakers.values()
    ]


def build_segment_rows(meeting: Meeting) -> list[dict]:
    """Segment rows without words; politician_slug denormalized from speaker map."""
    slug_by_label = {
        label: m.politician_slug for label, m in meeting.speakers.items()
    }
    rows = []
    for seg in meeting.segments:
        if not seg.text:
            continue
        rows.append(
            {
                "meeting_id": meeting.meeting_id,
                "segment_id": seg.segment_id,
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "speaker_label": seg.speaker_label,
                "speaker_name": seg.speaker_name,
                "politician_slug": slug_by_label.get(seg.speaker_label),
                "text": seg.text,
                "confidence": seg.confidence,
            }
        )
    return rows


def publish_meeting(meeting: Meeting, body_slug: Optional[str] = None) -> PublishResult:
    """Push one meeting into Supabase. Safe to re-run (idempotent by meeting_id)."""
    from supabase import create_client

    url, key = _require_env()
    client = create_client(url, key).schema(DB_SCHEMA)

    meeting_row = build_meeting_row(meeting, body_slug)
    people_rows = build_people_rows(meeting, body_slug)
    speaker_rows = build_speaker_rows(meeting)
    segment_rows = build_segment_rows(meeting)

    client.table("meetings").upsert(meeting_row, on_conflict="meeting_id").execute()

    if people_rows:
        client.table("people").upsert(
            people_rows, on_conflict="politician_slug"
        ).execute()

    if speaker_rows:
        client.table("meeting_speakers").upsert(
            speaker_rows, on_conflict="meeting_id,speaker_label"
        ).execute()

    # Delete-then-insert so renumbered segments never leave orphans.
    client.table("segments").delete().eq("meeting_id", meeting.meeting_id).execute()
    for i in range(0, len(segment_rows), SEGMENT_BATCH_SIZE):
        client.table("segments").insert(
            segment_rows[i : i + SEGMENT_BATCH_SIZE]
        ).execute()

    return PublishResult(
        meeting_id=meeting.meeting_id,
        segments=len(segment_rows),
        speakers=len(speaker_rows),
        people=len(people_rows),
    )

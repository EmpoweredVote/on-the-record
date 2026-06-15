"""Publish processed meetings to the meetings.* schema via direct Postgres.

Idempotent by meeting slug:
  - Meeting row: upsert keyed on slug (INSERT or UPDATE)
  - Speaker rows: upsert keyed on (meeting_id, label)
  - Segment rows: delete-then-insert so re-publishes never leave orphan rows

Word-level timestamps are deliberately not published (they stay in
transcript.json on disk).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import psycopg2
import psycopg2.extras

from .event_entities import validate_event_entities
from .models import Meeting

SEGMENT_BATCH_SIZE = 500

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

    Kinds: 'youtube' (url is the video id), 'file' (direct media URL),
    'hls' (.m3u8). Unknown providers return (None, None); the site renders
    transcript-only with a plain source link.
    """
    source = (audio_source or "").strip()
    if not source.startswith(("http://", "https://")):
        return None, None

    video_id = extract_youtube_id(source)
    if video_id:
        return "youtube", video_id

    parsed = urlparse(source)
    path = parsed.path.lower()

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


def _require_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "Publishing requires DATABASE_URL (add it to .env.local). "
            "Get it from Supabase dashboard: Project Settings → Database → "
            "Connection string (URI mode, port 5432)."
        )
    return url


def _validate_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise RuntimeError(
            f"Meeting date {date_str!r} is not YYYY-MM-DD; cannot publish. "
            "Fix the date in transcript_named.json and retry."
        )


def _resolve_chamber_id(cur, body_slug: Optional[str]) -> Optional[str]:
    if body_slug is None:
        return None

    cur.execute(
        """
        SELECT id
        FROM essentials.chambers
        WHERE slug = %s
        ORDER BY id
        LIMIT 2
        """,
        (body_slug,),
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            f"Body slug {body_slug!r} matched {len(rows)} chambers; "
            "publishing requires exactly one"
        )
    return str(rows[0][0])


def _upsert_meeting(cur, meeting: Meeting, body_slug: Optional[str]) -> str:
    """Insert or update the meeting row. Returns the meetings.meetings UUID."""
    chamber_id = _resolve_chamber_id(cur, body_slug)
    entity_error = validate_event_entities(
        meeting.event_kind,
        chamber_id,
        meeting.race_id,
    )
    if entity_error:
        raise RuntimeError(entity_error)

    source = (meeting.audio_source or "").strip()
    is_url = source.startswith(("http://", "https://"))
    kind, playback_url = resolve_playback(source)
    date = _validate_date(meeting.date)
    summary = meeting.summary.to_dict() if meeting.summary else None
    proc_meta = (
        meeting.processing_metadata.to_dict()
        if meeting.processing_metadata
        else None
    )

    cur.execute(
        "SELECT id FROM meetings.meetings WHERE slug = %s",
        (meeting.meeting_id,),
    )
    row = cur.fetchone()

    if row:
        meeting_uuid = row[0]
        cur.execute(
            """
            UPDATE meetings.meetings SET
              city = %s,
              date = %s,
              meeting_type = %s,
              title = %s,
              event_kind = %s,
              duration_seconds = %s,
              audio_source = %s,
              video_url = %s,
              status = %s,
              chamber_id = %s,
              race_id = %s,
              source_url = %s,
              playback_kind = %s,
              summary = %s,
              processing_metadata = %s,
              updated_at = NOW()
            WHERE id = %s
            """,
            (
                meeting.city,
                date,
                meeting.meeting_type,
                meeting.title,
                meeting.event_kind,
                meeting.duration_seconds or None,
                source or None,
                playback_url,
                "published",
                chamber_id,
                meeting.race_id,
                source if is_url else None,
                kind,
                psycopg2.extras.Json(summary),
                psycopg2.extras.Json(proc_meta),
                meeting_uuid,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO meetings.meetings
              (id, city, date, meeting_type, title, event_kind, duration_seconds,
               audio_source, video_url, status,
               chamber_id, race_id, source_url, playback_kind, slug,
               summary, processing_metadata,
               created_at, updated_at)
            VALUES
              (gen_random_uuid(), %s, %s, %s, %s, %s, %s,
               %s, %s, %s,
               %s, %s, %s, %s, %s,
               %s, %s,
               NOW(), NOW())
            RETURNING id
            """,
            (
                meeting.city,
                date,
                meeting.meeting_type,
                meeting.title,
                meeting.event_kind,
                meeting.duration_seconds or None,
                source or None,
                playback_url,
                "published",
                chamber_id,
                meeting.race_id,
                source if is_url else None,
                kind,
                meeting.meeting_id,
                psycopg2.extras.Json(summary),
                psycopg2.extras.Json(proc_meta),
            ),
        )
        meeting_uuid = cur.fetchone()[0]

    return meeting_uuid


def _upsert_speakers(
    cur, meeting: Meeting, meeting_uuid: str
) -> dict[str, str]:
    """Upsert speaker rows. Returns {speaker_label: speaker_uuid}."""
    label_to_uuid: dict[str, str] = {}

    for mapping in meeting.speakers.values():
        cur.execute(
            "SELECT id FROM meetings.speakers WHERE meeting_id = %s AND label = %s",
            (meeting_uuid, mapping.speaker_label),
        )
        row = cur.fetchone()

        if row:
            speaker_uuid = row[0]
            cur.execute(
                """
                UPDATE meetings.speakers SET
                  display_name = %s,
                  politician_slug = %s,
                  politician_id = %s,
                  confidence = %s,
                  id_method = %s
                WHERE id = %s
                """,
                (
                    mapping.speaker_name,
                    mapping.politician_slug,
                    mapping.politician_id,
                    mapping.confidence,
                    mapping.id_method,
                    speaker_uuid,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO meetings.speakers
                  (id, meeting_id, label, display_name,
                   politician_slug, politician_id, confidence, id_method,
                   created_at)
                VALUES
                  (gen_random_uuid(), %s, %s, %s,
                   %s, %s, %s, %s,
                   NOW())
                RETURNING id
                """,
                (
                    meeting_uuid,
                    mapping.speaker_label,
                    mapping.speaker_name,
                    mapping.politician_slug,
                    mapping.politician_id,
                    mapping.confidence,
                    mapping.id_method,
                ),
            )
            speaker_uuid = cur.fetchone()[0]

        label_to_uuid[mapping.speaker_label] = speaker_uuid

    return label_to_uuid


def _replace_segments(
    cur,
    meeting: Meeting,
    meeting_uuid: str,
    label_to_uuid: dict[str, str],
) -> int:
    """Delete then batch-insert segments. Returns segment count."""
    cur.execute(
        "DELETE FROM meetings.segments WHERE meeting_id = %s",
        (meeting_uuid,),
    )

    slug_by_label = {
        label: m.politician_slug for label, m in meeting.speakers.items()
    }

    rows = []
    for seg in meeting.segments:
        if not seg.text:
            continue
        rows.append((
            meeting_uuid,
            label_to_uuid.get(seg.speaker_label),
            seg.segment_id,
            seg.start_time,
            seg.end_time,
            seg.text,
            seg.speaker_name,
            slug_by_label.get(seg.speaker_label),
            seg.confidence,
        ))

    for i in range(0, len(rows), SEGMENT_BATCH_SIZE):
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO meetings.segments
              (meeting_id, speaker_id, segment_index,
               start_time, end_time, text,
               speaker_name, politician_slug, confidence)
            VALUES %s
            """,
            rows[i : i + SEGMENT_BATCH_SIZE],
        )

    return len(rows)


def _replace_topics(cur, meeting_uuid: str, meeting: "Meeting") -> None:
    """Delete-then-insert meeting_topics rows from meeting.section_topics.

    Denormalizes section metadata (title/type/times) so topic pages are a
    single query. status is always 'predicted' in this build.

    Guard runs BEFORE the delete: an empty section_topics almost always means
    classification wasn't loaded for this publish (e.g. a standalone
    --publish-meeting where topics.json wasn't read), not that the meeting
    genuinely has no topics. Deleting first would wipe previously-published
    tags on every plain re-publish. Only replace when we have a fresh set.
    """
    if not meeting.section_topics or not meeting.summary:
        return

    cur.execute(
        "DELETE FROM meetings.meeting_topics WHERE meeting_id = %s",
        (meeting_uuid,),
    )

    sections = meeting.summary.sections
    model = meeting.summary.model or None
    rows = []
    for st in meeting.section_topics:
        if st.section_index < 0 or st.section_index >= len(sections):
            continue
        sec = sections[st.section_index]
        for key in st.topic_keys:
            rows.append((
                meeting_uuid, st.section_index, key, "predicted",
                st.confidence, model,
                sec.title, sec.section_type, sec.start_time, sec.end_time,
            ))

    if rows:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO meetings.meeting_topics
              (meeting_id, section_index, topic_key, status, confidence, model,
               section_title, section_type, start_time, end_time)
            VALUES %s
            """,
            rows,
        )


def publish_meeting(
    meeting: Meeting, body_slug: Optional[str] = None
) -> PublishResult:
    """Push one meeting into the meetings.* schema. Idempotent by slug."""
    db_url = _require_db_url()

    conn = psycopg2.connect(db_url)
    try:
        with conn:
            with conn.cursor() as cur:
                meeting_uuid = _upsert_meeting(cur, meeting, body_slug)
                label_to_uuid = _upsert_speakers(cur, meeting, meeting_uuid)
                segment_count = _replace_segments(
                    cur, meeting, meeting_uuid, label_to_uuid
                )
                _replace_topics(cur, meeting_uuid, meeting)
                speaker_count = len(label_to_uuid)

                cur.execute(
                    """
                    UPDATE meetings.meetings
                    SET segment_count = %s, speaker_count = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (segment_count, speaker_count, meeting_uuid),
                )
    finally:
        conn.close()

    return PublishResult(
        meeting_id=meeting.meeting_id,
        segments=segment_count,
        speakers=speaker_count,
    )

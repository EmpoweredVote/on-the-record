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
        # Multi-chamber body slugs (e.g. full council) can't be pinned to one
        # seat — treat as unchambered rather than blocking publish.
        return None
    return str(rows[0][0])


def resolve_races_for_politicians(cur, politician_ids) -> list[str]:
    """All distinct essentials races the given linked politicians belong to.

    A meeting's races are the union of its linked candidates' races. Returns
    every distinct race_id (no "exactly one" gate) so multi-race forums are
    represented; [] when there are no ids or no race_candidates rows. Casts to
    uuid[] because essentials.race_candidates.politician_id is a uuid column and
    psycopg2 sends a Python list as text[].
    """
    ids = [pid for pid in (politician_ids or []) if pid]
    if not ids:
        return []
    cur.execute(
        """
        SELECT DISTINCT race_id
        FROM essentials.race_candidates
        WHERE politician_id = ANY(%s::uuid[])
        """,
        (ids,),
    )
    return [str(r[0]) for r in cur.fetchall()]


def _reconcile_event_races(cur, meeting: Meeting, meeting_uuid: str) -> list[str]:
    """Derive the meeting's races from its linked candidates and reconcile the
    meetings.event_races join table (delete this meeting's rows, insert the
    current set). Returns the race ids written.

    debate/forum require >=1 derived race: an empty set raises (aborting the
    publish transaction) — recoverable by linking candidates, then re-publishing.
    council/school_board legitimately have no races; an empty set just clears
    stale rows.
    """
    pol_ids = [m.politician_id for m in meeting.speakers.values() if m.politician_id]
    races = resolve_races_for_politicians(cur, pol_ids)

    if not races and meeting.event_kind in ("debate", "forum"):
        raise RuntimeError(
            f"{meeting.meeting_id}: {meeting.event_kind} resolved to no race — "
            "no linked candidate maps to an essentials race yet. Link candidates, "
            "then re-publish."
        )

    cur.execute("DELETE FROM meetings.event_races WHERE meeting_id = %s", (meeting_uuid,))
    for race_id in races:
        cur.execute(
            "INSERT INTO meetings.event_races (meeting_id, race_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (meeting_uuid, race_id),
        )
    return races


def _upsert_meeting(cur, meeting: Meeting, body_slug: Optional[str]) -> str:
    """Insert or update the meeting row. Returns the meetings.meetings UUID."""
    chamber_id = _resolve_chamber_id(cur, body_slug)
    entity_error = validate_event_entities(
        meeting.event_kind,
        chamber_id,
        None,
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
              source_url = %s,
              playback_kind = %s,
              clip_start_seconds = %s,
              clip_end_seconds = %s,
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
                source if is_url else None,
                kind,
                meeting.clip_start_seconds,
                meeting.clip_end_seconds,
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
               chamber_id, source_url, playback_kind, clip_start_seconds, clip_end_seconds, slug,
               summary, processing_metadata,
               created_at, updated_at)
            VALUES
              (gen_random_uuid(), %s, %s, %s, %s, %s, %s,
               %s, %s, %s,
               %s, %s, %s, %s, %s, %s,
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
                source if is_url else None,
                kind,
                meeting.clip_start_seconds,
                meeting.clip_end_seconds,
                meeting.meeting_id,
                psycopg2.extras.Json(summary),
                psycopg2.extras.Json(proc_meta),
            ),
        )
        meeting_uuid = cur.fetchone()[0]

    return meeting_uuid


def _upsert_event_orgs(cur, meeting_slug: str, event_orgs: list) -> None:
    """Delete then re-insert event_orgs for this meeting. Idempotent."""
    cur.execute(
        "DELETE FROM meetings.event_orgs WHERE meeting_id = %s",
        (meeting_slug,),
    )
    for org_name in event_orgs:
        cur.execute(
            """
            INSERT INTO meetings.event_orgs (id, meeting_id, org_name, created_at)
            VALUES (gen_random_uuid(), %s, %s, NOW())
            """,
            (meeting_slug, org_name),
        )


def _published_local_slug(mapping) -> "str | None":
    """The local_slug to publish for a speaker, or None to publish no local
    person. An unidentified handle is a placeholder, not a public entity, so it
    is suppressed until promoted to a real person."""
    if getattr(mapping, "speaker_status", None) == "unidentified":
        return None
    return mapping.local_slug


def _upsert_local_people(cur, meeting: Meeting) -> None:
    """Upsert local_people rows for any speaker mapping with local_slug set.

    Must be called BEFORE _upsert_speakers so the FK from meetings.speakers.local_slug
    to meetings.local_people.slug is satisfied at write time.
    """
    for mapping in meeting.speakers.values():
        slug = _published_local_slug(mapping)
        if not slug:
            continue
        cur.execute(
            """
            INSERT INTO meetings.local_people
              (slug, name, role, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (slug) DO UPDATE SET
              name = EXCLUDED.name,
              role = EXCLUDED.role,
              updated_at = NOW()
            """,
            (
                slug,
                mapping.speaker_name or slug,
                mapping.local_role or 'candidate',
            ),
        )


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
                  id_method = %s,
                  local_slug = %s
                WHERE id = %s
                """,
                (
                    mapping.speaker_name,
                    mapping.politician_slug,
                    mapping.politician_id,
                    mapping.confidence,
                    mapping.id_method,
                    _published_local_slug(mapping),
                    speaker_uuid,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO meetings.speakers
                  (id, meeting_id, label, display_name,
                   politician_slug, politician_id, confidence, id_method,
                   local_slug, created_at)
                VALUES
                  (gen_random_uuid(), %s, %s, %s,
                   %s, %s, %s, %s,
                   %s, NOW())
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
                    _published_local_slug(mapping),
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


def _trigger_deploy_hook() -> None:
    """POST to the Render deploy hook URL if RENDER_DEPLOY_HOOK_URL is set.

    Called after a successful DB publish so the static site rebuilds
    automatically. Failures are logged but never raised so a hook error
    never rolls back a completed publish.
    """
    url = os.environ.get("RENDER_DEPLOY_HOOK_URL", "").strip()
    if not url:
        return

    import urllib.request

    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"  Deploy hook triggered (HTTP {resp.status})")
    except Exception as exc:
        print(f"  WARNING: Deploy hook failed — {exc}")
        print(f"    Trigger manually: curl -X POST '{url}'")


def publish_meeting(
    meeting: Meeting, body_slug: Optional[str] = None, trigger_deploy: bool = True
) -> PublishResult:
    """Push one meeting into the meetings.* schema. Idempotent by slug."""
    from .clip import absolutize_meeting_times
    meeting = absolutize_meeting_times(meeting)
    db_url = _require_db_url()

    conn = psycopg2.connect(db_url)
    try:
        with conn:
            with conn.cursor() as cur:
                meeting_uuid = _upsert_meeting(cur, meeting, body_slug)
                _upsert_event_orgs(cur, meeting.meeting_id, meeting.event_orgs)
                _upsert_local_people(cur, meeting)
                label_to_uuid = _upsert_speakers(cur, meeting, meeting_uuid)
                _reconcile_event_races(cur, meeting, meeting_uuid)
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

    # The web app now reads data live from the API, so publishing no longer needs
    # to rebuild the static site (and the per-publish rebuild caused a deploy-hook
    # race that staled the meeting list). Code deploys happen via git push.
    # _trigger_deploy_hook() is intentionally not called here.

    return PublishResult(
        meeting_id=meeting.meeting_id,
        segments=segment_count,
        speakers=speaker_count,
    )

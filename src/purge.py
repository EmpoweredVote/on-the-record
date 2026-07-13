# src/purge.py
"""Hard-delete a meeting: remove its local folder and all meetings.* rows from the
live DB. Reports (never deletes) derived essentials.quotes, and warns when the
meeting contributed to voice profiles. Deliberate, irreversible full erase — the
opposite of src/cleanup.py (which keeps the transcript/audio).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import psycopg2

from src import config

logger = logging.getLogger(__name__)

# meetings.* child tables keyed by the meeting UUID (deleted before the parent row).
_UUID_CHILD_TABLES = ("segments", "speakers", "event_races", "meeting_topics")


def _is_safe_meeting_id(meeting_id: str) -> bool:
    return (
        bool(meeting_id)
        and meeting_id not in (".", "..")
        and "/" not in meeting_id
        and "\\" not in meeting_id
        and ".." not in meeting_id
    )


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def _meeting_row_exists(slug: str) -> bool:
    """True if a meetings.meetings row exists for slug. False if no DB / error."""
    url = _db_url()
    if not url:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM meetings.meetings WHERE slug = %s", (slug,))
                return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - existence check is best-effort
        logger.warning("meeting-row existence check failed for %s: %s", slug, exc)
        return False


def _delete_meeting_db(slug: str) -> tuple[bool, dict]:
    """Delete all meetings.* rows for slug in ONE transaction (children before the
    parent). Returns (db_deleted, rows_deleted). db_deleted is False when the DB
    isn't configured or no row exists. Raises (after rollback) on a mid-delete error.
    """
    url = _db_url()
    if not url:
        return False, {}
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM meetings.meetings WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if row is None:
                return False, {}
            meeting_uuid = row[0]
            rows_deleted: dict = {}
            for table in _UUID_CHILD_TABLES:
                cur.execute(f"DELETE FROM meetings.{table} WHERE meeting_id = %s", (meeting_uuid,))
                rows_deleted[table] = cur.rowcount
            cur.execute("DELETE FROM meetings.event_orgs WHERE meeting_id = %s", (slug,))
            rows_deleted["event_orgs"] = cur.rowcount
            cur.execute("DELETE FROM meetings.meetings WHERE slug = %s", (slug,))
            rows_deleted["meetings"] = cur.rowcount
        conn.commit()
        return True, rows_deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _resolve_source_url(meeting_dir: Path, slug: str) -> Optional[str]:
    """The meeting's source URL for quote matching: local transcript's audio_source,
    else the DB source_url. None if neither available."""
    named = meeting_dir / "transcript_named.json"
    if named.exists():
        try:
            src = json.loads(named.read_text(encoding="utf-8")).get("audio_source")
            if src:
                return src
        except (ValueError, OSError):
            pass
    url = _db_url()
    if url:
        try:
            conn = psycopg2.connect(url)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT source_url FROM meetings.meetings WHERE slug = %s", (slug,))
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("source_url lookup failed for %s: %s", slug, exc)
    return None


def _find_orphan_quotes(source_url: Optional[str]) -> list[dict]:
    """Report essentials.quotes whose source_url matches this meeting (by YouTube id
    when present, else the raw URL). READ-ONLY — never deletes. [] if no DB/URL/error."""
    url = _db_url()
    if not url or not source_url:
        return []
    from src.publish import extract_youtube_id

    vid = extract_youtube_id(source_url)
    like = f"%{vid}%" if vid else f"%{source_url}%"
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, politician_id, topic_key, source_url, left(quote_text, 80) "
                    "FROM essentials.quotes WHERE source_url LIKE %s",
                    (like,),
                )
                return [
                    {"id": r[0], "politician_id": r[1], "topic_key": r[2],
                     "source_url": r[3], "preview": r[4]}
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - quote report is best-effort
        logger.warning("orphan-quote lookup failed: %s", exc)
        return []


def _profile_contaminated(slug: str) -> bool:
    """True if slug appears in any voice profile's meetings_seen. Best-effort."""
    try:
        from src.enroll import load_profiles

        db = load_profiles()
        return any(slug in (getattr(p, "meetings_seen", []) or []) for p in db.profiles.values())
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile contamination check failed for %s: %s", slug, exc)
        return False


def purge_meeting(meeting_id: str, *, delete_db: bool = True, delete_local: bool = True) -> dict:
    """Full-erase a meeting. Returns a result dict. Reports quotes (never deletes
    them); leaves voice profiles intact (only flags contamination)."""
    result = {
        "meeting_id": meeting_id,
        "status": "deleted",
        "db_deleted": False,
        "rows_deleted": {},
        "local_deleted": False,
        "quotes_found": [],
        "profile_contamination": False,
    }
    if not _is_safe_meeting_id(meeting_id):
        result["status"] = "invalid"
        return result

    meeting_dir = config.MEETINGS_DIR / meeting_id
    dir_exists = meeting_dir.is_dir()
    row_exists = _meeting_row_exists(meeting_id)
    if not dir_exists and not row_exists:
        result["status"] = "not_found"
        return result

    # Read-only quote report BEFORE destructive steps.
    result["quotes_found"] = _find_orphan_quotes(_resolve_source_url(meeting_dir, meeting_id))

    if delete_db:
        db_deleted, rows_deleted = _delete_meeting_db(meeting_id)
        result["db_deleted"] = db_deleted
        result["rows_deleted"] = rows_deleted

    if delete_local and meeting_dir.is_dir():
        shutil.rmtree(meeting_dir)
        result["local_deleted"] = True

    result["profile_contamination"] = _profile_contaminated(meeting_id)
    return result

"""Push GUI metadata edits to the meetings.* Supabase schema. Reuses src.publish's
connection model (DATABASE_URL + psycopg2, keyed on slug). Best-effort: when the
DB isn't configured or the meeting isn't published, Supabase steps are skipped —
the local write is always authoritative."""
from __future__ import annotations

import os
from typing import Optional

import psycopg2

# Display columns a metadata edit may change. NEVER includes slug/id (ADR-0002).
_EDITABLE = ("title", "city", "date", "meeting_type", "event_kind")


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def meeting_published_id(meeting_id: str) -> Optional[str]:
    """The Supabase UUID for a published meeting (row where slug = meeting_id),
    or None if unpublished / DB not configured / any error."""
    url = _db_url()
    if not url:
        return None
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM meetings.meetings WHERE slug = %s", (meeting_id,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def update_supabase_metadata(meeting_id: str, fields: dict) -> bool:
    """UPDATE the editable display columns for a published meeting. Returns True if
    a row was updated, False if unpublished / not configured / error. Never raises."""
    url = _db_url()
    if not url:
        return False
    cols = [c for c in _EDITABLE if c in fields]
    if not cols:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM meetings.meetings WHERE slug = %s", (meeting_id,))
                if cur.fetchone() is None:
                    return False  # unpublished — nothing to update
                set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = NOW()"
                params = [fields[c] for c in cols] + [meeting_id]
                cur.execute(
                    f"UPDATE meetings.meetings SET {set_clause} WHERE slug = %s", params
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False

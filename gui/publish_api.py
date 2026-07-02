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


def apply_metadata_edit(meeting_id: str, fields: dict) -> Optional[dict]:
    """Apply display-metadata edits (title/city/date/meeting_type/event_kind).
    Writes the local meeting files, then best-effort pushes to Supabase if the
    meeting is published. NEVER changes the slug / meeting_id / directory
    (ADR-0002). Returns {"local": bool, "supabase": bool} or None if the meeting
    doesn't exist."""
    from gui.review_api import _atomic_write_text, _load_meeting_ctx
    import json as _json

    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return None
    meeting, meeting_dir, _roster = ctx

    edits = {k: v for k, v in fields.items() if k in _EDITABLE}
    for k, v in edits.items():
        setattr(meeting, k, (v.strip() or None) if isinstance(v, str) else v)

    # local: transcript_named.json (the Meeting) — meeting_id/slug untouched.
    _atomic_write_text(meeting_dir / "transcript_named.json",
                       _json.dumps(meeting.to_dict(), indent=2))
    # local: pipeline_state display fields (for --resume parity).
    from src.checkpoint import PipelineState
    state = PipelineState(meeting_dir)
    for k in ("city", "date", "meeting_type", "event_kind"):
        if k in edits:
            setattr(state, k, getattr(meeting, k))
    state.save()

    pushed = update_supabase_metadata(meeting_id, edits) if edits else False
    return {"local": True, "supabase": pushed}


def apply_publish(meeting_id: str, *, force: bool = False) -> dict:
    """Publish a meeting to the live site via src.publish.publish_meeting.

    Gated on the confidence gate: only publishes when review_status == "pass",
    unless force=True (human override). Best-effort — returns a structured result,
    never raises. reasons: "unknown" | "gate" | "no_db" | "error"."""
    from gui.review_api import _load_meeting_ctx
    from src.checkpoint import PipelineState

    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return {"ok": False, "reason": "unknown"}
    meeting, meeting_dir, _roster = ctx
    state = PipelineState(meeting_dir)
    review_status = state.review_status

    if not force and review_status != "pass":
        return {"ok": False, "reason": "gate", "review_status": review_status}
    if not _db_url():
        return {"ok": False, "reason": "no_db"}
    try:
        from src.publish import publish_meeting
        result = publish_meeting(meeting, state.body_slug)
        return {"ok": True, "meeting_id": result.meeting_id,
                "segments": result.segments, "speakers": result.speakers}
    except Exception as exc:  # DB / validation failure — surface, don't crash
        return {"ok": False, "reason": "error", "error": str(exc)}

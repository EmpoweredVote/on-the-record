"""Import cloud-processed House-floor sessions onto the Mac for local review."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FloorSession:
    slug: str
    date: str | None
    gate_verdict: str | None
    gate_coverage: float | None
    is_local: bool


def _query_draft_floor_rows() -> list[dict]:
    """Draft floor meetings from the DB, newest first. [] if DB not configured."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return []
    import psycopg2  # local import: keeps import-time hermetic for tests
    from psycopg2.extras import RealDictCursor
    sql = (
        "SELECT slug, date::text AS date, "
        "  processing_metadata->>'gate_verdict' AS gate_verdict, "
        "  (processing_metadata->>'gate_coverage')::float AS gate_coverage "
        "FROM meetings.meetings "
        "WHERE status = 'draft' AND slug LIKE %s "
        "ORDER BY date DESC"
    )
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, ("%-house-floor",))
            return [dict(r) for r in cur.fetchall()]


def list_floor_sessions(meetings_dir: Path) -> list[FloorSession]:
    """Draft House-floor sessions from the DB, marking which are already local
    (a meetings_dir/<slug> directory exists). [] if the DB is not configured."""
    out: list[FloorSession] = []
    for r in _query_draft_floor_rows():
        slug = r["slug"]
        out.append(FloorSession(
            slug=slug, date=r.get("date"),
            gate_verdict=r.get("gate_verdict"),
            gate_coverage=r.get("gate_coverage"),
            is_local=(meetings_dir / slug).is_dir(),
        ))
    return out

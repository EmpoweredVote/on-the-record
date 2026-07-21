"""Search essentials.races (via DATABASE_URL + psycopg2, like gui.publish_api) and
compose a human label + a URL slug for a race. Best-effort: when the DB isn't
configured or a query fails, search returns no results rather than raising —
mirroring gui.review_api.search_politicians_safe."""
from __future__ import annotations

import os
import re
from typing import Optional

import psycopg2

# Tokens dropped from a race slug: the "U.S." pair and English connectives.
_SLUG_DROP = {"u", "s", "of", "the"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def race_slug(position_name: str) -> str:
    """A clean URL token from a race's position_name (see module tests)."""
    tokens = [t for t in _slug(position_name).split("-") if t and t not in _SLUG_DROP]
    return "-".join(tokens)


def race_display(position_name: str, year: Optional[int]) -> str:
    """'Governor of Michigan · 2026' (omit the year suffix when year is None)."""
    name = (position_name or "").strip()
    return f"{name} · {year}" if year else name


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def search_races_safe(q: str, *, limit: int = 20) -> dict:
    """Best-effort race search by position_name. Returns
    {"results": [{"race_id","label","slug"}], "error": None|str} — never raises."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "error": None}
    url = _db_url()
    if not url:
        return {"results": [], "error": None}
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.position_name,
                           EXTRACT(YEAR FROM e.election_date)::int AS yr
                    FROM essentials.races r
                    LEFT JOIN essentials.elections e ON e.id = r.election_id
                    WHERE r.position_name ILIKE %s
                    ORDER BY e.election_date DESC NULLS LAST, r.position_name
                    LIMIT %s
                    """,
                    (f"%{query}%", limit),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:  # DB down / auth / schema — stay best-effort
        return {"results": [], "error": f"race search failed: {exc}"}
    results = [
        {"race_id": str(rid), "label": race_display(name, yr), "slug": race_slug(name)}
        for (rid, name, yr) in rows
    ]
    return {"results": results, "error": None}

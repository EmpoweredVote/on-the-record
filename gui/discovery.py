"""Data layer for the Discovery triage tab.

Best-effort like gui/races.py: no DATABASE_URL or DB error -> empty values,
never a crash. Writes commit explicitly.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import psycopg2


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")

_SELECT = """
    select d.id::text, d.url, d.title, d.description_snippet, d.channel_name,
           d.channel_id, d.channel_url, d.outlet_id::text, d.duration_seconds,
           d.published_at::text, d.race_id::text, d.event_kind_guess,
           d.source_tier_guess, d.route, d.confidence, d.why, d.discovered_via,
           d.status, e.election_date::text
    from essentials.discovered_sources d
    left join essentials.races r on r.id = d.race_id
    left join essentials.elections e on e.id = r.election_id
"""


@dataclass
class DiscoveredRow:
    id: str
    url: str
    title: Optional[str]
    description_snippet: Optional[str]
    channel_name: Optional[str]
    channel_id: Optional[str]
    channel_url: Optional[str]
    outlet_id: Optional[str]
    duration_seconds: Optional[int]
    published_at: Optional[str]
    race_id: Optional[str]
    event_kind_guess: Optional[str]
    source_tier_guess: Optional[int]
    route: str
    confidence: Optional[float]
    why: Optional[str]
    discovered_via: str
    status: str
    election_date: Optional[str] = None
    race_label: Optional[str] = None  # filled by the route via races.race_labels

    @property
    def thumb_url(self) -> Optional[str]:
        m = _YT_ID.search(self.url or "")
        return f"https://i.ytimg.com/vi/{m.group(1)}/mqdefault.jpg" if m else None

    @property
    def duration_label(self) -> str:
        if not self.duration_seconds:
            return "?"
        minutes = round(self.duration_seconds / 60)
        if minutes >= 60:
            return f"{minutes // 60}h{minutes % 60:02d}m".replace("h00m", "h")
        return f"{minutes}m"

    @property
    def confidence_label(self) -> str:
        return f"{self.confidence:.2f}" if self.confidence is not None else "—"

    @property
    def safe_url(self) -> Optional[str]:
        """self.url, but only when it's an http(s) link — never render an
        unvetted scheme (javascript:, data:, ...) as an href."""
        u = (self.url or "").strip()
        return u if u.startswith(("http://", "https://")) else None


def _to_row(r) -> DiscoveredRow:
    return DiscoveredRow(*r)


def pending_rows() -> list:
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT + """
                    where d.status = 'pending'
                    order by e.election_date asc nulls last,
                             d.confidence desc nulls last, d.created_at desc
                """)
                return [_to_row(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def get_row(row_id: str) -> Optional[DiscoveredRow]:
    url = _db_url()
    if not url:
        return None
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT + " where d.id = %s::uuid", (row_id,))
                r = cur.fetchone()
                return _to_row(r) if r else None
        finally:
            conn.close()
    except Exception:
        return None


def set_status(row_id: str, status: str, reason: "str | None" = None) -> bool:
    url = _db_url()
    if not url:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.discovered_sources
                    set status = %s, status_reason = %s, reviewed_at = now()
                    where id = %s::uuid
                """, (status, reason, row_id))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def health() -> dict:
    empty = {"alarms": [], "stale_outlets": [], "pending_total": 0,
             "last_run": None, "scheduled_run_overdue": False}
    url = _db_url()
    if not url:
        return empty
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                from src.discovery.db import alarm_races
                alarms = alarm_races(cur, days=30)
                cur.execute("""
                    select name from essentials.source_outlets
                    where active and (last_polled_at is null
                                      or last_polled_at < now() - interval '48 hours')
                    order by name
                """)
                stale = [r[0] for r in cur.fetchall()]
                cur.execute("select count(*) from essentials.discovered_sources "
                            "where status = 'pending'")
                total = cur.fetchone()[0]
                cur.execute("""
                    select to_char(started_at, 'YYYY-MM-DD HH24:MI:SS'),
                           to_char(finished_at, 'YYYY-MM-DD HH24:MI:SS'),
                           trigger_kind, items_examined, classified,
                           inserted_pending, spend_capped, failure_count,
                           (finished_at is null
                            and started_at > now() - interval '2 hours') as running
                    from essentials.source_discovery_runs
                    order by started_at desc limit 1
                """)
                r = cur.fetchone()
                last_run = None
                if r:
                    last_run = {"started_at": r[0], "finished_at": r[1],
                                "trigger": r[2], "examined": r[3], "classified": r[4],
                                "queued": r[5], "capped": r[6], "failures": r[7],
                                "running": bool(r[8])}
                cur.execute("""
                    select not exists (
                        select 1 from essentials.source_discovery_runs
                        where trigger_kind = 'scheduled'
                          and finished_at > now() - interval '36 hours')
                """)
                overdue = bool(cur.fetchone()[0])
            return {"alarms": alarms, "stale_outlets": stale, "pending_total": total,
                    "last_run": last_run, "scheduled_run_overdue": overdue}
        finally:
            conn.close()
    except Exception:
        return empty


def race_slug_for(race_id: "str | None") -> str:
    """Slug for RunParams.race_slug (feeds the meeting id derivation)."""
    if not race_id:
        return ""
    url = _db_url()
    if not url:
        return ""
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select r.position_name, e.state, r.primary_party, e.election_type
                    from essentials.races r
                    left join essentials.elections e on e.id = r.election_id
                    where r.id = %s::uuid
                """, (race_id,))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    if not row:
        return ""
    from gui.races import race_slug
    return race_slug(row[0], row[1], row[2], row[3])


def watch_channel(row: DiscoveredRow) -> "tuple[bool, str]":
    """Flywheel: insert the row's channel as an active outlet, reviving it
    if a prior (now-deactivated) row already claims this feed_url."""
    if not row.channel_id:
        return False, "no channel id on this item"
    url = _db_url()
    if not url:
        return False, "no DATABASE_URL"
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={row.channel_id}"
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    insert into essentials.source_outlets
                      (name, kind, feed_url, external_channel_id, added_via)
                    values (%s, 'youtube_channel', %s, %s, 'flywheel')
                    on conflict (feed_url) do update set active = true, updated_at = now()
                    returning id
                """, (row.channel_name or row.channel_id, feed_url, row.channel_id))
                cur.fetchone()
            conn.commit()
            return True, "watching " + (row.channel_name or row.channel_id)
        finally:
            conn.close()
    except Exception:
        return False, "failed to add outlet (db error)"

"""Cursor-bound DB helpers for the discovery engine (essentials schema).

Engine-side policy: DATABASE_URL is required (raise), unlike the GUI's
best-effort variants. All functions take a cur so they compose in one
transaction; the engine owns connect/commit.
"""
from __future__ import annotations

import os

import psycopg2

from src.discovery.models import Outlet, TrackedCandidate


def _require_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "Discovery requires DATABASE_URL (add it to .env.local; use the "
            "IPv4 pooler host).")
    return url


def connect():
    return psycopg2.connect(_require_db_url(), sslmode="require")


def fetch_active_outlets(cur) -> list:
    cur.execute(
        "select id::text, name, kind, feed_url, external_channel_id "
        "from essentials.source_outlets where active order by name")
    return [Outlet(id=r[0], name=r[1], kind=r[2], feed_url=r[3],
                   external_channel_id=r[4]) for r in cur.fetchall()]


def fetch_tracked_candidates(cur) -> list:
    cur.execute("""
        select rc.politician_id::text, rc.race_id::text, rc.full_name,
               p.race_label, p.election_date::text
        from essentials.race_candidates rc
        join essentials.readrank_race_pipeline p on p.race_id = rc.race_id
        where p.status in ('needs_quotes','quotes_staged','published')
          and p.election_date >= current_date
          and coalesce(rc.candidate_status, 'active') not in ('withdrawn','removed')
          and rc.full_name is not null
        order by rc.race_id, rc.full_name
    """)
    return [TrackedCandidate(politician_id=r[0], race_id=r[1], full_name=r[2],
                             race_label=r[3], election_date=r[4])
            for r in cur.fetchall()]


def fetch_sweep_state(cur) -> dict:
    cur.execute("select race_id::text, last_swept_at "
                "from essentials.discovery_race_state")
    return {r[0]: r[1] for r in cur.fetchall()}


def existing_source_keys(cur) -> set:
    cur.execute("select source_key from essentials.discovered_sources")
    return {r[0] for r in cur.fetchall()}


def insert_discovered(cur, row: dict) -> bool:
    """Idempotent on source_key. Returns True when a row was inserted."""
    cur.execute("""
        insert into essentials.discovered_sources
          (source_key, url, title, description_snippet, channel_name, channel_id,
           channel_url, outlet_id, duration_seconds, published_at,
           matched_politician_ids, race_id, event_kind_guess, source_tier_guess,
           route, confidence, why, discovered_via, status)
        values (%s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, %s,
                %s::uuid[], %s::uuid, %s, %s, %s, %s, %s, %s, %s)
        on conflict (source_key) do nothing
        returning id
    """, (
        row["source_key"], row["url"], row["title"], row["description_snippet"],
        row["channel_name"], row["channel_id"], row["channel_url"], row["outlet_id"],
        row["duration_seconds"], row["published_at"],
        row["matched_politician_ids"], row["race_id"], row["event_kind_guess"],
        row["source_tier_guess"], row["route"], row["confidence"], row["why"],
        row["discovered_via"], row["status"],
    ))
    return cur.fetchone() is not None


def mark_outlet_polled(cur, outlet_id: str) -> None:
    cur.execute("update essentials.source_outlets "
                "set last_polled_at = now(), updated_at = now() "
                "where id = %s::uuid", (outlet_id,))


def record_sweep(cur, race_id: str) -> None:
    cur.execute("""
        insert into essentials.discovery_race_state (race_id, last_swept_at)
        values (%s::uuid, now())
        on conflict (race_id) do update set last_swept_at = now()
    """, (race_id,))


def alarm_races(cur, days: int = 30) -> list:
    """Races inside the deadline window, still sourcing, with zero approved
    items on either route. Returns [(race_id, race_label, election_date)]."""
    cur.execute("""
        select p.race_id::text, p.race_label, p.election_date::text
        from essentials.readrank_race_pipeline p
        where p.race_id is not null and p.status = 'needs_quotes'
          and p.election_date between current_date
              and current_date + make_interval(days => %s)
          and not exists (
              select 1 from essentials.discovered_sources d
              where d.race_id = p.race_id and d.status in ('approved','ingested'))
        order by p.election_date
    """, (days,))
    return cur.fetchall()

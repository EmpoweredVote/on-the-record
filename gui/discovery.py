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

# Spec Q4's zero-tolerance set for mode C. Deliberately narrower than the eval
# harvest's GOLD_FALSE_REASONS (which adds tier-5): tier-5 already drags the
# approve rate; identity errors are the misattribution class.
IDENTITY_REJECT_REASONS = ("wrong-person", "clip-not-original")

_SELECT = """
    select d.id::text, d.url, d.title, d.description_snippet, d.channel_name,
           d.channel_id, d.channel_url, d.outlet_id::text, d.duration_seconds,
           d.published_at::text, d.race_id::text, d.event_kind_guess,
           d.source_tier_guess, d.route, d.confidence, d.why, d.discovered_via,
           d.status, e.election_date::text,
           coalesce(o.trusted, false), coalesce(o.ingest_barred, false),
           d.original_vs_clip
    from essentials.discovered_sources d
    left join essentials.races r on r.id = d.race_id
    left join essentials.elections e on e.id = r.election_id
    left join essentials.source_outlets o on o.id = d.outlet_id
"""

_LIST_WHERE_ORDER = """
    where d.status = %s
    order by e.election_date asc nulls last,
             d.source_tier_guess asc nulls last,
             d.confidence desc nulls last, d.created_at desc
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
    # The next three are positional-mapped from _SELECT's three trailing
    # columns (see _to_row) — they MUST stay here, immediately after
    # election_date and before race_label/family_count below. Those two are
    # never supplied by _SELECT (filled later by callers), so this block must
    # stay past the last column _SELECT actually returns or DiscoveredRow(*r)
    # misaligns silently (e.g. o.trusted landing in race_label instead of
    # outlet_trusted).
    outlet_trusted: bool = False
    outlet_ingest_barred: bool = False
    original_vs_clip: Optional[str] = None  # 'original' | 'clip' | None (Task 5b)
    race_label: Optional[str] = None  # filled by the route via races.race_labels
    family_count: int = 0  # other pending rows sharing this row's source key (page render)

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


def family_key(row: "DiscoveredRow") -> "tuple[str, str] | None":
    """A row's source identity, by precedence: registered outlet, else
    YouTube channel, else the channel name (trimmed + lowercased). Two rows
    are the same source when this returns the same pair. A row with none of
    the three has no family."""
    if row.outlet_id:
        return ("outlet", row.outlet_id)
    if row.channel_id:
        return ("channel", row.channel_id)
    name = (row.channel_name or "").strip().lower()
    if name:
        return ("name", name)
    return None


def pending_rows(status: str = "pending") -> list:
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT + _LIST_WHERE_ORDER, (status,))
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


def set_status_bulk(row_ids: "list[str]", status: str, reason: "str | None" = None) -> int:
    """Set status on many rows at once. Only rows currently pending or deferred
    are touched, so a bulk action can never un-ingest or un-approve. Returns the
    number of rows changed. Empty id list is a no-op."""
    if not row_ids:
        return 0
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.discovered_sources
                    set status = %s, status_reason = %s, reviewed_at = now()
                    where id = any(%s::uuid[])
                      and status = any(array['pending','deferred'])
                """, (status, reason, row_ids))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0


def _family_where(row: "DiscoveredRow") -> "tuple[str, str]":
    """The (where_clause, value) selecting a row's source family, by the same
    precedence as family_key. The clause is drawn only from the hardcoded match
    map or the literal id fallback — never from row data — so it carries no
    injection surface; the value is always bound as a parameter by callers."""
    key = family_key(row)
    match = {
        "outlet": "outlet_id = %s::uuid",
        "channel": "channel_id = %s",
        "name": "lower(btrim(channel_name)) = %s",
    }
    if key is None:
        return "id = %s::uuid", row.id
    return match[key[0]], key[1]


def approve_source_family(row: "DiscoveredRow") -> int:
    """Approve, as a quote source, every pending row that shares this row's
    source key (see family_key). Whole-queue scope, all races. Only 'pending'
    rows are touched, so this can never un-ingest or re-approve. A keyless row
    approves only itself. Returns the number of rows changed, 0 on failure."""
    where, val = _family_where(row)
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    update essentials.discovered_sources
                    set status = 'approved', status_reason = null, reviewed_at = now()
                    where status = 'pending' and {where}
                """, (val,))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0


def reject_source_family(row: "DiscoveredRow", reason: "str | None") -> int:
    """Reject every pending row that shares this row's source key (family_key),
    all with one reason. Whole-queue scope, all races. Only 'pending' rows are
    touched. A keyless row rejects only itself. Returns rows changed, 0 on
    failure."""
    where, val = _family_where(row)
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    update essentials.discovered_sources
                    set status = 'rejected', status_reason = %s, reviewed_at = now()
                    where status = 'pending' and {where}
                """, (reason, val))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0


def health() -> dict:
    empty = {"alarms": [], "stale_outlets": [], "pending_total": 0,
             "last_run": None, "scheduled_run_overdue": False,
             "outlet_stats": [], "outletless_reviewed": 0,
             "auto_kept_week": 0, "auto_kept_outlets": 0}
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
                    select started_at, finished_at,
                           trigger_kind, items_examined, classified,
                           inserted_pending, spend_capped, failure_count,
                           skipped_seen, prefiltered_out, recency_filtered,
                           (finished_at is null
                            and started_at > now() - interval '2 hours') as running
                    from essentials.source_discovery_runs
                    order by started_at desc limit 1
                """)
                r = cur.fetchone()
                last_run = None
                if r:
                    def _local(ts):
                        return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S") if ts else None
                    last_run = {"started_at": _local(r[0]), "finished_at": _local(r[1]),
                                "trigger": r[2], "examined": r[3], "classified": r[4],
                                "queued": r[5], "capped": r[6], "failures": r[7],
                                "skipped": r[8], "prefiltered": r[9], "recency": r[10],
                                "running": bool(r[11])}
                cur.execute("""
                    select not exists (
                        select 1 from essentials.source_discovery_runs
                        where trigger_kind = 'scheduled'
                          and finished_at > now() - interval '36 hours')
                """)
                overdue = bool(cur.fetchone()[0])
                # Folded in from outlet_stats() to avoid a 4th connection per
                # page load (discovery_page rebuilds ~115x via redirects) —
                # identical aggregate, riding this cursor instead.
                cur.execute("""
                    select o.name,
                           count(*) filter (where d.status in
                               ('approved','ingested','rejected')) as reviewed,
                           count(*) filter (where d.status in
                               ('approved','ingested')) as approved,
                           count(*) filter (where d.status = 'rejected'
                               and d.status_reason = any(%s)) as identity_rejects
                    from essentials.source_outlets o
                    join essentials.discovered_sources d on d.outlet_id = o.id
                    group by o.name
                    having count(*) filter (where d.status in
                        ('approved','ingested','rejected')) > 0
                    order by 2 desc, o.name
                """, (list(IDENTITY_REJECT_REASONS),))
                ostats = [{"name": r[0], "reviewed": r[1], "approved": r[2],
                           "identity_rejects": r[3]} for r in cur.fetchall()]
                cur.execute("""
                    select count(*) from essentials.discovered_sources
                    where outlet_id is null
                      and status in ('approved','ingested','rejected')
                """)
                outletless = cur.fetchone()[0]
                # Task 6: how much the auto-approve sweep (poll_discovery,
                # trust_from_row) has kept out of the human queue lately.
                cur.execute("""
                    select count(*), count(distinct outlet_id)
                    from essentials.discovered_sources
                    where status = 'approved' and status_reason like 'auto:%%'
                      and reviewed_at > now() - interval '7 days'
                """)
                auto_kept_week, auto_kept_outlets = cur.fetchone()
            return {"alarms": alarms, "stale_outlets": stale, "pending_total": total,
                    "last_run": last_run, "scheduled_run_overdue": overdue,
                    "outlet_stats": ostats, "outletless_reviewed": outletless,
                    "auto_kept_week": auto_kept_week, "auto_kept_outlets": auto_kept_outlets}
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


def outlet_stats() -> list:
    """Per-outlet triage evidence toward the future mode-C flag-flip.
    Qualification bar (spec, Q4): >=10 reviewed, >=90% approved, zero
    identity-class rejects. Display-only — no auto-ingest path exists."""
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select o.name,
                           count(*) filter (where d.status in
                               ('approved','ingested','rejected')) as reviewed,
                           count(*) filter (where d.status in
                               ('approved','ingested')) as approved,
                           count(*) filter (where d.status = 'rejected'
                               and d.status_reason = any(%s)) as identity_rejects
                    from essentials.source_outlets o
                    join essentials.discovered_sources d on d.outlet_id = o.id
                    group by o.name
                    having count(*) filter (where d.status in
                        ('approved','ingested','rejected')) > 0
                    order by 2 desc, o.name
                """, (list(IDENTITY_REJECT_REASONS),))
                return [{"name": r[0], "reviewed": r[1], "approved": r[2],
                         "identity_rejects": r[3]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


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


def _outlet_id_for_channel(channel_id: "str | None") -> Optional[str]:
    """Best-effort: the id of the outlet registered for a YouTube channel, or
    None if there isn't one (or there's no DB). Used right after watch_channel
    upserts an outlet, to recover its id for the trust+sweep that follows."""
    if not channel_id:
        return None
    url = _db_url()
    if not url:
        return None
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select id::text from essentials.source_outlets
                    where external_channel_id = %s
                """, (channel_id,))
                r = cur.fetchone()
                return r[0] if r else None
        finally:
            conn.close()
    except Exception:
        return None


def set_outlet_trusted(outlet_id: str) -> bool:
    url = _db_url()
    if not url:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.source_outlets
                    set trusted = true, trusted_at = now(), updated_at = now()
                    where id = %s::uuid
                """, (outlet_id,))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def trust_from_row(row: "DiscoveredRow") -> "tuple[bool, str, int]":
    """Trust the row's outlet — registering it first (via watch_channel) if
    the row is channel-only and has no outlet yet — then sweep its pending
    news clips into approved quote sources. Returns (ok, message, n_auto),
    best-effort: any DB failure along the way returns (False, ..., 0) rather
    than raising."""
    from src.discovery.autoapprove import auto_approve_pending

    outlet_id = row.outlet_id
    if not outlet_id:
        ok, _ = watch_channel(row)  # upserts/revives an outlet for the channel
        if not ok:
            return False, "could not register outlet", 0
        outlet_id = _outlet_id_for_channel(row.channel_id)
        if not outlet_id:
            return False, "outlet not found after register", 0
    if not set_outlet_trusted(outlet_id):
        return False, "failed to set trusted", 0
    url = _db_url()
    n = 0
    if url:
        try:
            conn = psycopg2.connect(url)
            try:
                with conn.cursor() as cur:
                    n = auto_approve_pending(cur, outlet_id)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            n = 0
    return True, f"trusted {row.channel_name or 'outlet'}", n


def unapprove_auto(row_ids: "list[str]") -> int:
    """Undo: return auto-approved rows to pending. Restricted to rows whose
    status_reason still starts with 'auto:' — a since-reviewed or
    human-approved row is never touched, even if its id is passed in."""
    if not row_ids:
        return 0
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.discovered_sources
                    set status = 'pending', status_reason = null, reviewed_at = null
                    where id = any(%s::uuid[])
                      and status = 'approved' and status_reason like 'auto:%%'
                """, (row_ids,))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0


def probe_extractable(url: str) -> "tuple[bool, str]":
    """Can yt-dlp actually get a video out of this page? Metadata-only, no
    download. Gate for approve->ingest on non-YouTube items so unextractable
    embeds bounce to Edit-first instead of poisoning the batch pool.

    Resolver-owned URLs (podcast RSS episode pages, Brightspot/NPR pages) and
    raw HLS manifests ingest fine without yt-dlp at all (see src/resolve.py's
    resolve_source, src/download.py's is_hls_url) — bouncing those here would
    be a false-positive regression, so they short-circuit True before yt-dlp
    is even asked."""
    from src.download import is_hls_url

    if is_hls_url(url):
        return True, ""
    try:
        from src.resolve import resolve_source

        def _quick_fetch(u: str) -> str:
            import requests

            r = requests.get(u, timeout=(5, 10), headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.text

        if resolve_source(url, fetch=_quick_fetch) is not None:
            return True, ""
    except Exception:
        pass  # resolver errors fall through to the yt-dlp probe below

    # A few human-triggered metadata fetches (resolver peek + one bounded
    # yt-dlp extract) — deliberately outside the watchlist lane's
    # robots/pacing (see feeds.py); the human just previewed this page.
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "js_runtimes": {"node": {}}, "socket_timeout": 15,
                "no_color": True, "playlist_items": "1"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — any extractor error = not extractable
        return False, str(exc)[:200]
    if not info:
        return False, "no media found"
    if info.get("entries") is not None and not [e for e in info["entries"] if e]:
        return False, "page has no extractable video"
    return True, ""

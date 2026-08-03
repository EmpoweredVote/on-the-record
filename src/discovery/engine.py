"""Discovery run orchestration.

All I/O is injected (provider, feed fetcher, search, hydration, captions,
sleep) so the whole run is testable with fakes. The engine owns commits:
one per outlet and one per swept race, so a crash loses at most one unit.
Log lines follow the poll_agendas convention: UPPERCASE verb prefixes.
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import Counter
from dataclasses import dataclass, field

from src import config
from src.discovery import db
from src.discovery.classify import classify_item
from src.discovery.prefilter import normalize, prefilter_item
from src.discovery.search import queries_for_candidate
from src.source_key import source_key


@dataclass
class RunStats:
    examined: int = 0
    skipped_seen: int = 0
    prefiltered_out: int = 0
    classified: int = 0
    inserted_pending: int = 0
    inserted_auto_filtered: int = 0
    spend_capped: int = 0
    failures: list = field(default_factory=list)


def sweep_interval_days(days_to_election: int) -> int:
    if days_to_election > 60:
        return 7
    if days_to_election > 30:
        return 3
    return 2


def sweep_due(election_date: "str | None", last_swept_at, today: dt.date) -> bool:
    if not election_date:
        return False
    election = dt.date.fromisoformat(election_date[:10])
    days_to = (election - today).days
    if days_to < 0:
        return False
    if last_swept_at is None:
        return True
    last = last_swept_at.date() if hasattr(last_swept_at, "date") else last_swept_at
    return (today - last).days >= sweep_interval_days(days_to)


def _snippet(text: "str | None", limit: int = 500) -> "str | None":
    if not text:
        return None
    return text[:limit]


def run_discovery(conn, *, provider, fetch_feed_items, ytsearch_fn, hydrate_fn,
                  captions_fetcher, sleep_fn, meeting_keys: set, today: dt.date,
                  dry_run: bool = False, race_filter: "str | None" = None,
                  classify_cap: "int | None" = None,
                  skip_watchlist: bool = False, skip_sweeps: bool = False) -> RunStats:
    stats = RunStats()
    cur = conn.cursor()
    tracked = db.fetch_tracked_candidates(cur)
    by_race: dict = {}
    by_norm_name: dict = {}
    for t in tracked:
        by_race.setdefault(t.race_id, []).append(t)
        by_norm_name.setdefault(normalize(t.full_name), []).append(t)
    all_names = sorted({t.full_name for t in tracked})
    seen = db.existing_source_keys(cur) | set(meeting_keys)
    cap = classify_cap if classify_cap is not None else config.DISCOVERY_CLASSIFY_CAP_PER_RUN

    def process(item, roster_names, race_hint):
        key = source_key(item.url)
        if not key or key in seen:
            stats.skipped_seen += 1
            return
        stats.examined += 1
        pf = prefilter_item(item.title, item.description, item.duration_seconds,
                            roster_names)
        if not pf.passed:
            stats.prefiltered_out += 1
            return
        if item.duration_seconds is None or item.description is None:
            item = hydrate_fn(item)
            pf = prefilter_item(item.title, item.description, item.duration_seconds,
                                roster_names)
            if not pf.passed:
                stats.prefiltered_out += 1
                return
        matched = [t for name in pf.matched_names
                   for t in by_norm_name.get(normalize(name), [])]
        if race_hint:
            in_race = [t for t in matched if t.race_id == race_hint]
            matched = in_race or matched
        if not matched:
            stats.prefiltered_out += 1
            return
        race_id = race_hint or Counter(t.race_id for t in matched).most_common(1)[0][0]
        race_cands = by_race.get(race_id, [])
        race_label = race_cands[0].race_label if race_cands else "(unknown race)"
        roster = [t.full_name for t in race_cands] or pf.matched_names
        if dry_run:
            print(f"DRY-RUN candidate [{item.via}] {item.title!r} "
                  f"({item.channel_name}, {item.duration_seconds}s) -> {race_label}")
            seen.add(key)
            return
        if stats.classified >= cap:
            if stats.spend_capped == 0:
                print(f"SPEND CAP reached ({cap} classifications); "
                      "remaining items left for the next run")
            stats.spend_capped += 1
            return
        verdict = classify_item(provider, item, race_label=race_label,
                                roster_names=roster, captions_fetcher=captions_fetcher)
        stats.classified += 1
        pending = (verdict.rejected_reason is None and verdict.relevant
                   and verdict.confidence >= config.DISCOVERY_CONFIDENCE_FLOOR)
        status = "pending" if pending else "auto_filtered"
        matched_ids = sorted({t.politician_id for t in matched
                              if t.race_id == race_id})
        db.insert_discovered(cur, {
            "source_key": key, "url": item.url, "title": item.title,
            "description_snippet": _snippet(item.description),
            "channel_name": item.channel_name, "channel_id": item.channel_id,
            "channel_url": item.channel_url, "outlet_id": item.outlet_id,
            "duration_seconds": item.duration_seconds,
            "published_at": item.published_at,
            "matched_politician_ids": matched_ids, "race_id": race_id,
            "event_kind_guess": verdict.event_kind_guess,
            "source_tier_guess": verdict.source_tier_guess,
            "route": verdict.route, "confidence": verdict.confidence,
            "why": verdict.why or verdict.rejected_reason,
            "discovered_via": item.via, "status": status,
        })
        seen.add(key)
        if status == "pending":
            stats.inserted_pending += 1
            print(f"QUEUED [{item.via}] {item.title!r} -> {race_label} "
                  f"({verdict.confidence:.2f})")
        else:
            stats.inserted_auto_filtered += 1

    if not skip_watchlist:
        for outlet in db.fetch_active_outlets(cur):
            try:
                items = fetch_feed_items(outlet)
            except Exception as exc:  # noqa: BLE001 — per-outlet, loud, non-fatal
                stats.failures.append(f"outlet {outlet.name}: {exc}")
                print(f"FAILED outlet {outlet.name}: {exc}", file=sys.stderr)
                continue
            for item in items:
                process(item, all_names, None)
            if not dry_run:
                db.mark_outlet_polled(cur, outlet.id)
                conn.commit()

    if not skip_sweeps:
        state = db.fetch_sweep_state(cur)
        for race_id, cands in by_race.items():
            if race_filter and race_id != race_filter:
                continue
            if not race_filter and not sweep_due(cands[0].election_date,
                                                 state.get(race_id), today):
                continue
            for cand in cands:
                for query in queries_for_candidate(cand.full_name):
                    try:
                        results = ytsearch_fn(query)
                    except Exception as exc:  # noqa: BLE001
                        stats.failures.append(f"search {query!r}: {exc}")
                        print(f"FAILED search {query!r}: {exc}", file=sys.stderr)
                        continue
                    for item in results:
                        process(item, [c.full_name for c in cands], race_id)
                    sleep_fn(config.DISCOVERY_SEARCH_SLEEP_SECONDS)
            if not dry_run:
                db.record_sweep(cur, race_id)
                conn.commit()

    return stats

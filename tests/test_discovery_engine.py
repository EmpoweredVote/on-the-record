import datetime as dt

from src import config
from src.discovery import db, engine
from src.discovery.models import Outlet, RawItem, TrackedCandidate, Verdict


class _FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return object()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


TRACKED = [
    TrackedCandidate("p1", "r1", "Maria Delgado", "TX Senate", "2026-11-03"),
    TrackedCandidate("p2", "r1", "Ana Ruiz", "TX Senate", "2026-11-03"),
]
OUTLET = Outlet(id="o1", name="KXAN", kind="youtube_channel", feed_url="https://f")
GOOD_ITEM = RawItem(url="https://www.youtube.com/watch?v=abc12345678",
                    title="Maria Delgado and Ana Ruiz: full debate",
                    description="d", channel_name="KXAN", channel_id="UCk",
                    duration_seconds=3300, published_at="2026-08-01",
                    outlet_id="o1", via="watchlist")
NOISE_ITEM = RawItem(url="https://www.youtube.com/watch?v=zzz12345678",
                     title="Morning weather", description="", channel_name="KXAN",
                     duration_seconds=120, outlet_id="o1", via="watchlist")


def _patch_db(monkeypatch, inserted):
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: None)


class _FakeProvider:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt, *, max_tokens, temperature, system=None):
        self.calls += 1
        return self.reply


def _run(monkeypatch, inserted, **kwargs):
    provider = kwargs.pop("provider", _FakeProvider(
        '{"relevant": true, "confidence": 0.9, "candidates_present": ["Maria Delgado"],'
        ' "event_kind": "debate", "source_tier": 1, "original_vs_clip": "original",'
        ' "route": "ingest", "why": "long full debate"}'))
    _patch_db(monkeypatch, inserted)
    stats = engine.run_discovery(
        _FakeConn(), provider=provider,
        fetch_feed_items=kwargs.pop("fetch_feed_items", lambda o: [GOOD_ITEM, NOISE_ITEM]),
        ytsearch_fn=kwargs.pop("ytsearch_fn", lambda q: []),
        hydrate_fn=kwargs.pop("hydrate_fn", lambda item: item),
        peek_fetcher=None, sleep_fn=lambda s: None,
        meeting_keys=kwargs.pop("meeting_keys", set()),
        today=dt.date(2026, 8, 2), **kwargs)
    return stats, provider


def test_watchlist_flow_inserts_pending_row(monkeypatch):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True)
    assert provider.calls == 1              # noise item died in prefilter, free
    assert len(inserted) == 1
    row = inserted[0]
    assert row["status"] == "pending" and row["race_id"] == "r1"
    assert set(row["matched_politician_ids"]) == {"p1", "p2"}
    assert row["source_key"] == "youtube:abc12345678"
    assert stats.inserted_pending == 1 and stats.prefiltered_out == 1


def test_already_seen_sources_are_skipped_before_classify(monkeypatch):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True,
                           meeting_keys={"youtube:abc12345678"})
    assert provider.calls == 0 and inserted == [] and stats.skipped_seen == 1


def test_low_confidence_stored_as_auto_filtered(monkeypatch):
    inserted = []
    stats, _ = _run(monkeypatch, inserted, skip_sweeps=True, provider=_FakeProvider(
        '{"relevant": false, "confidence": 0.1, "why": "news package"}'))
    assert inserted[0]["status"] == "auto_filtered"
    assert stats.inserted_auto_filtered == 1


def test_spend_cap_stops_classification_loudly(monkeypatch, capsys):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True, classify_cap=0)
    assert provider.calls == 0 and inserted == [] and stats.spend_capped == 1
    assert "SPEND CAP" in capsys.readouterr().out


def test_dry_run_skips_llm_and_writes(monkeypatch, capsys):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_sweeps=True, dry_run=True)
    assert provider.calls == 0 and inserted == []
    assert "DRY-RUN" in capsys.readouterr().out


def test_outlet_failure_is_nonfatal(monkeypatch):
    inserted = []

    def boom(outlet):
        raise RuntimeError("feed 500")

    stats, _ = _run(monkeypatch, inserted, skip_sweeps=True, fetch_feed_items=boom)
    assert stats.failures and inserted == []


def test_sweep_queries_each_candidate_and_records(monkeypatch):
    inserted = []
    recorded = []
    queries = []
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: recorded.append(rid))

    def fake_search(q):
        queries.append(q)
        return [GOOD_ITEM]

    provider = _FakeProvider(
        '{"relevant": true, "confidence": 0.9, "candidates_present": ["Maria Delgado"],'
        ' "event_kind": "debate", "source_tier": 1, "original_vs_clip": "original",'
        ' "route": "ingest", "why": "long full debate"}')
    stats = engine.run_discovery(
        _FakeConn(), provider=provider,
        fetch_feed_items=lambda o: [], ytsearch_fn=fake_search,
        hydrate_fn=lambda item: item, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True)

    assert len(queries) == 8               # 2 candidates x 4 terms
    assert provider.calls == 1             # dedup: same item after first insert
    assert stats.inserted_pending == 1
    assert recorded == ["r1"]              # no errors this race -> cadence resets


def test_sweep_search_error_skips_record_but_commits(monkeypatch):
    inserted = []
    recorded = []
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: recorded.append(rid))

    def raising_search(q):
        raise RuntimeError("bot check")

    conn = _FakeConn()
    stats = engine.run_discovery(
        conn, provider=_FakeProvider("irrelevant"),
        fetch_feed_items=lambda o: [], ytsearch_fn=raising_search,
        hydrate_fn=lambda item: item, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True)

    assert stats.failures                  # loud -- not silently swallowed to 0 results
    assert recorded == []                  # a bot-check wave must not reset the cadence clock
    assert conn.commits >= 1               # commit stays unconditional
    assert inserted == []


def test_sweep_interval_days_bands():
    assert engine.sweep_interval_days(90) == 7
    assert engine.sweep_interval_days(45) == 3
    assert engine.sweep_interval_days(10) == 2


def test_sweep_due_respects_last_swept(monkeypatch):
    today = dt.date(2026, 8, 2)
    assert engine.sweep_due("2026-11-03", None, today) is True
    recent = dt.datetime(2026, 8, 1, 9, 0)
    assert engine.sweep_due("2026-11-03", recent, today) is False  # weekly band
    assert engine.sweep_due("2026-08-20", recent, today) is False  # 2-3 day band, 1 day ago
    old = dt.datetime(2026, 7, 20, 9, 0)
    assert engine.sweep_due("2026-11-03", old, today) is True
    assert engine.sweep_due("2026-07-01", old, today) is False     # election passed


# --- Post-review hardening: degraded-path fixes -----------------------------

def test_item_failure_is_nonfatal_and_run_continues(monkeypatch):
    inserted = []
    _patch_db(monkeypatch, inserted)
    second_item = RawItem(url="https://www.youtube.com/watch?v=def12345678",
                          title="Maria Delgado town hall on the issues",
                          description="d", channel_name="KXAN",
                          duration_seconds=1800, published_at="2026-08-01",
                          outlet_id="o1", via="watchlist")

    class _RaisesOnceProvider:
        def __init__(self, reply):
            self.reply = reply
            self.calls = 0

        def complete(self, prompt, *, max_tokens, temperature, system=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("classifier API error")
            return self.reply

    provider = _RaisesOnceProvider(
        '{"relevant": true, "confidence": 0.9, "candidates_present": ["Maria Delgado"],'
        ' "event_kind": "town_hall", "source_tier": 1, "original_vs_clip": "original",'
        ' "route": "ingest", "why": "town hall"}')
    conn = _FakeConn()
    stats = engine.run_discovery(
        conn, provider=provider,
        fetch_feed_items=lambda o: [GOOD_ITEM, second_item],
        ytsearch_fn=lambda q: [], hydrate_fn=lambda item: item,
        peek_fetcher=None, sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_sweeps=True)

    assert provider.calls == 2
    assert len(inserted) == 1
    assert inserted[0]["url"] == second_item.url
    assert len(stats.failures) == 1
    assert GOOD_ITEM.url in stats.failures[0]
    # RuntimeError from the classifier is not a DB error -> no rollback,
    # so any already-inserted rows in this outlet's transaction survive.
    assert conn.rollbacks == 0


def test_sweep_item_failure_is_nonfatal(monkeypatch):
    inserted = []
    _patch_db(monkeypatch, inserted)

    class _AlwaysRaisesProvider:
        def complete(self, prompt, *, max_tokens, temperature, system=None):
            raise RuntimeError("classifier API error")

    item = RawItem(url="https://www.youtube.com/watch?v=sw112345678",
                   title="Maria Delgado town hall on the issues", description="d",
                   channel_name="KXAN", duration_seconds=1800,
                   published_at="2026-08-01", via="search")
    hits = {"count": 0}

    def fake_search(q):
        hits["count"] += 1
        return [item] if hits["count"] == 1 else []

    stats = engine.run_discovery(
        _FakeConn(), provider=_AlwaysRaisesProvider(),
        fetch_feed_items=lambda o: [], ytsearch_fn=fake_search,
        hydrate_fn=lambda it: it, peek_fetcher=None, sleep_fn=lambda s: None,
        meeting_keys=set(), today=dt.date(2026, 8, 2), skip_watchlist=True)

    assert len(stats.failures) == 1
    assert inserted == []


def test_spend_cap_mid_race_skips_record_but_commits(monkeypatch):
    inserted = []
    recorded = []
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: recorded.append(rid))

    item_a = RawItem(url="https://www.youtube.com/watch?v=aaa12345678",
                     title="Maria Delgado town hall event", description="d",
                     channel_name="KXAN", duration_seconds=1800,
                     published_at="2026-08-01", via="search")
    item_b = RawItem(url="https://www.youtube.com/watch?v=bbb12345678",
                     title="Ana Ruiz debate coverage", description="d",
                     channel_name="KXAN", duration_seconds=1800,
                     published_at="2026-08-01", via="search")
    hits = {"count": 0}

    def fake_search(q):
        hits["count"] += 1
        if hits["count"] == 1:
            return [item_a]
        if hits["count"] == 2:
            return [item_b]
        return []

    conn = _FakeConn()
    stats = engine.run_discovery(
        conn, provider=_FakeProvider(
            '{"relevant": true, "confidence": 0.9, "candidates_present": [],'
            ' "event_kind": "town_hall", "source_tier": 1, "original_vs_clip": "original",'
            ' "route": "ingest", "why": "town hall"}'),
        fetch_feed_items=lambda o: [], ytsearch_fn=fake_search,
        hydrate_fn=lambda item: item, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True, classify_cap=1)

    # item_a consumes the one allowed classification and is inserted; item_b
    # arrives after the cap is already spent, so it's skipped in-race (no
    # early break, since the cap wasn't exhausted until partway through).
    assert stats.classified == 1
    assert len(inserted) == 1
    assert recorded == []          # cap truncated this race -> don't record it
    assert conn.commits >= 1       # but the already-paid-for row still commits


def test_spend_cap_defers_sweep_and_skips_record(monkeypatch, capsys):
    inserted = []
    recorded = []
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: recorded.append(rid))

    stats = engine.run_discovery(
        _FakeConn(), provider=_FakeProvider("irrelevant"),
        fetch_feed_items=lambda o: [], ytsearch_fn=lambda q: [GOOD_ITEM],
        hydrate_fn=lambda item: item, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True, classify_cap=0)

    assert recorded == []
    assert "SPEND CAP" in capsys.readouterr().out


def test_matched_politician_ids_keep_cross_race_matches(monkeypatch):
    inserted = []
    tracked = TRACKED + [TrackedCandidate("p3", "r2", "Carlos Diaz", "TX AG", "2026-11-03")]
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(tracked))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: None)

    item = RawItem(url="https://www.youtube.com/watch?v=cross1234567",
                   title="Maria Delgado, Ana Ruiz, and Carlos Diaz: full debate",
                   description="d", channel_name="KXAN",
                   duration_seconds=3300, published_at="2026-08-01",
                   outlet_id="o1", via="watchlist")

    stats = engine.run_discovery(
        _FakeConn(), provider=_FakeProvider(
            '{"relevant": true, "confidence": 0.9, "candidates_present": [],'
            ' "event_kind": "debate", "source_tier": 1, "original_vs_clip": "original",'
            ' "route": "ingest", "why": "cross-race debate"}'),
        fetch_feed_items=lambda o: [item], ytsearch_fn=lambda q: [],
        hydrate_fn=lambda it: it, peek_fetcher=None, sleep_fn=lambda s: None,
        meeting_keys=set(), today=dt.date(2026, 8, 2), skip_sweeps=True)

    assert len(inserted) == 1
    row = inserted[0]
    assert set(row["matched_politician_ids"]) == {"p1", "p2", "p3"}
    assert row["race_id"] == "r1"


def test_hydration_is_cached_across_duplicate_sweep_hits(monkeypatch):
    inserted = []
    _patch_db(monkeypatch, inserted)
    calls = []
    hits = {"count": 0}

    def counting_hydrate(item):
        calls.append(item.url)
        return RawItem(url=item.url, title=item.title, description="",
                       channel_name=item.channel_name, duration_seconds=90,
                       published_at=item.published_at, outlet_id=item.outlet_id,
                       via=item.via)

    needs_hydration_item = RawItem(url="https://www.youtube.com/watch?v=hyd1234567",
                                   title="Maria Delgado talks issues", description=None,
                                   channel_name="KXAN", duration_seconds=None,
                                   published_at="2026-08-01", via="search")

    def fake_search(q):
        if hits["count"] < 2:
            hits["count"] += 1
            return [needs_hydration_item]
        return []

    stats = engine.run_discovery(
        _FakeConn(), provider=_FakeProvider("irrelevant"),
        fetch_feed_items=lambda o: [], ytsearch_fn=fake_search,
        hydrate_fn=counting_hydrate, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True)

    assert calls == [needs_hydration_item.url]  # hydrated exactly once, cached on repeat
    assert stats.prefiltered_out == 2
    assert inserted == []


def test_sweep_due_converts_aware_datetime_to_local_date(monkeypatch):
    import os
    import time as time_mod

    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time_mod.tzset()
    try:
        today = dt.date(2026, 8, 2)
        # 2026-08-01 02:00 UTC == 2026-07-31 22:00 EDT (UTC-4); the raw UTC
        # calendar date is 2026-08-01 but the local calendar date is 2026-07-31.
        aware = dt.datetime(2026, 8, 1, 2, 0, tzinfo=dt.timezone.utc)
        # 2026-08-20 is 18 days out from today -> the 2-day sweep band.
        # Local-date reckoning: 2 days since last swept -> due.
        # Raw-UTC-date reckoning (the pre-fix bug): only 1 day since last swept -> not due.
        assert engine.sweep_due("2026-08-20", aware, today) is True
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time_mod.tzset()


def test_unknown_race_filter_is_loud_failure(monkeypatch):
    inserted = []
    stats, provider = _run(monkeypatch, inserted, skip_watchlist=True,
                           race_filter="bogus-race-id")
    assert stats.failures
    assert any("bogus-race-id" in f for f in stats.failures)


def test_stale_watchlist_item_is_recency_filtered(monkeypatch):
    import dataclasses
    inserted = []
    _patch_db(monkeypatch, inserted)
    old = dataclasses.replace(GOOD_ITEM, published_at="2024-01-15")
    stats, provider = _run(monkeypatch, inserted,
                           fetch_feed_items=lambda outlet: [old], skip_sweeps=True)
    assert stats.examined == 1                # name-matched, so it reached the stale check
    assert stats.recency_filtered == 1
    assert inserted == []


def test_sweep_phase_aborts_after_consecutive_search_failures(monkeypatch):
    inserted = []
    recorded = []
    calls = []
    # A second race so the abort must also break the RACE loop, not just the
    # query/candidate loops: r1 alone already supplies 8 queries (> the abort
    # threshold), so race r2 must never be reached at all.
    tracked = list(TRACKED) + [
        TrackedCandidate("p3", "r2", "Carlos Diaz", "TX AG", "2026-11-03")]
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: tracked)
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: recorded.append(rid))

    def always_boom(q):
        calls.append(q)
        raise RuntimeError("boom")

    # r1 = 2 candidates x 4 terms = 8 queries -- more than
    # DISCOVERY_SWEEP_ABORT_AFTER (5), so the abort must cut the sweep short.
    stats = engine.run_discovery(
        _FakeConn(), provider=_FakeProvider("irrelevant"),
        fetch_feed_items=lambda o: [], ytsearch_fn=always_boom,
        hydrate_fn=lambda item: item, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True)

    assert any("sweep phase aborted" in f for f in stats.failures)
    assert len(calls) == config.DISCOVERY_SWEEP_ABORT_AFTER
    assert all("Carlos Diaz" not in q for q in calls)   # race r2 never reached
    assert recorded == []


def test_search_failure_counter_resets_on_success(monkeypatch):
    inserted = []
    recorded = []
    calls = []
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: list(TRACKED))
    monkeypatch.setattr(db, "fetch_active_outlets", lambda cur: [OUTLET])
    monkeypatch.setattr(db, "fetch_sweep_state", lambda cur: {})
    monkeypatch.setattr(db, "existing_source_keys", lambda cur: set())
    monkeypatch.setattr(db, "insert_discovered",
                        lambda cur, row: inserted.append(row) or True)
    monkeypatch.setattr(db, "mark_outlet_polled", lambda cur, oid: None)
    monkeypatch.setattr(db, "record_sweep", lambda cur, rid: recorded.append(rid))

    # 3 fails, 1 success (resets the counter), 3 fails, 1 success -- never 5
    # consecutive, across all 8 queries (2 candidates x 4 terms).
    fail_pattern = [True, True, True, False, True, True, True, False]

    def flaky(q):
        idx = len(calls)
        calls.append(q)
        if fail_pattern[idx]:
            raise RuntimeError("boom")
        return []

    stats = engine.run_discovery(
        _FakeConn(), provider=_FakeProvider("irrelevant"),
        fetch_feed_items=lambda o: [], ytsearch_fn=flaky,
        hydrate_fn=lambda item: item, peek_fetcher=None,
        sleep_fn=lambda s: None, meeting_keys=set(),
        today=dt.date(2026, 8, 2), skip_watchlist=True)

    assert not any("sweep phase aborted" in f for f in stats.failures)
    assert len(calls) == len(fail_pattern)          # all queries attempted


def test_web_items_are_not_hydrated(monkeypatch):
    inserted = []
    _patch_db(monkeypatch, inserted)
    web_item = RawItem(url="https://www.kctv5.com/2026/08/01/governor-debate/",
                       title="Maria Delgado and Ana Ruiz: full debate",
                       description=None, channel_name="KCTV5",
                       published_at="2026-08-01", outlet_id="o1", via="watchlist")
    hydrate_calls = []

    def hydrate(item):
        hydrate_calls.append(item.url)
        return item

    stats, provider = _run(monkeypatch, inserted,
                           fetch_feed_items=lambda outlet: [web_item],
                           hydrate_fn=hydrate, skip_sweeps=True)
    assert hydrate_calls == []                    # no yt-dlp on article pages
    assert stats.classified == 1                  # still reached stage 2
    assert inserted and inserted[0]["duration_seconds"] is None


def test_hydrated_publish_date_also_recency_filtered(monkeypatch):
    import dataclasses
    inserted = []
    _patch_db(monkeypatch, inserted)
    undated = dataclasses.replace(GOOD_ITEM, published_at=None, description=None)

    def hydrate(item):
        item.description = "d"
        item.published_at = "2024-01-15"
        return item

    stats, provider = _run(monkeypatch, inserted,
                           fetch_feed_items=lambda outlet: [undated],
                           hydrate_fn=hydrate, skip_sweeps=True)
    assert stats.recency_filtered == 1
    assert inserted == []

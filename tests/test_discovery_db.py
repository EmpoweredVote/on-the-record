import re

from src.discovery import db


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_insert_discovered_binds_uuid_array_and_conflicts_silently():
    cur = _FakeCursor(rows=[("new-id",)])
    inserted = db.insert_discovered(cur, {
        "source_key": "youtube:abc12345678",
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "title": "Full debate", "description_snippet": "d",
        "channel_name": "KXAN", "channel_id": "UCk", "channel_url": "https://x",
        "outlet_id": None, "duration_seconds": 3300, "published_at": "2026-08-01",
        "matched_politician_ids": ["11111111-1111-1111-1111-111111111111"],
        "race_id": "22222222-2222-2222-2222-222222222222",
        "event_kind_guess": "debate", "source_tier_guess": 1, "route": "ingest",
        "confidence": 0.9, "why": "w", "discovered_via": "search", "status": "pending",
    })
    assert inserted is True
    sql, params = cur.executed[0]
    assert "on conflict (source_key) do nothing" in sql.lower()
    assert "%s::uuid[]" in sql
    assert params[0] == "youtube:abc12345678"


def test_insert_discovered_returns_false_on_conflict():
    cur = _FakeCursor(rows=[])
    assert db.insert_discovered(cur, _minimal_row()) is False


def _minimal_row():
    return {"source_key": "k", "url": "u", "title": None, "description_snippet": None,
            "channel_name": None, "channel_id": None, "channel_url": None,
            "outlet_id": None, "duration_seconds": None, "published_at": None,
            "matched_politician_ids": [], "race_id": None, "event_kind_guess": None,
            "source_tier_guess": None, "route": "ingest", "confidence": None,
            "why": None, "discovered_via": "watchlist", "status": "pending"}


def test_fetch_tracked_candidates_filters_active_pipeline_races():
    cur = _FakeCursor(rows=[("p1", "r1", "Maria Delgado", "TX Senate (general)", "2026-11-03")])
    tracked = db.fetch_tracked_candidates(cur)
    sql, _ = cur.executed[0]
    assert "readrank_race_pipeline" in sql
    assert "'needs_quotes','quotes_staged','published'" in sql.replace(" ", "")
    assert "order by" in sql.lower()
    assert tracked[0].full_name == "Maria Delgado"
    assert tracked[0].race_label == "TX Senate (general)"


def test_alarm_races_excludes_races_with_approved_sources():
    cur = _FakeCursor(rows=[])
    db.alarm_races(cur, days=30)
    sql, params = cur.executed[0]
    assert "not exists" in sql.lower()
    assert "'approved','ingested'" in sql.replace(" ", "")
    assert params == (30,)


class _Stats:
    examined = 40
    classified = 30
    inserted_pending = 25
    inserted_auto_filtered = 5
    spend_capped = 3
    skipped_seen = 7
    prefiltered_out = 12
    recency_filtered = 0
    failures = ["outlet X: boom", "search 'q': bot check"]


def test_insert_run_returns_id_and_binds_trigger_kind():
    cur = _FakeCursor(rows=[("run-1",)])
    run_id = db.insert_run(cur, "scheduled")
    assert run_id == "run-1"
    sql, params = cur.executed[0]
    assert "essentials.source_discovery_runs" in sql
    assert "trigger_kind" in sql
    assert params == ("scheduled",)


def test_finish_run_writes_counters_and_joined_failures():
    cur = _FakeCursor()
    db.finish_run(cur, "run-1", _Stats())
    sql, params = cur.executed[0]
    assert "finished_at = now()" in sql
    assert params[0] == 40                      # items_examined
    assert params[5] == 7                       # skipped_seen
    assert params[6] == 12                      # prefiltered_out
    assert params[7] == 0                       # recency_filtered
    assert params[8] == 2                       # failure_count
    assert params[9] == "outlet X: boom\nsearch 'q': bot check"
    assert params[10] == "run-1"


def test_finish_run_null_failures_when_none():
    class _Clean(_Stats):
        failures = []
    cur = _FakeCursor()
    db.finish_run(cur, "run-1", _Clean())
    _, params = cur.executed[0]
    assert params[8] == 0 and params[9] is None


def test_record_alarms_upserts_last_alarm_at_per_race():
    cur = _FakeCursor()
    db.record_alarms(cur, ["r1", "r2"])
    assert len(cur.executed) == 2
    sql, params = cur.executed[0]
    assert "last_alarm_at" in sql and "on conflict (race_id)" in sql
    assert params == ("r1",)


def test_record_alarms_empty_is_a_noop():
    cur = _FakeCursor()
    db.record_alarms(cur, [])
    assert cur.executed == []


def test_finish_run_sql_column_order_matches_param_order():
    cur = _FakeCursor()
    db.finish_run(cur, "run-1", _Stats())
    sql, _ = cur.executed[0]
    set_clause = sql.split("where")[0]  # exclude "where id = %s::uuid" (also matches)
    cols = re.findall(r"(\w+) = %s", set_clause)
    assert cols == ["items_examined", "classified", "inserted_pending",
                    "inserted_auto_filtered", "spend_capped", "skipped_seen",
                    "prefiltered_out", "recency_filtered", "failure_count", "failures"]


def test_finish_run_truncates_failures_text_but_not_count():
    class _Noisy(_Stats):
        failures = ["x" * 3000, "y" * 3000]
    cur = _FakeCursor()
    db.finish_run(cur, "run-1", _Noisy())
    _, params = cur.executed[0]
    assert params[8] == 2                 # count stays authoritative
    assert len(params[9]) == 4000         # text truncated


class _RowcountCursor:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


def test_apply_tier3_defer_targets_low_value_search_found_and_returns_count():
    cur = _RowcountCursor(rowcount=970)
    n = db.apply_tier3_defer(cur)
    assert n == 970
    sql = cur.executed[0][0]
    assert "update essentials.discovered_sources" in sql.lower()
    assert "'deferred'" in sql
    assert "status = 'pending'" in sql.lower()          # only touches the queue
    assert "source_tier_guess >= 3" in sql.lower()      # tier-3 tail
    assert "outlet_id is null" in sql.lower()            # search-found only; watchlisted kept
    assert "not exists" in sql.lower()                   # every-candidate-has-a-better-source guard
    assert "cardinality(d.matched_politician_ids) > 0" in sql.lower()  # skip items naming nobody
    assert "b.source_tier_guess in (1, 2)" in sql.lower()  # "better source" means tier 1-2 only

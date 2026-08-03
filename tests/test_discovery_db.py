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

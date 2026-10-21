"""fetch_stance must resolve the per-race ranking question (override ?? compass)."""
from scripts.db import fetch_stance


class _Cur:
    def __init__(self, row):
        self._row = row
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self.cur = _Cur(row)

    def cursor(self, **kw):
        return self.cur


def test_fetch_stance_resolves_override_and_passes_race_id():
    row = {
        "question_text": "OVERRIDE Q",
        "compass_question_text": "COMPASS Q",
        "override_active": True,
        "value": 3.0,
        "chairs": [],
    }
    conn = _Conn(row)
    stance = fetch_stance(conn, "pol-1", "fossil-fuels", race_id="race-1")

    assert stance["question_text"] == "OVERRIDE Q"
    assert stance["compass_question_text"] == "COMPASS Q"
    assert stance["override_active"] is True

    sql, params = conn.cur.executed
    assert "readrank_race_topic_questions" in sql
    # Lock param order: politician_id (value subquery), race_id (join), topic_key (where).
    assert params == ("pol-1", "race-1", "fossil-fuels")


def test_fetch_stance_picks_one_season_answer():
    # politician_answers holds one row per season; a scalar subquery over it raised
    # CardinalityViolation for anyone answered in two seasons. Lock the single-row pick.
    conn = _Conn({"value": 3.0, "season_name": "Season 2", "chairs": []})
    fetch_stance(conn, "pol-1", "housing")

    sql, _ = conn.cur.executed
    assert "LIMIT 1" in sql
    assert "se.status = 'open'" in sql


def test_fetch_stance_chairs_follow_open_season_bound_version():
    # Legacy inform.compass_stances is frozen and drifted from published revisions (41 topics on
    # 2026-10-21). Chairs must resolve like ev-accounts getCompassTopics: open-season pin → latest
    # published/superseded revision of that version → compass_stance_revisions.
    conn = _Conn({"chairs": [], "chairs_source": "open_season"})
    fetch_stance(conn, "pol-1", "housing")

    sql, _ = conn.cur.executed
    assert "inform.compass_stances s" not in sql
    assert "compass_topics_promoted" in sql
    assert "p.season_revision_id" in sql
    assert "e.version = pin.version" in sql
    assert "e.status IN ('published', 'superseded')" in sql
    assert "ORDER BY e.revision DESC" in sql
    assert "compass_stance_revisions" in sql
    # A topic the open season does not ask falls back to the is_current ladder.
    assert "compass_stances_current" in sql
    assert "chairs_source" in sql

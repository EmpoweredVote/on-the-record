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

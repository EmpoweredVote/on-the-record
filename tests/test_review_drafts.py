import run_local


class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchall(self):
        return self._rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, cur):
        self._cur = cur
    def cursor(self):
        return self._cur
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def commit(self): pass
    def close(self): pass


def test_list_draft_meetings_queries_draft_status(monkeypatch):
    # tests/conftest.py's autouse `_no_real_db_env` fixture strips DATABASE_URL
    # from every test's environment (defense-in-depth against leaking the real
    # DB into tests). _list_draft_meetings still calls _require_db_url() before
    # using the injected `connect`, so it needs a value present even though the
    # fake connect never uses it. See test_republish_all.py for the same shape.
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    cur = _Cur(rows=[("2026-09-04-house-floor", "2026-09-04", "House Floor",
                      120, 8, {"gate_verdict": "review", "gate_coverage": 0.62})])
    drafts = run_local._list_draft_meetings(connect=lambda *_a, **_k: _Conn(cur))
    sql = cur.executed[0][0]
    assert "status = 'draft'" in sql.replace('"', "'")
    assert drafts[0]["slug"] == "2026-09-04-house-floor"
    assert drafts[0]["gate_verdict"] == "review"


def test_promote_meeting_flips_status_to_published(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    cur = _Cur(rowcount=1)
    ok = run_local._promote_meeting("2026-09-04-house-floor",
                                    connect=lambda *_a, **_k: _Conn(cur))
    sql, params = cur.executed[0]
    assert "set status = 'published'" in sql.lower().replace('"', "'")
    assert "where slug = %s" in sql.lower()
    assert params == ("2026-09-04-house-floor",)
    assert ok is True


def test_promote_meeting_returns_false_when_no_row(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    cur = _Cur(rowcount=0)
    ok = run_local._promote_meeting("missing",
                                    connect=lambda *_a, **_k: _Conn(cur))
    assert ok is False

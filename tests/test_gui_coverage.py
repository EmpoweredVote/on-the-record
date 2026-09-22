import pytest
from gui import coverage
from gui.coverage import race_level

@pytest.mark.parametrize("name,expected", [
    ("U.S. Senate — Indiana", "federal"),
    ("U.S. Representative District 9", "federal"),
    ("President of the United States", "federal"),
    ("Indiana Governor", "state"),
    ("Attorney General", "state"),
    ("State Representative, District 060", "state"),
    ("Monroe County Commissioner, District 3", "county"),
    ("County Council At-Large", "county"),
    ("MCCSC School Board, District 1", "school"),
    ("Bloomington Board of Education", "school"),
    ("Bloomington Mayor", "local"),
    ("Bloomington City Council, District 4", "local"),
    (None, "local"),
])
def test_race_level(name, expected):
    assert race_level(name) == expected


# --- Task 4: coverage aggregation — state index, sections, per-race counts ---
#
# NOTE: there is no local/seeded test database (conftest.py deletes DATABASE_URL
# for every test via the autouse `_no_real_db_env` fixture). The three-tier
# strategy below replaces the brief's `seeded_test_db`-based sample test, which
# does not exist in this project:
#   1. no-DB path -> []
#   2. fake-cursor injection -> real mapping/sort behavior, no live DB needed
#   3. live_db-gated integration (skipped unless DATABASE_URL exported before
#      pytest) -> sanity-checks shape against the real schema


class _FakeCursor:
    """Records every execute() call and replays canned fetchall() rows."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _fake_connect_returning(rows):
    """Build a (fake_connect, cursor) pair: fake_connect is a drop-in for
    psycopg2.connect that always returns a connection wrapping one cursor
    pre-loaded with `rows`; the returned cursor lets a test inspect exactly
    what SQL/params were bound."""
    cur = _FakeCursor(rows)
    conn = _FakeConn(cur)
    return (lambda url: conn), cur


# --- 1. No-DB path ---

def test_races_for_state_no_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert coverage.races_for_state("IN") == []
    assert coverage.state_index() == []


# --- 2. Fake-cursor injection: races_for_state maps + sorts for real ---

def test_races_for_state_maps_and_sorts(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://fake")
    # Deliberately scrambled input order (not pre-sorted) so a passing test
    # proves races_for_state() itself sorts by LEVEL_ORDER, then locality,
    # then name -- not that it merely passes through DB order. Column order
    # matches _RACES_SQL: id, position_name, locality, candidates,
    # quote_sources, ingested, pending. Each row's four counts are pairwise
    # distinct so a column-swap bug (e.g. ingested/quote_sources mixed up)
    # would fail the assertions below instead of hiding behind equal values.
    rows = [
        ("r-school", "MCCSC School Board, District 1", "Monroe County, Indiana", 0, 0, 0, 0),
        ("r-local-b", "City Clerk", "City of Bloomington, Indiana", 41, 42, 43, 44),
        ("r-county-m", "Monroe County Commissioner, District 3", "Monroe County, Indiana", 21, 22, 23, 24),
        ("r-fed", "U.S. Senate", None, 6, 5, 4, 7),
        ("r-local-a", "Bloomington Mayor", "City of Bloomington, Indiana", 31, 32, 33, 34),
        ("r-state", "Indiana Governor", None, 3, 2, 1, 9),
        ("r-county-b", "Boone County Commissioner", "Boone County, Indiana", 11, 12, 13, 14),
    ]
    fake_connect, cur = _fake_connect_returning(rows)
    monkeypatch.setattr(coverage.psycopg2, "connect", fake_connect)

    result = coverage.races_for_state("in")  # lowercase in -> must bind "IN"

    assert all(isinstance(r, coverage.RaceCoverage) for r in result)
    # federal, state precede county precede local precede school; within a
    # level, locality then name break ties (Boone < Monroe; Bloomington < City)
    assert [r.race_id for r in result] == [
        "r-fed", "r-state", "r-county-b", "r-county-m",
        "r-local-a", "r-local-b", "r-school",
    ]
    levels = [r.level for r in result]
    assert levels == sorted(levels, key=coverage.LEVEL_ORDER.index)

    by_id = {r.race_id: r for r in result}

    fed = by_id["r-fed"]
    assert fed.level == "federal" and fed.locality is None
    assert (fed.candidates, fed.quote_sources, fed.ingested, fed.pending) == (6, 5, 4, 7)

    state = by_id["r-state"]
    assert state.level == "state" and state.locality is None
    assert (state.candidates, state.quote_sources, state.ingested, state.pending) == (3, 2, 1, 9)

    county_b = by_id["r-county-b"]
    assert county_b.level == "county" and county_b.locality == "Boone County, Indiana"
    assert (county_b.candidates, county_b.quote_sources, county_b.ingested, county_b.pending) == (11, 12, 13, 14)

    county_m = by_id["r-county-m"]
    assert county_m.level == "county" and county_m.locality == "Monroe County, Indiana"

    local_a = by_id["r-local-a"]
    assert local_a.level == "local" and local_a.locality == "City of Bloomington, Indiana"
    assert local_a.position_name == "Bloomington Mayor"

    local_b = by_id["r-local-b"]
    assert local_b.level == "local" and local_b.position_name == "City Clerk"

    school = by_id["r-school"]
    assert school.level == "school" and school.locality == "Monroe County, Indiana"

    # SQL uses a bound param (state upper-cased), never string interpolation
    sql, params = cur.executed[0]
    assert params == ("IN",)
    assert "%s" in sql
    assert "essentials.races" in sql.lower()


def test_races_for_state_db_error_returns_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://fake")

    def _boom(url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(coverage.psycopg2, "connect", _boom)
    assert coverage.races_for_state("IN") == []


# --- 2. Fake-cursor injection: state_index maps DB rows into dicts ---

def test_state_index_maps_and_preserves_sql_order(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://fake")
    # Rows arrive already ordered the way the SQL's own ORDER BY would
    # produce (most pending first); state_index() should map them into dicts
    # without re-sorting in Python, so this also pins that no accidental
    # re-sort/reverse creeps in later.
    rows = [("IN", 42), ("TX", 17), ("OH", 0)]
    fake_connect, cur = _fake_connect_returning(rows)
    monkeypatch.setattr(coverage.psycopg2, "connect", fake_connect)

    result = coverage.state_index()

    assert result == [
        {"state": "IN", "pending": 42},
        {"state": "TX", "pending": 17},
        {"state": "OH", "pending": 0},
    ]
    sql = cur.executed[0][0].lower()
    assert "essentials.elections" in sql
    assert "essentials.discovered_sources" in sql
    assert "group by e.state" in sql
    assert "order by" in sql and "desc" in sql
    assert "e.state is not null" in sql


def test_state_index_db_error_returns_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://fake")

    def _boom(url):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(coverage.psycopg2, "connect", _boom)
    assert coverage.state_index() == []


# --- 3. live_db-gated integration: skipped unless DATABASE_URL is exported
# before pytest starts (see conftest.py's LIVE_DB_URL capture). This is not
# run in the normal suite -- it exists so Chris can verify the real SQL
# against the live essentials schema later. A skip here is a PASS condition.

def test_races_for_state_live_db_shape(live_db):
    rows = coverage.races_for_state("IN")
    assert isinstance(rows, list)
    for r in rows:
        assert isinstance(r, coverage.RaceCoverage)
        assert r.level in coverage.LEVEL_ORDER
        assert isinstance(r.candidates, int)
        assert isinstance(r.quote_sources, int)
        assert isinstance(r.ingested, int)
        assert isinstance(r.pending, int)


def test_state_index_live_db_shape(live_db):
    rows = coverage.state_index()
    assert isinstance(rows, list)
    for row in rows:
        assert set(row.keys()) == {"state", "pending"}
        assert isinstance(row["state"], str)
        assert isinstance(row["pending"], int)

# tests/test_gui_races.py
from __future__ import annotations

from gui.races import race_display, race_slug


def test_race_display_with_and_without_year():
    assert race_display("Governor of Michigan", 2026) == "Governor of Michigan · 2026"
    assert race_display("U.S. Senate Alabama", None) == "U.S. Senate Alabama"


def test_race_slug_strips_us_and_connectives():
    assert race_slug("U.S. Senate Alabama") == "senate-alabama"
    assert race_slug("Governor of Michigan") == "governor-michigan"
    assert race_slug("Governor") == "governor"
    assert race_slug("Long Beach Mayor") == "long-beach-mayor"
    assert race_slug("") == ""


def test_race_slug_state_prefix_disambiguates_federal_districts():
    # federal districts collide without the state; prefix makes them unique
    assert race_slug("U.S. Representative District 1", "AZ") == "az-representative-district-1"
    assert race_slug("Governor", "AZ") == "az-governor"


def test_race_slug_state_prefix_not_duplicated_when_already_leading():
    # position_name already starts with the state token -> no "ma-ma-..."
    assert race_slug("MA House 10th Bristol District", "MA") == "ma-house-10th-bristol-district"


# tests/test_gui_races.py  (append)
import gui.races as races


# --- query parsing: state extraction + synonym expansion ---

def test_parse_race_query_full_state_name():
    state, terms = races.parse_race_query("arizona governor")
    assert state == "AZ"
    # "governor" survives as a text term (state word removed)
    assert any("governor" in alts for alts in terms)


def test_parse_race_query_two_letter_code():
    state, terms = races.parse_race_query("az governor")
    assert state == "AZ"


def test_parse_race_query_multiword_state_name():
    state, _ = races.parse_race_query("new mexico senate")
    assert state == "NM"


def test_parse_race_query_congressional_maps_to_representative():
    state, terms = races.parse_race_query("arizona congressional district 1")
    assert state == "AZ"
    flat = [a for alts in terms for a in alts]
    assert "representative" in flat          # congressional -> representative
    assert "district" in flat
    assert "1" in flat


def test_parse_race_query_cd_abbrev_maps_to_representative():
    _, terms = races.parse_race_query("az cd 1")
    flat = [a for alts in terms for a in alts]
    assert "representative" in flat


def test_parse_race_query_no_state():
    state, terms = races.parse_race_query("governor")
    assert state is None
    assert any("governor" in alts for alts in terms)


# --- display gains an optional state prefix (back-compat when omitted) ---

def test_race_display_state_prefix():
    assert (races.race_display("U.S. Representative District 1", 2026, "AZ")
            == "AZ · U.S. Representative District 1 · 2026")


def test_race_display_no_state_unchanged():
    assert race_display("Governor", 2026) == "Governor · 2026"


# --- end-to-end search wiring against a fake cursor ---

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def test_search_races_safe_filters_state_and_prefixes_label(monkeypatch):
    cur = _FakeCursor([("uuid-az-1", "U.S. Representative District 1", 2026, "AZ")])
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(races.psycopg2, "connect", lambda url: _FakeConn(cur))
    out = races.search_races_safe("arizona congressional district 1")
    assert out["error"] is None
    assert out["results"] == [{
        "race_id": "uuid-az-1",
        "label": "AZ · U.S. Representative District 1 · 2026",
        "slug": "az-representative-district-1",
    }]
    sql, params = cur.executed
    # state code passed as a bound param, not interpolated
    assert "AZ" in params
    # synonym-expanded text token reached the query
    assert any("representative" in str(p).lower() for p in params)


def test_search_races_safe_no_db_returns_empty(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: None)
    out = races.search_races_safe("senate")
    assert out == {"results": [], "error": None}


def test_search_races_safe_short_query_returns_empty(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    out = races.search_races_safe("x")           # <2 chars -> no query attempted
    assert out["results"] == []


def test_search_races_safe_swallows_db_errors(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    def boom(url):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(races.psycopg2, "connect", boom)
    out = races.search_races_safe("senate")
    assert out["results"] == []
    assert out["error"]                          # a message, not a crash


def test_race_labels_empty_and_no_db(monkeypatch):
    assert races.race_labels([]) == {}
    monkeypatch.setattr(races, "_db_url", lambda: None)
    assert races.race_labels(["uuid-1"]) == {}


def test_race_labels_swallows_db_errors(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(races.psycopg2, "connect",
                        lambda url: (_ for _ in ()).throw(RuntimeError("down")))
    assert races.race_labels(["uuid-1"]) == {}

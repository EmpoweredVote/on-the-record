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


# tests/test_gui_races.py  (append)
import gui.races as races


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

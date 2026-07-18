# tests/test_congress_roster.py
from __future__ import annotations

import json
from pathlib import Path

from src.congress_roster import CongressMember, CongressRoster, _member_from_raw

_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"


def _raw() -> list[dict]:
    return json.loads(_FIX.read_text(encoding="utf-8"))


def test_member_from_raw_senator():
    ernst = next(e for e in _raw() if e["name"]["last"] == "Ernst")
    m = _member_from_raw(ernst, "senate")
    assert m == CongressMember(
        bioguide="E000295", full_name="Joni Ernst", last_name="Ernst",
        state="IA", district=None, chamber="senate", party="Republican")


def test_member_from_raw_representative():
    adam = next(e for e in _raw() if e["name"]["last"] == "Smith" and e["terms"][-1]["state"] == "WA")
    m = _member_from_raw(adam, "house")
    assert m.bioguide == "S000510"
    assert m.district == 9
    assert m.chamber == "house"


def test_member_from_raw_returns_none_for_wrong_chamber():
    ernst = next(e for e in _raw() if e["name"]["last"] == "Ernst")  # a senator
    assert _member_from_raw(ernst, "house") is None


def test_congress_roster_by_surname_accessor():
    m = CongressMember("X0", "Jane Doe", "Doe", "CA", None, "senate", "Democrat")
    roster = CongressRoster(chamber="senate", members=[m], _by_surname={"doe": [m]})
    assert roster.by_surname("DOE") == [m]      # case-insensitive
    assert roster.by_surname("nope") == []

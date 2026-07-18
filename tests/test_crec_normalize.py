# tests/test_crec_normalize.py
from __future__ import annotations

import json
from pathlib import Path

from src.congress_roster import build_roster
from src.crec_normalize import ResolvedSpeaker, _resolve_surname, _role_slug

_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"


def _roster(chamber):
    return build_roster(json.loads(_FIX.read_text(encoding="utf-8")), chamber)


def test_role_slug():
    assert _role_slug("PRESIDING OFFICER") == "presiding_officer"
    assert _role_slug("SPEAKER pro tempore") == "speaker"
    assert _role_slug("VICE PRESIDENT") == "vice_president"
    assert _role_slug("Clerk") == "clerk"


def test_resolve_surname_unique_senate():
    res = _resolve_surname("McConnell", None, _roster("senate"))
    assert res.member.bioguide == "M000355"
    assert res.method == "surname"
    assert res.confidence == 1.0
    assert res.needs_review is False


def test_resolve_surname_with_state_disambiguates():
    res = _resolve_surname("Smith", "Nebraska", _roster("house"))
    assert res.member.bioguide == "S001172"
    assert res.method == "surname_state"


def test_resolve_surname_ambiguous_without_state():
    res = _resolve_surname("Smith", None, _roster("house"))
    assert res.member is None
    assert res.method == "ambiguous"
    assert res.needs_review is True


def test_resolve_surname_unknown():
    res = _resolve_surname("Nonesuch", None, _roster("senate"))
    assert res.member is None
    assert res.method == "unresolved"

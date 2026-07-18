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


# add to tests/test_crec_normalize.py
from src.crec_normalize import normalize_designation


def test_normalize_plain_member_uppercase():
    res = normalize_designation("Mr. McCONNELL", _roster("senate"))
    assert res.member.bioguide == "M000355"
    assert res.method == "surname"


def test_normalize_member_of_state():
    res = normalize_designation("Ms. BALDWIN of Wisconsin", _roster("senate"))
    assert res.member.bioguide == "B001230"
    assert res.method == "surname_state"


def test_normalize_house_ambiguous_needs_review():
    res = normalize_designation("Mr. SMITH", _roster("house"))
    assert res.member is None
    assert res.needs_review is True
    assert res.method == "ambiguous"


def test_normalize_house_of_state_resolves():
    res = normalize_designation("Mr. SMITH of Washington", _roster("house"))
    assert res.member.bioguide == "S000510"


def test_normalize_presiding_parenthetical():
    res = normalize_designation("The PRESIDING OFFICER (Mrs. Ernst)", _roster("senate"))
    assert res.member.bioguide == "E000295"
    assert res.method == "presiding_parenthetical"


def test_normalize_bare_presiding_officer_is_role():
    res = normalize_designation("The PRESIDING OFFICER", _roster("senate"))
    assert res.member is None
    assert res.role == "presiding_officer"
    assert res.method == "role"


def test_normalize_bare_speaker_is_role():
    res = normalize_designation("The SPEAKER", _roster("house"))
    assert res.role == "speaker"
    assert res.method == "role"


def test_normalize_unknown_surname_unresolved():
    res = normalize_designation("Mr. NONESUCH", _roster("senate"))
    assert res.method == "unresolved"


def test_normalize_presiding_parenthetical_unknown_falls_back_to_role():
    res = normalize_designation("The PRESIDING OFFICER (Mr. Nonesuch)", _roster("senate"))
    assert res.member is None
    assert res.role == "presiding_officer"
    assert res.method == "role"


# add to tests/test_crec_normalize.py
from src.govinfo import CrecTurn
from src.crec_normalize import annotate_turns


def test_annotate_turns_pairs_each_turn_with_resolution():
    turns = [
        CrecTurn("Mr. McCONNELL", "I move to proceed.", "g1", 0),
        CrecTurn("The PRESIDING OFFICER", "Without objection.", "g1", 1),
        CrecTurn("Ms. BALDWIN of Wisconsin", "I rise in support.", "g1", 2),
    ]
    pairs = annotate_turns(turns, _roster("senate"))
    assert [t.order for t, _ in pairs] == [0, 1, 2]
    assert pairs[0][1].member.bioguide == "M000355"
    assert pairs[1][1].role == "presiding_officer"
    assert pairs[2][1].member.bioguide == "B001230"

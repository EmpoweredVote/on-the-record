# tests/test_crec_essentials.py
from __future__ import annotations

from src.congress_roster import CongressMember
from src.crec_essentials import (
    resolve_politician_id, _is_federal, _chamber_matches, _district_number,
)


def _mem(last, chamber, district=None, bio="X000001"):
    return CongressMember(bio, f"First {last}", last, "XX", district, chamber, "Party")


def _rec(name, office, *, gov="United States Federal Government",
         district_label="", pid="id1", slug=None):
    return {"politician_id": pid, "politician_slug": slug, "full_name": name,
            "office_title": office, "district_label": district_label,
            "is_incumbent": True, "government_name": gov}


def test_helpers():
    assert _is_federal({"government_name": "United States Federal Government"})
    assert not _is_federal({"government_name": "City of Cambridge, MA"})
    assert not _is_federal({})
    assert _chamber_matches({"office_title": "Senator"}, "senate")
    assert _chamber_matches({"office_title": "U.S. Representative"}, "house")
    assert not _chamber_matches({"office_title": "Senator"}, "house")
    assert _district_number("Congressional District 9") == 9
    assert _district_number("At-Large") is None
    assert _district_number("") is None


def test_resolve_single_federal_rep():
    search = lambda q, **kw: [_rec("Bryan Steil", "U.S. Representative",
                                   district_label="Congressional District 1", pid="P1")]
    assert resolve_politician_id(_mem("Steil", "house", 1), search=search) == ("P1", None)


def test_resolve_single_senator():
    search = lambda q, **kw: [_rec("John Thune", "Senator", pid="THUNE")]
    assert resolve_politician_id(_mem("Thune", "senate"), search=search) == ("THUNE", None)


def test_resolve_filters_out_local_namesake():
    search = lambda q, **kw: [
        _rec("Jim McGovern", "Representative", district_label="Congressional District 2", pid="FED"),
        _rec("Marc McGovern", "City Councillor", gov="City of Cambridge, MA",
             district_label="Cambridge", pid="LOCAL"),
    ]
    assert resolve_politician_id(_mem("McGovern", "house", 2), search=search) == ("FED", None)


def test_resolve_same_surname_reps_by_district():
    search = lambda q, **kw: [
        _rec("Adam Smith", "U.S. Representative", district_label="Congressional District 9", pid="WA9"),
        _rec("Adrian Smith", "U.S. Representative", district_label="Congressional District 3", pid="NE3"),
    ]
    assert resolve_politician_id(_mem("Smith", "house", 3), search=search) == ("NE3", None)


def test_resolve_chamber_filter_excludes_wrong_house():
    search = lambda q, **kw: [_rec("Some Smith", "U.S. Representative",
                                   district_label="Congressional District 1", pid="R1")]
    assert resolve_politician_id(_mem("Smith", "senate"), search=search) is None


def test_resolve_excludes_non_incumbent_challenger():
    # essentials shares one ID space for incumbents AND challengers; the CREC
    # oracle only ever resolves a SITTING member, so a same-district challenger
    # must be excluded (else false ambiguity, or worse, the wrong link).
    search = lambda q, **kw: [
        _rec("Bryan Steil", "U.S. Representative",
             district_label="Congressional District 1", pid="INC"),   # is_incumbent True
        {"politician_id": "CHAL", "politician_slug": None, "full_name": "Chad Steil",
         "office_title": "U.S. Representative", "district_label": "Congressional District 1",
         "is_incumbent": False, "government_name": "United States Federal Government"},
    ]
    assert resolve_politician_id(_mem("Steil", "house", 1), search=search) == ("INC", None)


def test_resolve_ambiguous_returns_none():
    search = lambda q, **kw: [
        _rec("A Smith", "U.S. Representative", district_label="Congressional District 5", pid="X"),
        _rec("B Smith", "U.S. Representative", district_label="Congressional District 5", pid="Y"),
    ]
    assert resolve_politician_id(_mem("Smith", "house", 5), search=search) is None


def test_resolve_no_match_returns_none():
    assert resolve_politician_id(_mem("Nobody", "house", 1), search=lambda q, **kw: []) is None


def test_resolve_search_error_returns_none():
    def boom(q, **kw):
        raise RuntimeError("essentials api down")
    assert resolve_politician_id(_mem("Steil", "house", 1), search=boom) is None

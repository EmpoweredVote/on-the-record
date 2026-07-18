# tests/test_crec_identify.py
from __future__ import annotations

import pytest

from src.congress_roster import CongressMember
from src.crec_align import LabelResolution
from src.crec_identify import label_resolution_to_mapping, parse_crec_arg


def _member():
    return CongressMember("M000355", "Mitch McConnell", "McConnell", "KY", None, "senate", "Republican")


def test_convert_confident_member():
    res = LabelResolution(speaker_label="S0", member=_member(), confidence=0.9,
                          method="congressional_record", needs_review=False,
                          matched_turns=2, total_turns=2)
    m = label_resolution_to_mapping(res)
    assert m.speaker_label == "S0"
    assert m.speaker_name == "Mitch McConnell"
    assert m.id_method == "congressional_record"
    assert m.confidence == 0.9
    assert m.local_slug == "congress-M000355"
    assert m.politician_id is None
    assert m.needs_review is False


def test_convert_role():
    res = LabelResolution(speaker_label="S9", role="presiding_officer", confidence=1.0,
                          method="congressional_record", needs_review=False,
                          matched_turns=1, total_turns=1)
    m = label_resolution_to_mapping(res)
    assert m.speaker_name == "The Presiding Officer"
    assert m.id_method == "congressional_record"
    assert m.local_slug is None
    assert m.needs_review is False


def test_convert_role_unknown_slug_titlecases():
    res = LabelResolution(speaker_label="S9", role="some_new_role", method="congressional_record")
    m = label_resolution_to_mapping(res)
    assert m.speaker_name == "Some New Role"


def test_convert_ambiguous():
    res = LabelResolution(speaker_label="S0", method="ambiguous", needs_review=True)
    m = label_resolution_to_mapping(res)
    assert m.speaker_name is None
    assert m.needs_review is True
    assert m.speaker_status == "unidentified"


def test_convert_unresolved_returns_none():
    res = LabelResolution(speaker_label="S0", method="unresolved")
    assert label_resolution_to_mapping(res) is None


def test_parse_crec_arg_valid_lowercases_chamber():
    assert parse_crec_arg(["2018-10-10", "Senate"]) == ("2018-10-10", "senate")


def test_parse_crec_arg_none_when_absent():
    assert parse_crec_arg(None) is None


def test_parse_crec_arg_bad_date():
    with pytest.raises(SystemExit):
        parse_crec_arg(["10/10/2018", "house"])


def test_parse_crec_arg_bad_chamber():
    with pytest.raises(SystemExit):
        parse_crec_arg(["2018-10-10", "congress"])

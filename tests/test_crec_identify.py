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


# add to tests/test_crec_identify.py
import json
from pathlib import Path

from src.models import Segment
from src.crec_identify import crec_speaker_mappings

_LEG_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"

_GRANULES = (
    '{"count":1,"granules":['
    '{"granuleId":"CREC-2025-01-10-pt1-PgS100-1","granuleClass":"SENATE","title":"HEALTHCARE"}'
    '],"nextPage":null}'
)
_HTM = (
    "<html><body><pre>\n"
    "[Congressional Record Volume 171, Number 5 (Friday, January 10, 2025)]\n"
    "[Senate]\n"
    "[Page S100]\n"
    "From the Congressional Record Online through the Government Publishing Office "
    "[<a href=\"https://www.gpo.gov\">www.gpo.gov</a>]\n\n"
    "                   HEALTHCARE FUNDING\n\n"
    "  Mr. McCONNELL. I move to proceed to the healthcare funding bill.\n"
    "  Ms. BALDWIN of Wisconsin. I rise in strong support of the healthcare measure.\n\n"
    "                          ____________________\n\n"
    "</pre></body></html>"
)


def _fake_fetch(url: str) -> str:
    if "legislators-current" in url:
        return _LEG_FIX.read_text(encoding="utf-8")
    if "/granules/" in url and "/htm" in url:
        return _HTM
    if "/granules?" in url:
        return _GRANULES
    raise AssertionError(f"unexpected url {url}")


def _seg(i, label, text):
    return Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                   speaker_label=label, text=text)


def test_crec_speaker_mappings_resolves_members(tmp_path):
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill today"),
        _seg(1, "SPEAKER_01", "I rise in strong support of this healthcare measure"),
    ]
    out = crec_speaker_mappings(
        "2025-01-10", "senate", segs,
        fetch=_fake_fetch, cache_path=tmp_path / "leg.json", min_confidence=0.4)
    assert out["SPEAKER_00"].speaker_name == "Mitch McConnell"
    assert out["SPEAKER_00"].id_method == "congressional_record"
    assert out["SPEAKER_00"].local_slug == "congress-M000355"
    assert out["SPEAKER_01"].speaker_name == "Tammy Baldwin"


def test_crec_speaker_mappings_empty_when_no_record(tmp_path):
    def no_record(url):
        raise RuntimeError("404 no CREC package")
    out = crec_speaker_mappings(
        "1900-01-01", "senate", [_seg(0, "SPEAKER_00", "hello")],
        fetch=no_record, cache_path=tmp_path / "leg.json")
    assert out == {}

# tests/test_govinfo.py
from __future__ import annotations

from pathlib import Path

from src.govinfo import CrecTurn, _package_id, _resolve_api_key, parse_granule_list, _next_offset_mark

_FIX = Path(__file__).parent / "fixtures" / "govinfo"


def _read(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def test_crec_turn_fields():
    t = CrecTurn(speaker_raw="Mr. Cotton", text="The majority leader is recognized.",
                 granule_id="CREC-2018-10-10-pt1-PgS6735-6", order=0)
    assert t.speaker_raw == "Mr. Cotton"
    assert t.text == "The majority leader is recognized."
    assert t.granule_id == "CREC-2018-10-10-pt1-PgS6735-6"
    assert t.order == 0


def test_package_id():
    assert _package_id("2018-10-10") == "CREC-2018-10-10"


def test_resolve_api_key_prefers_arg(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "envkey")
    assert _resolve_api_key("argkey") == "argkey"


def test_resolve_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "envkey")
    assert _resolve_api_key(None) == "envkey"


def test_resolve_api_key_falls_back_to_demo(monkeypatch):
    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    assert _resolve_api_key(None) == "DEMO_KEY"


def test_parse_granule_list_filters_house():
    ids = parse_granule_list(_read("granules_page1.json"), "house")
    assert ids == ["CREC-2018-10-10-pt1-PgH1-1"]


def test_parse_granule_list_filters_senate():
    ids = parse_granule_list(_read("granules_page1.json"), "senate")
    assert ids == ["CREC-2018-10-10-pt1-PgS6735-6"]


def test_parse_granule_list_is_case_insensitive():
    assert parse_granule_list(_read("granules_page1.json"), "HOUSE") == \
        ["CREC-2018-10-10-pt1-PgH1-1"]


def test_parse_granule_list_excludes_digest_and_extensions():
    ids = parse_granule_list(_read("granules_page1.json"), "house") + \
        parse_granule_list(_read("granules_page1.json"), "senate")
    assert "CREC-2018-10-10-pt1-PgD1124" not in ids
    assert "CREC-2018-10-10-pt1-PgE1-1" not in ids


def test_next_offset_mark_returns_url_then_none():
    assert _next_offset_mark(_read("granules_page1.json")) == \
        "https://api.govinfo.gov/packages/CREC-2018-10-10/granules?offsetMark=PAGE2&pageSize=100"
    assert _next_offset_mark(_read("granules_page2.json")) is None

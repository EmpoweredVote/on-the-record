# tests/test_govinfo.py
from __future__ import annotations

from src.govinfo import CrecTurn, _package_id, _resolve_api_key


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

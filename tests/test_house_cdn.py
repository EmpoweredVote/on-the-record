import json
from pathlib import Path
import pytest
from src.house_cdn import resolve_session, HouseFloorSource

FIX = Path(__file__).parent / "fixtures" / "house_cdn" / "broadcastevents_20260716.json"


def _fake_fetch(_url: str) -> str:
    return FIX.read_text()


def test_resolve_session_picks_hls_east_and_strips_hash():
    s = resolve_session("2026-07-16", fetch=_fake_fetch)
    assert isinstance(s, HouseFloorSource)
    assert s.manifest_url == (
        "https://houseliveprod-f9h4cpb9dyb8gegg.a01.azurefd.net"
        "/east/2026-07-16T08-51-14/manifest.m3u8"
    )  # HLS, east mirror, no #t= hash
    assert s.manifest_url.endswith("manifest.m3u8")


def test_resolve_session_metadata_and_citation():
    s = resolve_session("2026-07-16", fetch=_fake_fetch)
    assert s.date == "2026-07-16"
    assert s.title == "LEGISLATIVE DAY OF JULY 16, 2026"
    assert s.congress == "119" and s.session == "2"
    assert s.start.startswith("2026-07-16") and s.end.startswith("2026-07-16")
    assert s.citation_url == "https://live.house.gov/?date=2026-07-16"
    assert "public domain" in s.rights.lower()


def test_resolve_session_builds_broadcastevents_url():
    captured = {}
    def fetch(url):
        captured["url"] = url
        return FIX.read_text()
    resolve_session("2026-07-16", fetch=fetch)
    assert captured["url"].endswith("/broadcastevents/20260716")  # dashes stripped for id


def test_resolve_session_falls_back_to_central_when_no_east():
    doc = json.loads(FIX.read_text())
    ev = doc[0]
    ev["asset"]["files"] = [f for f in ev["asset"]["files"]
                            if not (f["type"] == "HLS" and "/east/" in f["url"])]
    s = resolve_session("2026-07-16", fetch=lambda _u: json.dumps([ev]))
    assert "/central/" in s.manifest_url and s.manifest_url.endswith("manifest.m3u8")


def test_resolve_session_returns_none_when_no_hls():
    doc = json.loads(FIX.read_text())
    ev = doc[0]
    ev["asset"]["files"] = [f for f in ev["asset"]["files"] if f["type"] != "HLS"]
    assert resolve_session("2026-07-16", fetch=lambda _u: json.dumps([ev])) is None


def test_resolve_session_returns_none_on_empty_or_error():
    assert resolve_session("2026-07-16", fetch=lambda _u: "[]") is None
    def boom(_u): raise RuntimeError("404")
    assert resolve_session("2026-07-16", fetch=boom) is None

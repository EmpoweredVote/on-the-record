from fastapi.testclient import TestClient

from gui.app import create_app
import gui.discovery as discovery
from gui.discovery import DiscoveredRow


def _row(**over):
    base = dict(
        id="d1", url="https://www.youtube.com/watch?v=abc12345678",
        title="Full debate", description_snippet="All four candidates",
        channel_name="KXAN", channel_id="UCk", channel_url=None, outlet_id=None,
        duration_seconds=3480, published_at="2026-08-01", race_id="r1",
        event_kind_guess="debate", source_tier_guess=1, route="ingest",
        confidence=0.9, why="58-min video, all candidates in description",
        discovered_via="search", status="pending", election_date="2026-11-03",
        race_label="TX · U.S. Senate · General · 2026",
    )
    base.update(over)
    return DiscoveredRow(**base)


def test_thumb_and_duration_properties():
    r = _row()
    assert r.thumb_url == "https://i.ytimg.com/vi/abc12345678/mqdefault.jpg"
    assert r.duration_label == "58m"
    assert _row(duration_seconds=5460).duration_label == "1h31m"
    assert _row(duration_seconds=None).duration_label == "?"
    assert _row(url="https://x.example/ep/1").thumb_url is None


def test_discovery_page_renders_rows_and_health(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [_row()])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [("r9", "MI Governor (D primary)", "2026-08-04")],
        "stale_outlets": ["PBS Kansas"], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    body = resp.text
    assert "Full debate" in body
    assert "TX · U.S. Senate" in body
    assert "MI Governor (D primary)" in body       # alarm strip
    assert "58-min video" in body                   # the classifier's why
    assert "watch this channel" in body.lower()     # flywheel offer (no outlet_id)


def test_discovery_page_empty_state(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [])
    monkeypatch.setattr(discovery, "health",
                        lambda: {"alarms": [], "stale_outlets": [], "pending_total": 0})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "No pending discoveries" in resp.text


def test_library_links_to_discovery(monkeypatch, tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/")
    assert 'href="/discovery"' in resp.text

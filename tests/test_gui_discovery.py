from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from gui.app import create_app
import gui.discovery as discovery
from gui.discovery import DiscoveredRow


def _flash(resp):
    """Decoded value of the ?flash= query param on a redirect response."""
    return parse_qs(urlparse(resp.headers["location"]).query).get("flash", [""])[0]


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


def test_approve_ingest_enqueues_with_gated_fields(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    launched = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: launched.setdefault("status", status) or True)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)

    def fake_enqueue(p):
        launched["params"] = p
        return ("started", "mid")

    monkeypatch.setattr(batch, "launch_or_enqueue", fake_enqueue)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303
    p = launched["params"]
    assert p.input == "https://www.youtube.com/watch?v=abc12345678"
    assert p.event_kind == "debate" and p.meeting_type == "Debate"
    assert p.date == "2026-08-01"
    assert p.race_id == "r1" and p.race_slug == "us-senate-tx-general"
    assert p.event_orgs == ["KXAN"]
    assert launched["status"] == "ingested"


def test_approve_ingest_blocks_known_duplicate(monkeypatch):
    import gui.runner as runner
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: "2026-08-01-debate")
    statuses = {}
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: statuses.update(s=status, r=reason) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303 and "duplicate" in resp.headers["location"]
    assert statuses["s"] == "superseded"


def test_reject_requires_and_records_reason(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(status=status, reason=reason) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject", data={"reason": "clip-not-original"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls == {"status": "rejected", "reason": "clip-not-original"}


def test_quote_source_route_marks_approved(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(status=status) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303 and calls["status"] == "approved"


def test_watch_channel_calls_flywheel(monkeypatch):
    called = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())

    def fake_watch(row):
        called["row"] = row
        return (True, "watching KXAN")

    monkeypatch.setattr(discovery, "watch_channel", fake_watch)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/watch-channel", follow_redirects=False)
    assert resp.status_code == 303 and "watching" in resp.headers["location"]


def test_new_form_prefills_from_query(monkeypatch, tmp_meetings_dir):
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    client = TestClient(create_app())
    resp = client.get("/new", params={
        "input": "https://www.youtube.com/watch?v=abc12345678",
        "date": "2026-08-01", "title": "Full debate", "event_kind": "debate",
        "meeting_type": "Debate", "race_id": "r1",
        "race_label": "TX · U.S. Senate · General · 2026", "event_orgs": "KXAN",
    })
    body = resp.text
    assert 'value="https://www.youtube.com/watch?v=abc12345678"' in body
    assert 'value="2026-08-01"' in body
    assert 'value="Full debate"' in body
    assert 'value="KXAN"' in body
    assert 'value="r1"' in body
    assert 'value="us-senate-tx-general"' in body
    assert "TX · U.S. Senate" in body
    import re
    chosen = re.search(r'<div class="race-chosen" id="f-race-chosen"([^>]*)>', body).group(1)
    assert "hidden" not in chosen


def test_new_form_race_chosen_hidden_when_not_prefilled(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/new")
    body = resp.text
    import re
    chosen = re.search(r'<div class="race-chosen" id="f-race-chosen"([^>]*)>', body).group(1)
    assert "hidden" in chosen


# --- I1: status guard on the four POST actions (double-click = double ingest) ---

def test_approve_ingest_blocks_non_pending_status(monkeypatch):
    import gui.batch as batch
    calls = {"enqueued": False}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(status="ingested"))
    monkeypatch.setattr(batch, "launch_or_enqueue",
                        lambda p: calls.update(enqueued=True) or ("started", "mid"))
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303
    assert "already" in _flash(resp)
    assert calls["enqueued"] is False


def test_quote_source_blocks_non_pending_status(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(status="rejected"))
    calls = {"set_status": False}
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(set_status=True) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303
    assert "already" in _flash(resp)
    assert calls["set_status"] is False


def test_reject_blocks_non_pending_status(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(status="approved"))
    calls = {"set_status": False}
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(set_status=True) or True)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject", data={"reason": "other"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "already" in _flash(resp)
    assert calls["set_status"] is False


# --- I2: surface set_status failures in the flash ---

def test_reject_flash_surfaces_save_failure(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status", lambda rid, status, reason=None: False)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject", data={"reason": "clip-not-original"},
                       follow_redirects=False)
    assert "SAVE FAILED" in _flash(resp)


def test_quote_source_flash_surfaces_save_failure(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status", lambda rid, status, reason=None: False)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert "SAVE FAILED" in _flash(resp)


def test_approve_ingest_flash_surfaces_save_failure(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    monkeypatch.setattr(discovery, "set_status", lambda rid, status, reason=None: False)
    monkeypatch.setattr(batch, "launch_or_enqueue", lambda p: ("started", "mid"))
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert "SAVE FAILED" in _flash(resp)


# --- I3: race-anchored items keep their race even when kind is a generic bucket ---

def test_approve_ingest_coerces_community_meeting_to_forum_when_race_set(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    launched = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(event_kind_guess="community_meeting"))
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(discovery, "set_status", lambda rid, status, reason=None: True)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    def fake_enqueue(p):
        launched["params"] = p
        return ("started", "mid")

    monkeypatch.setattr(batch, "launch_or_enqueue", fake_enqueue)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303
    p = launched["params"]
    assert p.event_kind == "forum"
    assert p.race_id == "r1"
    assert p.meeting_type == "Candidate Forum"


# --- M2: published_at renders as a date, not a raw timestamptz ---

def test_discovery_page_truncates_published_at_to_date(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda: [_row(published_at="2026-08-01 14:30:00+00")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    body = resp.text
    assert "2026-08-01" in body
    assert "14:30" not in body


# --- M5: scheme-filter r.url so an unsafe scheme never becomes an href ---

def test_discovery_page_blocks_unsafe_url_scheme(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda: [_row(url="javascript:alert(1)")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert 'href="javascript:' not in resp.text


# --- M11: missing branch coverage (monkeypatch-based, cheap) ---

def test_approve_ingest_none_kind_coerces_to_news_clip_with_race(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    launched = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(event_kind_guess=None))
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(discovery, "set_status", lambda rid, status, reason=None: True)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    def fake_enqueue(p):
        launched["params"] = p
        return ("started", "mid")

    monkeypatch.setattr(batch, "launch_or_enqueue", fake_enqueue)
    client = TestClient(create_app())
    client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    p = launched["params"]
    assert p.event_kind == "news_clip"
    assert p.race_id == "r1"


def test_approve_ingest_none_published_at_defaults_to_today(monkeypatch):
    import datetime as dt
    import gui.batch as batch
    import gui.runner as runner
    launched = {}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(published_at=None))
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(discovery, "set_status", lambda rid, status, reason=None: True)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    def fake_enqueue(p):
        launched["params"] = p
        return ("started", "mid")

    monkeypatch.setattr(batch, "launch_or_enqueue", fake_enqueue)
    client = TestClient(create_app())
    client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert launched["params"].date == dt.date.today().isoformat()


def test_approve_ingest_value_error_flashes_error_and_skips_status(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    calls = {"set_status": False}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(set_status=True) or True)

    def raise_value_error(p):
        raise ValueError("bad input")

    monkeypatch.setattr(batch, "launch_or_enqueue", raise_value_error)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert _flash(resp).startswith("error:")
    assert calls["set_status"] is False


@pytest.mark.parametrize("path", [
    "/discovery/d1/approve-ingest",
    "/discovery/d1/quote-source",
    "/discovery/d1/reject",
    "/discovery/d1/watch-channel",
])
def test_missing_row_404s_across_all_actions(monkeypatch, path):
    monkeypatch.setattr(discovery, "get_row", lambda rid: None)
    client = TestClient(create_app())
    resp = client.post(path, follow_redirects=False)
    assert resp.status_code == 404


# --- Task 5: health strip — last-run line + overdue pill ---

def test_health_defaults_include_last_run_keys_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    h = discovery.health()
    assert h["last_run"] is None
    assert h["scheduled_run_overdue"] is False


def test_discovery_page_renders_last_run_and_overdue(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [])
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [], raising=False)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": {"started_at": "2026-08-03 08:00:04", "finished_at": "2026-08-03 08:11:40",
                     "trigger": "scheduled", "examined": 120, "classified": 40,
                     "queued": 9, "capped": 0, "failures": 0, "running": False},
        "scheduled_run_overdue": True,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "last run 2026-08-03 08:00" in resp.text
    assert "no scheduled run in 36h" in resp.text


def test_discovery_page_shows_running_not_crashed_for_inflight_run(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [])
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [], raising=False)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": {"started_at": "2026-08-03 08:00:04", "finished_at": None,
                     "trigger": "scheduled", "examined": 0, "classified": 0,
                     "queued": 0, "capped": 0, "failures": 0, "running": True},
        "scheduled_run_overdue": False,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "running" in resp.text
    assert "CRASHED" not in resp.text


def test_discovery_page_reddens_pill_on_failures(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [])
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [], raising=False)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": {"started_at": "2026-08-03 08:00:04", "finished_at": "2026-08-03 08:11:40",
                     "trigger": "scheduled", "examined": 120, "classified": 40,
                     "queued": 9, "capped": 0, "failures": 4, "running": False},
        "scheduled_run_overdue": False,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "4 failure(s)" in resp.text
    assert "background:#c0392b" in resp.text   # a failing run must not render as a calm grey pill

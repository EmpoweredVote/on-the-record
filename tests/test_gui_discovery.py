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
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [_row()])
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
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
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
                        lambda status="pending": [_row(published_at="2026-08-01 14:30:00+00")])
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
                        lambda status="pending": [_row(url="javascript:alert(1)")])
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
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
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
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
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
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
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


def test_discovery_page_shows_crashed_for_stale_unfinished_run(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [], raising=False)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": {"started_at": "2026-08-01 08:00:04", "finished_at": None,
                     "trigger": "scheduled", "examined": 0, "classified": 0,
                     "queued": 0, "capped": 0, "failures": 0,
                     "skipped": 3, "prefiltered": 2, "recency": 1, "running": False},
        "scheduled_run_overdue": False,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "CRASHED" in resp.text
    assert "background:#c0392b" in resp.text


def test_discovery_page_healthy_run_stays_grey(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [], raising=False)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": {"started_at": "2026-08-03 08:00:04", "finished_at": "2026-08-03 08:11:40",
                     "trigger": "scheduled", "examined": 120, "classified": 40,
                     "queued": 9, "capped": 0, "failures": 0,
                     "skipped": 5, "prefiltered": 8, "recency": 2, "running": False},
        "scheduled_run_overdue": False,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "ok" in resp.text
    assert "background:#c0392b" not in resp.text


# --- Task 9: mode-C evidence surface — per-outlet stats + group pending counts ---

def test_outlet_stats_empty_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.outlet_stats() == []


def test_discovery_page_renders_outlet_evidence_and_group_counts(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [_row(), _row(id="d2")])
    # health() now carries outlet_stats itself (the perf fold) — the standalone
    # outlet_stats() must NOT be hit on this path.
    called = {"hit": False}
    monkeypatch.setattr(discovery, "outlet_stats",
                        lambda: called.update(hit=True) or [])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 2,
        "last_run": None, "scheduled_run_overdue": False,
        "outlet_stats": [
            {"name": "Fountainhead Forum", "reviewed": 2, "approved": 2, "identity_rejects": 0},
            {"name": "Milwaukee Journal Sentinel", "reviewed": 6, "approved": 0, "identity_rejects": 1},
        ],
        "outletless_reviewed": 9,
    })
    import gui.races as races
    monkeypatch.setattr(races, "race_labels", lambda ids: {"r1": "TX · U.S. Senate"})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "Fountainhead Forum" in resp.text
    assert "100%" in resp.text                       # 2/2 approved
    assert "Outlet evidence" in resp.text
    assert "<summary>" in resp.text and "<thead>" in resp.text
    assert "2 pending</span></h2>" in resp.text.replace("\n", "")
    assert "needs 4 more reviewed" in resp.text        # MJS: 6 reviewed, needs 10
    assert "needs 8 more reviewed" in resp.text        # Fountainhead: 2 reviewed, needs 10
    assert "9 reviewed item(s) have no outlet" in resp.text
    assert called["hit"] is False


def test_discovery_page_floors_approval_percent(monkeypatch):
    """89.7% must not round up to 90% next to a >=90% qualification bar."""
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": None, "scheduled_run_overdue": False,
        "outlet_stats": [
            {"name": "Big Outlet", "reviewed": 39, "approved": 35, "identity_rejects": 0},
        ],
        "outletless_reviewed": 0,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "35 (89%)" in resp.text
    assert "(90%)" not in resp.text   # copy elsewhere on the page legitimately says "90%"
    assert "below bar" in resp.text   # 35/39 = 89.7% < 90%, reviewed already >= 10


def test_discovery_page_qualifies_marker_for_a_bar_clearing_outlet(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": None, "scheduled_run_overdue": False,
        "outlet_stats": [
            {"name": "Great Outlet", "reviewed": 10, "approved": 10, "identity_rejects": 0},
        ],
        "outletless_reviewed": 0,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "&#10003; qualifies" in resp.text


def test_discovery_page_below_bar_when_approval_rate_too_low(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": None, "scheduled_run_overdue": False,
        "outlet_stats": [
            {"name": "Shaky Outlet", "reviewed": 10, "approved": 5, "identity_rejects": 0},
        ],
        "outletless_reviewed": 0,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert "below bar" in resp.text


def test_discovery_page_uses_health_outlet_stats_when_key_present(monkeypatch):
    """The perf fold: outlet_stats() must not be called when health() already
    carries the key (the real DB path after this fold)."""
    called = {"hit": False}
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "outlet_stats",
                        lambda: called.update(hit=True) or [])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": None, "scheduled_run_overdue": False,
        "outlet_stats": [], "outletless_reviewed": 0,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert called["hit"] is False


def test_discovery_page_falls_back_to_outlet_stats_when_key_absent(monkeypatch):
    """Legacy/monkeypatched health() dicts without the key still work by
    falling back to the standalone outlet_stats() call."""
    called = {"hit": False}
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [])
    monkeypatch.setattr(discovery, "outlet_stats",
                        lambda: called.update(hit=True) or [])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": None, "scheduled_run_overdue": False,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert called["hit"] is True


# --- Task 12: extractability probe on approve->ingest for non-YouTube items ---

def test_approve_ingest_probes_non_youtube_and_bounces_on_failure(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    row = _row(url="https://www.kctv5.com/2026/08/01/governor-debate/")
    monkeypatch.setattr(discovery, "get_row", lambda rid: row)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    monkeypatch.setattr(discovery, "probe_extractable",
                        lambda url: (False, "Unsupported URL"))
    launched = []
    monkeypatch.setattr(batch, "launch_or_enqueue",
                        lambda params: launched.append(params) or ("queued", "m1"))
    statuses = []
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: statuses.append(status) or True)
    client = TestClient(create_app(), follow_redirects=False)
    resp = client.post("/discovery/d1/approve-ingest")
    assert resp.status_code == 303
    assert "use Edit first" in _flash(resp)
    assert launched == [] and statuses == []      # nothing enqueued, still pending


def test_approve_ingest_skips_probe_for_youtube(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    row = _row()                                   # default _row url is YouTube
    monkeypatch.setattr(discovery, "get_row", lambda rid: row)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    probed = []
    monkeypatch.setattr(discovery, "probe_extractable",
                        lambda url: probed.append(url) or (True, ""))
    monkeypatch.setattr(batch, "launch_or_enqueue", lambda params: ("queued", "m1"))
    monkeypatch.setattr(discovery, "set_status", lambda rid, s, reason=None: True)
    client = TestClient(create_app(), follow_redirects=False)
    resp = client.post("/discovery/d1/approve-ingest")
    assert resp.status_code == 303
    assert probed == []                            # YouTube: no probe spent


def test_approve_ingest_enqueues_when_probe_succeeds_for_non_youtube(monkeypatch):
    """The happy path for a non-YouTube row: probe says extractable -> the
    row still enqueues and lands 'ingested', same as the YouTube path."""
    import gui.batch as batch
    import gui.runner as runner
    row = _row(url="https://www.kctv5.com/2026/08/01/governor-debate/")
    monkeypatch.setattr(discovery, "get_row", lambda rid: row)
    monkeypatch.setattr(discovery, "race_slug_for", lambda rid: "us-senate-tx-general")
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    monkeypatch.setattr(discovery, "probe_extractable", lambda url: (True, ""))
    launched = {}

    def fake_enqueue(p):
        launched["params"] = p
        return ("started", "mid")

    monkeypatch.setattr(batch, "launch_or_enqueue", fake_enqueue)
    statuses = []
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: statuses.append(status) or True)
    client = TestClient(create_app(), follow_redirects=False)
    resp = client.post("/discovery/d1/approve-ingest")
    assert resp.status_code == 303
    assert launched["params"].input == row.url
    assert statuses == ["ingested"]


# --- Task 12 follow-up: probe/downloader parity — probe_extractable unit tests ---

def _stub_ydl(monkeypatch, result=None, raise_exc=None):
    """Stub yt_dlp.YoutubeDL so probe_extractable's `import yt_dlp` sees a
    fake extractor instead of hitting the network."""
    import yt_dlp

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            if raise_exc is not None:
                raise raise_exc
            return result

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)


def test_probe_extractable_false_on_extractor_error(monkeypatch):
    _stub_ydl(monkeypatch, raise_exc=Exception("Unsupported URL: " + "x" * 250))
    ok, err = discovery.probe_extractable("https://station.example.com/embed/x")
    assert ok is False
    assert len(err) <= 200
    assert err.startswith("Unsupported URL:")


def test_probe_extractable_true_when_formats_present(monkeypatch):
    _stub_ydl(monkeypatch, result={"formats": [{"url": "https://cdn.example.com/x.mp4"}]})
    ok, err = discovery.probe_extractable("https://station.example.com/embed/x")
    assert ok is True
    assert err == ""


def test_probe_extractable_false_when_all_entries_falsy(monkeypatch):
    _stub_ydl(monkeypatch, result={"entries": [None, None]})
    ok, err = discovery.probe_extractable("https://station.example.com/embed/x")
    assert ok is False
    assert err


def test_probe_extractable_skips_ytdlp_for_resolver_owned_url(monkeypatch):
    """Podcast/Brightspot pages resolve without yt-dlp at ingest time — the
    probe must not spend a yt-dlp attempt (or bounce) on them."""
    touched = {"hit": False}
    import yt_dlp

    class _Boom:
        def __init__(self, opts):
            touched["hit"] = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            touched["hit"] = True
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _Boom)
    import src.resolve as resolve_mod
    from src.resolve import ResolvedSource
    monkeypatch.setattr(
        resolve_mod, "resolve_source",
        lambda url, **kw: ResolvedSource(audio_url="https://cdn.example.com/ep.mp3",
                                          resolver="podcast"))
    ok, err = discovery.probe_extractable("https://show.example.com/ep-1")
    assert ok is True and err == ""
    assert touched["hit"] is False


def test_probe_extractable_skips_ytdlp_for_hls_url(monkeypatch):
    touched = {"hit": False}
    import yt_dlp

    class _Boom:
        def __init__(self, opts):
            touched["hit"] = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            touched["hit"] = True
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _Boom)
    ok, err = discovery.probe_extractable("https://cdn.example.com/east/manifest.m3u8")
    assert ok is True and err == ""
    assert touched["hit"] is False


def test_probe_extractable_falls_through_to_ytdlp_when_resolver_errors(monkeypatch):
    """A resolver bug/exception must not crash the probe — it should just
    fall through to the yt-dlp attempt."""
    import src.resolve as resolve_mod

    def _boom(url, **kw):
        raise RuntimeError("resolver blew up")

    monkeypatch.setattr(resolve_mod, "resolve_source", _boom)
    _stub_ydl(monkeypatch, result={"formats": [{"url": "https://cdn.example.com/x.mp4"}]})
    ok, err = discovery.probe_extractable("https://station.example.com/embed/x")
    assert ok is True and err == ""


# --- Task 4: pending queue orders by tier before confidence ---

def test_pending_order_ranks_tier_before_confidence():
    order = discovery._LIST_WHERE_ORDER
    assert "election_date asc" in order
    tier_pos = order.index("source_tier_guess asc")
    conf_pos = order.index("confidence desc")
    assert tier_pos < conf_pos


# --- Task 4: deferred-view toggle ---

def test_discovery_page_deferred_view_lists_deferred(monkeypatch):
    seen = {}
    def fake_rows(status="pending"):
        seen["status"] = status
        return [_row(status=status)]
    monkeypatch.setattr(discovery, "pending_rows", fake_rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery?show=deferred")
    assert resp.status_code == 200
    assert seen["status"] == "deferred"


# --- Task 5: bulk status change touches only pending/deferred rows ---

def test_set_status_bulk_updates_only_pending_or_deferred(monkeypatch):
    captured = {}
    class _Cur:
        rowcount = 2
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): captured["committed"] = True
        def close(self): pass
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())

    n = discovery.set_status_bulk(["a", "b"], "rejected", reason="tier-3")
    assert n == 2
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "id = any(%s::uuid[])" in sql
    assert "status = any(array['pending','deferred'])" in sql
    assert discovery.set_status_bulk([], "rejected", reason="x") == 0   # empty is a no-op

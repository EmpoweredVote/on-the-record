from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from gui.app import create_app
import gui.coverage as coverage
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


def _race(race_id="r1", position_name="U.S. Senate", level="state", locality=None,
         candidates=0, quote_sources=0, ingested=0, pending=1):
    return coverage.RaceCoverage(race_id, position_name, level, locality,
                                 candidates, quote_sources, ingested, pending)


def test_thumb_and_duration_properties():
    r = _row()
    assert r.thumb_url == "https://i.ytimg.com/vi/abc12345678/mqdefault.jpg"
    assert r.duration_label == "58m"
    assert _row(duration_seconds=5460).duration_label == "1h31m"
    assert _row(duration_seconds=None).duration_label == "?"
    assert _row(url="https://x.example/ep/1").thumb_url is None


def test_discovery_state_view_renders_row_details_and_alarms(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state",
                        lambda state: [_race(position_name="U.S. Senate")])
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [_row()])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [("r9", "MI Governor (D primary)", "2026-08-04")],
        "stale_outlets": ["PBS Kansas"], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery?state=TX")
    assert resp.status_code == 200
    body = resp.text
    assert "Full debate" in body
    assert "U.S. Senate" in body
    assert "MI Governor (D primary)" in body       # alarm strip (state-agnostic)
    assert "58-min video" in body                   # the classifier's why
    assert "watch this channel" in body.lower()     # flywheel offer (no outlet_id)


def test_discovery_page_empty_state_no_states_tracked(monkeypatch):
    monkeypatch.setattr(coverage, "state_index", lambda: [])
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "No tracked races yet" in resp.text


def test_discovery_page_empty_state_for_state_with_no_races(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [])
    client = TestClient(create_app())
    resp = client.get("/discovery?state=WY")
    assert resp.status_code == 200
    assert "No tracked races in Wyoming" in resp.text


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


def test_quote_source_route_approves_family_and_reports_count(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row",
                        lambda rid: _row(channel_name="Wisconsin PBS"))
    monkeypatch.setattr(discovery, "approve_source_family",
                        lambda row: calls.update(row=row) or 6)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303
    assert calls["row"].id == "d1"
    assert "approved 6 (Wisconsin PBS)" in _flash(resp)


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
    called = {"fanned": False}
    monkeypatch.setattr(discovery, "approve_source_family",
                        lambda row: called.update(fanned=True) or 1)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert resp.status_code == 303
    assert "already rejected" in _flash(resp)
    assert called["fanned"] is False


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
    monkeypatch.setattr(discovery, "approve_source_family", lambda row: 0)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/quote-source", follow_redirects=False)
    assert "approved as quote source — SAVE FAILED, retry" in _flash(resp)


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
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race()])
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda status="pending": [_row(published_at="2026-08-01 14:30:00+00")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery?state=TX")
    body = resp.text
    assert "2026-08-01" in body
    assert "14:30" not in body


# --- M5: scheme-filter r.url so an unsafe scheme never becomes an href ---

def test_discovery_page_blocks_unsafe_url_scheme(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race()])
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda status="pending": [_row(url="javascript:alert(1)")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    resp = client.get("/discovery?state=TX")
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


# --- Task 9: mode-C evidence surface (outlet_stats itself; no longer rendered
# on /discovery — see Task 7's report for why: frictionless per-outlet trust,
# shown in place inside each race, supersedes the old queue-wide evidence
# table for this page's purposes. outlet_stats() itself is untouched and still
# tested here.) ---

def test_outlet_stats_empty_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.outlet_stats() == []


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


# --- Task 4's deferred-view toggle (?show=deferred) and Task 7's page-wide
# checkbox bulk bar both no longer exist: Task 7's reorg reads only `state`/
# `flash` (see task-7-brief.md's Layout section) and nests review three levels
# deep (state -> race -> outlet), which has no natural page-wide multi-select.
# discovery.set_status_bulk and POST /discovery/bulk are untouched and still
# fully tested below — only this page stopped rendering a UI control for them.

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


# --- Task 6: POST /discovery/bulk ---

def test_bulk_reject_calls_set_status_bulk_with_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, "set_status_bulk",
                        lambda ids, status, reason=None: calls.append((ids, status, reason)) or len(ids))
    client = TestClient(create_app())
    resp = client.post("/discovery/bulk",
                       data={"action": "reject", "row_ids": ["a", "b"], "reason": "tier-5"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls == [(["a", "b"], "rejected", "tier-5")]
    assert "rejected 2" in _flash(resp)


def test_bulk_restore_sets_pending(monkeypatch):
    calls = []
    monkeypatch.setattr(discovery, "set_status_bulk",
                        lambda ids, status, reason=None: calls.append((ids, status, reason)) or len(ids))
    client = TestClient(create_app())
    resp = client.post("/discovery/bulk",
                       data={"action": "restore", "row_ids": ["a"]},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls == [(["a"], "pending", None)]
    assert "restored 1" in _flash(resp)


def test_bulk_no_rows_is_a_noop(monkeypatch):
    monkeypatch.setattr(discovery, "set_status_bulk",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))
    client = TestClient(create_app())
    resp = client.post("/discovery/bulk", data={"action": "reject"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "no rows selected" in _flash(resp)



# --- Approve source family: matching key ---

def test_family_key_prefers_outlet_id():
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001",
             channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery.family_key(r) == ("outlet", "00000000-0000-0000-0000-000000000001")


def test_family_key_falls_back_to_channel_id():
    r = _row(outlet_id=None, channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery.family_key(r) == ("channel", "UCk")


def test_family_key_falls_back_to_normalized_name():
    r = _row(outlet_id=None, channel_id=None, channel_name="  Wisconsin PBS ")
    assert discovery.family_key(r) == ("name", "wisconsin pbs")


def test_family_key_none_when_no_identity():
    r = _row(outlet_id=None, channel_id=None, channel_name=None)
    assert discovery.family_key(r) is None


def test_family_key_none_when_name_blank():
    r = _row(outlet_id=None, channel_id=None, channel_name="   ")
    assert discovery.family_key(r) is None


def test_discovered_row_has_family_count_default_zero():
    assert _row().family_count == 0


# --- Approve source family: DB action ---

def _capture_conn(monkeypatch, rowcount=1):
    captured = {}
    class _Cur:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
        @property
        def rowcount(self):
            return rowcount
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class _Conn:
        def cursor(self): return _Cur()
        def commit(self): captured["committed"] = True
        def close(self): pass
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())
    return captured


def test_approve_family_by_outlet_id(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=6)
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001")
    n = discovery.approve_source_family(r)
    assert n == 6
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "status = 'approved'" in sql
    assert "status = 'pending'" in sql
    assert "outlet_id = %s::uuid" in sql
    assert captured["params"] == ("00000000-0000-0000-0000-000000000001",)


def test_approve_family_by_channel_id(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=3)
    r = _row(outlet_id=None, channel_id="UCk")
    assert discovery.approve_source_family(r) == 3
    sql = captured["sql"].lower()
    assert "channel_id = %s" in sql
    assert captured["params"] == ("UCk",)


def test_approve_family_by_name(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=2)
    r = _row(outlet_id=None, channel_id=None, channel_name="Wisconsin PBS")
    assert discovery.approve_source_family(r) == 2
    sql = captured["sql"].lower()
    assert "lower(btrim(channel_name)) = %s" in sql
    assert captured["params"] == ("wisconsin pbs",)


def test_approve_family_keyless_updates_only_self(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=1)
    r = _row(id="d9", outlet_id=None, channel_id=None, channel_name=None)
    assert discovery.approve_source_family(r) == 1
    sql = captured["sql"].lower()
    assert "id = %s::uuid" in sql
    assert captured["params"] == ("d9",)


def test_approve_family_returns_zero_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.approve_source_family(_row()) == 0


# --- Reject source family: DB action ---

def test_reject_family_by_outlet_sets_reason(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=4)
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001")
    n = discovery.reject_source_family(r, "tier-5")
    assert n == 4
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "status = 'rejected'" in sql
    assert "status_reason = %s" in sql
    assert "status = 'pending'" in sql
    assert "outlet_id = %s::uuid" in sql
    assert captured["params"] == ("tier-5", "00000000-0000-0000-0000-000000000001")


def test_reject_family_by_name(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=2)
    r = _row(outlet_id=None, channel_id=None, channel_name="Wisconsin PBS")
    assert discovery.reject_source_family(r, "stale") == 2
    sql = captured["sql"].lower()
    assert "lower(btrim(channel_name)) = %s" in sql
    assert captured["params"] == ("stale", "wisconsin pbs")


def test_reject_family_keyless_updates_only_self(monkeypatch):
    captured = _capture_conn(monkeypatch, rowcount=1)
    r = _row(id="d9", outlet_id=None, channel_id=None, channel_name=None)
    assert discovery.reject_source_family(r, "other") == 1
    sql = captured["sql"].lower()
    assert "id = %s::uuid" in sql
    assert captured["params"] == ("other", "d9")


def test_reject_family_returns_zero_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.reject_source_family(_row(), "tier-5") == 0


# --- Approve source family: button count on the page ---
#
# family_count is whole-queue (see approve_source_family/reject_source_family's
# own scope), so three rows sharing a channel across three different races each
# still see "+2 more" once every race is attached under the selected state.

def test_quote_source_button_shows_sibling_count(monkeypatch):
    rows = [_row(id="a", channel_id="UCw", channel_name="Wisconsin PBS", race_id="r1"),
            _row(id="b", channel_id="UCw", channel_name="Wisconsin PBS", race_id="r2"),
            _row(id="c", channel_id="UCw", channel_name="Wisconsin PBS", race_id="r3")]
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [
        _race("r1"), _race("r2"), _race("r3")])
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 3})
    client = TestClient(create_app())
    html = client.get("/discovery?state=WI").text
    # three rows share a channel → each button offers "+2 more" (siblings across races)
    assert "(+2 more)" in html


def test_quote_source_button_plain_for_loner(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race()])
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda status="pending": [_row(id="a", channel_id="UCsolo")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 1})
    client = TestClient(create_app())
    html = client.get("/discovery?state=TX").text
    assert "Approve &rarr; quote source</button>" in html or \
           "Approve → quote source</button>" in html
    assert "more)" not in html


# --- Reject source family: shared WHERE helper ---

def test_family_where_outlet():
    r = _row(outlet_id="00000000-0000-0000-0000-000000000001",
             channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery._family_where(r) == (
        "outlet_id = %s::uuid", "00000000-0000-0000-0000-000000000001")


def test_family_where_channel():
    r = _row(outlet_id=None, channel_id="UCk", channel_name="Wisconsin PBS")
    assert discovery._family_where(r) == ("channel_id = %s", "UCk")


def test_family_where_name():
    r = _row(outlet_id=None, channel_id=None, channel_name="  Wisconsin PBS ")
    assert discovery._family_where(r) == (
        "lower(btrim(channel_name)) = %s", "wisconsin pbs")


def test_family_where_keyless_uses_id():
    r = _row(id="d9", outlet_id=None, channel_id=None, channel_name=None)
    assert discovery._family_where(r) == ("id = %s::uuid", "d9")


# --- Reject source family: route + checkbox ---

def test_reject_whole_source_fans_out_and_reports_count(monkeypatch):
    calls = {}
    monkeypatch.setattr(discovery, "get_row",
                        lambda rid: _row(channel_name="Wisconsin PBS"))
    monkeypatch.setattr(discovery, "reject_source_family",
                        lambda row, reason: calls.update(row=row, reason=reason) or 4)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject",
                       data={"reason": "tier-5", "whole_source": "1"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert calls["row"].id == "d1" and calls["reason"] == "tier-5"
    assert "rejected 4 (Wisconsin PBS)" in _flash(resp)


def test_reject_single_row_when_checkbox_absent(monkeypatch):
    calls = {"family": False}
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row())
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: calls.update(
                            status=status, reason=reason) or True)
    monkeypatch.setattr(discovery, "reject_source_family",
                        lambda row, reason: calls.update(family=True) or 9)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject",
                       data={"reason": "clip-not-original"}, follow_redirects=False)
    assert resp.status_code == 303
    assert calls["family"] is False
    assert calls["status"] == "rejected" and calls["reason"] == "clip-not-original"
    assert "rejected" in _flash(resp)


def test_reject_whole_source_blocks_non_pending(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(status="approved"))
    called = {"family": False}
    monkeypatch.setattr(discovery, "reject_source_family",
                        lambda row, reason: called.update(family=True) or 1)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/reject",
                       data={"reason": "tier-5", "whole_source": "1"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "already approved" in _flash(resp)
    assert called["family"] is False


def test_reject_checkbox_shows_only_with_siblings(monkeypatch):
    rows = [_row(id="a", channel_id="UCw"), _row(id="b", channel_id="UCw"),
            _row(id="c", channel_id="UConly")]
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race()])
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": rows)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 3})
    client = TestClient(create_app())
    html = client.get("/discovery?state=TX").text
    assert 'name="whole_source"' in html          # the two UCw rows have a sibling
    assert "apply to all 2 from this source" in html


# --- Task 7: reorganized /discovery — state index, state view, trust route,
# barred-ingest gate ---

def test_discovery_defaults_to_state_index(monkeypatch):
    monkeypatch.setattr(coverage, "state_index", lambda: [{"state": "IN", "pending": 42}])
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "Indiana" in resp.text  # a state from the index, shown by full name


def test_discovery_page_state_index_shows_pending_counts_and_links(monkeypatch):
    monkeypatch.setattr(coverage, "state_index", lambda: [
        {"state": "IN", "pending": 42}, {"state": "TX", "pending": 5}])
    client = TestClient(create_app())
    body = client.get("/discovery").text
    assert 'href="/discovery?state=IN"' in body
    assert "42 pending" in body
    assert "Texas" in body and "5 pending" in body


def test_discovery_state_view_groups_by_level(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [
        _race("r-gov", "Indiana Governor", "state", None, 3, 2, 1, 0),
        _race("r-may", "Bloomington Mayor", "local",
              "City of Bloomington, Indiana", 2, 1, 0, 1),
    ])
    client = TestClient(create_app())
    resp = client.get("/discovery?state=IN")
    assert resp.status_code == 200
    body = resp.text
    assert "Indiana Governor" in body
    assert "Bloomington Mayor" in body
    # statewide/federal band label present
    assert "tracked once" in body.lower()


def test_discovery_left_rail_shows_locality_counts_and_done_check(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [
        _race("r1", "Bloomington Mayor", "local",
              "City of Bloomington, Indiana", 2, 1, 0, 3),
        _race("r2", "Ellettsville Town Council", "local",
              "Town of Ellettsville, Indiana", 1, 1, 0, 0),
    ])
    client = TestClient(create_app())
    body = client.get("/discovery?state=IN").text
    assert "City of Bloomington, Indiana" in body
    assert "3 pending" in body
    assert "Town of Ellettsville, Indiana" in body
    assert "&#10003;" in body   # Ellettsville has 0 pending -> a done check, not a count


def test_discovery_race_row_shows_four_counts(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [
        _race("r1", "Indiana Governor", "state", None, 5, 4, 2, 7)])
    client = TestClient(create_app())
    body = client.get("/discovery?state=IN").text
    assert "5 candidates" in body
    assert "4 quote sources" in body
    assert "2 ingested" in body
    assert "7 pending" in body


def test_discovery_state_view_shows_trust_button_for_unknown_outlet(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race("r1")])
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [
        _row(id="d1", race_id="r1", channel_name="Random Blog", outlet_id=None,
            channel_id=None, event_kind_guess="other")])
    client = TestClient(create_app())
    body = client.get("/discovery?state=TX").text
    assert "Trust outlet" in body
    assert 'action="/discovery/d1/trust"' in body
    assert "auto-kept as quote sources" not in body


def test_discovery_state_view_mutes_trusted_outlets_news_clips(monkeypatch):
    """A trusted outlet's news_clip-lane rows collapse to a muted count with no
    per-row controls — content_lane('clip', 'other') is news_clip since 'other'
    isn't a FORMAL_EVENT_KIND."""
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race("r1")])
    row = _row(id="d1", race_id="r1", channel_name="WISH-TV",
              outlet_id="00000000-0000-0000-0000-000000000001",
              original_vs_clip="clip", event_kind_guess="other", outlet_trusted=True)
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [row])
    client = TestClient(create_app())
    body = client.get("/discovery?state=TX").text
    assert "1 auto-kept as quote sources" in body
    assert "Trust outlet" not in body
    assert "Approve &rarr; ingest" not in body   # a muted row carries no per-row controls


def test_discovery_state_view_disables_ingest_button_for_barred_outlet(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race("r1")])
    row = _row(id="d1", race_id="r1", channel_name="Nexstar Station",
              outlet_ingest_barred=True)
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [row])
    client = TestClient(create_app())
    body = client.get("/discovery?state=TX").text
    assert '<button type="submit" class="enroll" disabled' in body
    assert "chain ToS: pull a direct quote instead" in body


def test_trust_route_sweeps(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(id="d1"))
    monkeypatch.setattr(discovery, "trust_from_row",
                        lambda row: (True, "trusted KXAN", 3))
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/trust", follow_redirects=False)
    assert resp.status_code == 303
    assert "trusted" in resp.headers["location"].lower()
    assert "trusted kxan — auto-kept 3" in _flash(resp).lower()


def test_trust_route_404_for_missing_row(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: None)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/trust", follow_redirects=False)
    assert resp.status_code == 404


def test_trust_route_reports_failure_message(monkeypatch):
    monkeypatch.setattr(discovery, "get_row", lambda rid: _row(id="d1"))
    monkeypatch.setattr(discovery, "trust_from_row",
                        lambda row: (False, "no channel id on this item", 0))
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/trust", follow_redirects=False)
    assert resp.status_code == 303
    assert "trust failed: no channel id on this item" in _flash(resp)


def test_ingest_blocked_for_barred_outlet(monkeypatch):
    import gui.batch as batch
    monkeypatch.setattr(discovery, "get_row",
                        lambda rid: _row(id="d1", outlet_ingest_barred=True))
    called = {"hit": False}
    monkeypatch.setattr(batch, "launch_or_enqueue",
                        lambda p: called.update(hit=True) or ("started", "mid"))
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/approve-ingest", follow_redirects=False)
    assert resp.status_code == 303
    # The redirect's flash is a whole phrase with spaces/punctuation, so it
    # comes back %-encoded in the raw Location header (e.g. "chain%20ToS%3A");
    # decode via _flash() rather than substring-matching the raw header.
    assert "chain tos" in _flash(resp).lower()
    assert called["hit"] is False


# --- Task 5: outlet trust/undo DB layer ---
#
# No local/seeded discovery DB exists (see tests/conftest.py's _no_real_db_env
# and live_db fixtures) — these follow the project's established three-tier
# strategy: (1) pure SQL-fragment checks, (2) fake-cursor injection for SQL
# construction (mirrors _capture_conn above), (3) best-effort no-DB checks.
# No live_db test here: essentials.source_outlets.trusted/ingest_barred only
# exist once the Task 1 migration is applied (gated on Chris, not yet done),
# so a live query against them today could only fail, not confirm anything.

# The critical trap this task's brief called out by name: _to_row does
# `DiscoveredRow(*r)` — positional. _SELECT's two new columns must land
# immediately after election_date and before race_label/family_count, or a
# get_row()/pending_rows() call silently loads o.trusted into race_label.


def test_select_joins_outlet_trust_flags():
    sql = discovery._SELECT.lower()
    assert "coalesce(o.trusted, false)" in sql
    assert "coalesce(o.ingest_barred, false)" in sql
    assert "left join essentials.source_outlets o on o.id = d.outlet_id" in sql
    # The join must come after election_date in the column list, matching
    # DiscoveredRow's field order (see the alignment test below).
    assert sql.index("election_date") < sql.index("coalesce(o.trusted")


def test_select_appends_original_vs_clip_as_last_column():
    """Task 5b: d.original_vs_clip must be the LAST _SELECT column (appended
    after the outlet-trust flags), keeping positional _to_row(*r) alignment
    simplest — see the alignment test below."""
    sql = discovery._SELECT.lower()
    assert "d.original_vs_clip" in sql
    assert sql.index("coalesce(o.ingest_barred") < sql.index("d.original_vs_clip")


def test_discovered_row_outlet_flags_default_false():
    r = _row()
    assert r.outlet_trusted is False
    assert r.outlet_ingest_barred is False


def test_get_row_maps_outlet_flags_without_misaligning_family_fields(monkeypatch):
    """The alignment guard: feed a full 22-column row through get_row() (the
    real _SELECT -> _to_row -> DiscoveredRow(*r) path) and confirm the two
    outlet-trust columns AND the trailing original_vs_clip column (Task 5b)
    land on outlet_trusted/outlet_ingest_barred/original_vs_clip — NOT on
    race_label/family_count, which must stay at their dataclass defaults since
    _SELECT never supplies them."""
    row_tuple = (
        "d1", "https://www.youtube.com/watch?v=abc12345678", "Title", "desc",
        "Channel", "UCabc", "https://example.com/chan",
        "00000000-0000-0000-0000-000000000001", 600, "2026-08-01", "r1",
        "news_clip", 2, "quote_source", 0.5, "why", "search", "pending",
        "2026-11-03",
        True, False,   # coalesce(o.trusted, false), coalesce(o.ingest_barred, false)
        "clip",        # d.original_vs_clip
    )
    assert len(row_tuple) == 22  # _SELECT's exact column count today

    class _Cur:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return row_tuple

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())

    row = discovery.get_row("d1")
    assert row.election_date == "2026-11-03"
    assert row.outlet_trusted is True
    assert row.outlet_ingest_barred is False
    assert row.original_vs_clip == "clip"
    # The trap: these must stay defaulted, never receive o.trusted/o.ingest_barred.
    assert row.race_label is None
    assert row.family_count == 0


# --- Task 5: set_outlet_trusted ---

def test_set_outlet_trusted_sql(monkeypatch):
    captured = _capture_conn(monkeypatch)
    ok = discovery.set_outlet_trusted("00000000-0000-0000-0000-000000000001")
    assert ok is True
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.source_outlets" in sql
    assert "trusted = true" in sql
    assert "trusted_at = now()" in sql
    assert "id = %s::uuid" in sql
    assert captured["params"] == ("00000000-0000-0000-0000-000000000001",)


def test_set_outlet_trusted_no_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.set_outlet_trusted("x") is False


# --- Task 5: _outlet_id_for_channel ---

def test_outlet_id_for_channel_sql(monkeypatch):
    captured = {}

    class _Cur:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchone(self):
            return ("00000000-0000-0000-0000-000000000009",)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())
    outlet_id = discovery._outlet_id_for_channel("UCabc")
    assert outlet_id == "00000000-0000-0000-0000-000000000009"
    sql = captured["sql"].lower()
    assert "source_outlets" in sql
    assert "external_channel_id = %s" in sql
    assert captured["params"] == ("UCabc",)


def test_outlet_id_for_channel_none_when_not_found(monkeypatch):
    class _Cur:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())
    assert discovery._outlet_id_for_channel("UCabc") is None


def test_outlet_id_for_channel_no_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery._outlet_id_for_channel("UCabc") is None


def test_outlet_id_for_channel_blank_channel_id_short_circuits(monkeypatch):
    called = {"connect": False}
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect",
                        lambda url: called.update(connect=True))
    assert discovery._outlet_id_for_channel(None) is None
    assert called["connect"] is False


# --- Task 5: trust_from_row ---

def _fake_conn_for_sweep(monkeypatch, n=1):
    """Minimal fake connect for the auto_approve_pending leg of trust_from_row
    (a plain UPDATE...rowcount, no fetch needed)."""
    class _Cur:
        def execute(self, sql, params=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: _Conn())
    import src.discovery.autoapprove as autoapprove
    monkeypatch.setattr(autoapprove, "auto_approve_pending",
                        lambda cur, outlet_id=None: n)


def test_trust_from_row_with_outlet_id_sweeps_and_returns_count(monkeypatch):
    row = _row(outlet_id="00000000-0000-0000-0000-000000000001", channel_name="KXAN")
    monkeypatch.setattr(discovery, "set_outlet_trusted", lambda oid: True)
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    _fake_conn_for_sweep(monkeypatch, n=5)
    ok, msg, n = discovery.trust_from_row(row)
    assert ok is True
    assert n == 5
    assert "KXAN" in msg


def test_trust_from_row_channel_only_registers_then_trusts(monkeypatch):
    row = _row(outlet_id=None, channel_id="UCk", channel_name="KXAN")
    watched = {"called": False}
    monkeypatch.setattr(discovery, "watch_channel",
                        lambda r: watched.update(called=True) or (True, "watching KXAN"))
    monkeypatch.setattr(discovery, "_outlet_id_for_channel",
                        lambda cid: "00000000-0000-0000-0000-000000000002")
    monkeypatch.setattr(discovery, "set_outlet_trusted", lambda oid: True)
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    _fake_conn_for_sweep(monkeypatch, n=2)
    ok, msg, n = discovery.trust_from_row(row)
    assert watched["called"] is True
    assert ok is True
    assert n == 2


def test_trust_from_row_channel_only_watch_fails(monkeypatch):
    row = _row(outlet_id=None, channel_id="UCk")
    monkeypatch.setattr(discovery, "watch_channel", lambda r: (False, "boom"))
    ok, msg, n = discovery.trust_from_row(row)
    assert ok is False
    assert n == 0
    assert "register" in msg


def test_trust_from_row_channel_only_outlet_not_found_after_register(monkeypatch):
    row = _row(outlet_id=None, channel_id="UCk")
    monkeypatch.setattr(discovery, "watch_channel", lambda r: (True, "watching"))
    monkeypatch.setattr(discovery, "_outlet_id_for_channel", lambda cid: None)
    ok, msg, n = discovery.trust_from_row(row)
    assert ok is False
    assert n == 0
    assert "not found" in msg


def test_trust_from_row_set_trusted_fails(monkeypatch):
    row = _row(outlet_id="00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(discovery, "set_outlet_trusted", lambda oid: False)
    ok, msg, n = discovery.trust_from_row(row)
    assert ok is False
    assert n == 0


def test_trust_from_row_no_db_with_outlet_id(monkeypatch):
    """DATABASE_URL unset -> set_outlet_trusted degrades to False (its own
    real best-effort code path, not mocked here) -> trust_from_row degrades
    safely instead of raising."""
    row = _row(outlet_id="00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    ok, msg, n = discovery.trust_from_row(row)
    assert ok is False
    assert n == 0


def test_trust_from_row_no_db_channel_only(monkeypatch):
    row = _row(outlet_id=None, channel_id="UCk")
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    ok, msg, n = discovery.trust_from_row(row)
    assert ok is False
    assert n == 0


# --- Task 5: unapprove_auto ---

def test_unapprove_auto_only_auto_rows(monkeypatch):
    """The WHERE must restrict to status='approved' AND status_reason LIKE
    'auto:%' — a human-approved row (no auto: reason) is never touched."""
    captured = _capture_conn(monkeypatch, rowcount=2)
    n = discovery.unapprove_auto(["a", "b"])
    assert n == 2
    assert captured["committed"] is True
    sql = captured["sql"].lower()
    assert "update essentials.discovered_sources" in sql
    assert "status = 'pending'" in sql
    assert "id = any(%s::uuid[])" in sql
    assert "status = 'approved'" in sql
    assert "status_reason like 'auto:%" in sql
    assert captured["params"] == (["a", "b"],)


def test_unapprove_auto_empty_is_noop(monkeypatch):
    called = {"connect": False}
    monkeypatch.setattr(discovery, "_db_url", lambda: "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect",
                        lambda url: called.update(connect=True))
    assert discovery.unapprove_auto([]) == 0
    assert called["connect"] is False


def test_unapprove_auto_no_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.unapprove_auto(["a"]) == 0


# --- Task 5: live_db-gated shape checks ---
#
# Only for the two new functions whose SQL touches columns that already exist
# in prod today (discovered_sources.status/status_reason/reviewed_at,
# source_outlets.id/external_channel_id). set_outlet_trusted, trust_from_row
# and auto_approve_pending all read/write source_outlets.trusted/ingest_barred,
# which exist only after the Task 1 migration (ev-accounts 37313d43) is
# applied — gated on Chris, not done yet — so a live test for those would
# either error (column does not exist) or be meaningless; deliberately
# omitted here rather than shipped red. Both tests below touch zero real rows
# even if they do run (a random UUID / a channel id that doesn't exist), so
# they're safe to leave enabled once someone does export DATABASE_URL.

def test_unapprove_auto_live_db_shape(live_db):
    n = discovery.unapprove_auto(["00000000-0000-0000-0000-000000000000"])
    assert n == 0


def test_outlet_id_for_channel_live_db_shape(live_db):
    assert discovery._outlet_id_for_channel("UC_does_not_exist_00000000") is None


# --- Task 6: health() auto-kept summary ---
#
# Unlike the Task 5 functions above, this query reads only
# discovered_sources.status/status_reason/outlet_id/reviewed_at — columns that
# already exist today — so it's safe to run against the live (not-yet-
# migrated) DB, and gets a real live_db test rather than an omission note.

def test_health_defaults_include_auto_kept_keys_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    h = discovery.health()
    assert h["auto_kept_week"] == 0
    assert h["auto_kept_outlets"] == 0


def test_health_reports_auto_kept_live_db_shape(live_db):
    h = discovery.health()
    assert isinstance(h["auto_kept_week"], int)
    assert isinstance(h["auto_kept_outlets"], int)
    assert h["auto_kept_week"] >= 0
    assert h["auto_kept_outlets"] >= 0

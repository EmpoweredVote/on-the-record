from __future__ import annotations

import json

from fastapi.testclient import TestClient

from gui.app import create_app


def test_new_form_renders(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert "<form" in body and 'action="/new"' in body
    assert 'name="input"' in body and 'name="event_kind"' in body


def test_static_assets_send_no_cache(tmp_meetings_dir):
    resp = TestClient(create_app()).get("/static/new_meeting.js")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"


def test_post_new_launches_and_redirects(tmp_meetings_dir, monkeypatch):
    from gui import runner
    launched = {}

    def fake_launch(p, **kw):
        launched["params"] = p
        return "2026-02-10-regular"

    monkeypatch.setattr(runner, "launch_run", fake_launch)
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "Bloomington", "compute": "local", "diarizer": "oss",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-02-10-regular?tab=progress"
    assert launched["params"].input == "https://x/v"


def test_post_new_missing_input_is_rejected(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.post("/new", data={"input": "", "date": "2026-02-10",
                                     "meeting_type": "Regular", "event_kind": "council"},
                       follow_redirects=False)
    assert resp.status_code == 400


def test_run_page_and_status_json(tmp_meetings_dir, tagged_meeting_dir, monkeypatch):
    # Seed a meeting with a run sidecar + state so run_status returns data.
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=2)
    (mdir / "gui_run.json").write_text(json.dumps({"pid": 1, "cmd": [], "status": "running"}))
    (mdir / "gui_run.log").write_text("STAGE 2: Speaker Diarization\n")
    client = TestClient(create_app())

    page = client.get("/meetings/2026-02-10-regular/run")
    assert page.status_code == 200
    assert "workspace.js" in page.text and "stepper" in page.text.lower()

    st = client.get("/meetings/2026-02-10-regular/run/status")
    assert st.status_code == 200
    body = st.json()
    assert body["completed_stage"] == 2
    assert "STAGE 2" in body["log_tail"]

    assert client.get("/meetings/ghost/run/status").status_code == 404


def test_formmeta_covers_all_event_kinds():
    from gui.formmeta import EVENT_KIND_HELP, CITY_REQUIRED_KINDS
    from src.event_kinds import EVENT_KINDS
    # every controlled event kind has help text
    assert set(EVENT_KIND_HELP) == set(EVENT_KINDS)
    assert all(v.strip() for v in EVENT_KIND_HELP.values())
    # deliberative kinds require a city
    assert CITY_REQUIRED_KINDS == {"council", "school_board"}


def test_formmeta_compute_and_diarizer_help():
    from gui.formmeta import COMPUTE_HELP, DIARIZER_HELP
    assert set(COMPUTE_HELP) == {"local", "modal"}
    assert set(DIARIZER_HELP) == {"oss", "api", "vibevoice"}
    assert all(v.strip() for v in {**COMPUTE_HELP, **DIARIZER_HELP}.values())


def test_fields_by_kind_covers_all_event_kinds():
    from gui.formmeta import FIELDS_BY_KIND
    from src.event_kinds import EVENT_KINDS
    assert set(FIELDS_BY_KIND) == set(EVENT_KINDS)


def test_fields_by_kind_gating():
    from gui.formmeta import FIELDS_BY_KIND
    # council: city + body, no guest/race/crec
    assert "city" in FIELDS_BY_KIND["council"] and "body" in FIELDS_BY_KIND["council"]
    assert "guest" not in FIELDS_BY_KIND["council"]
    # interviews: guest + race, no city/body
    for k in ("news_clip", "press_conference", "podcast"):
        assert "guest" in FIELDS_BY_KIND[k] and "race" in FIELDS_BY_KIND[k]
        assert "city" not in FIELDS_BY_KIND[k] and "body" not in FIELDS_BY_KIND[k]
    # debate/forum: race, no guest
    assert "race" in FIELDS_BY_KIND["forum"] and "guest" not in FIELDS_BY_KIND["forum"]
    # floor: crec only
    assert "crec_chamber" in FIELDS_BY_KIND["floor"]
    assert "city" not in FIELDS_BY_KIND["floor"]


def test_default_compute_is_modal():
    from gui.formmeta import DEFAULT_COMPUTE, DEFAULT_DIARIZER
    assert DEFAULT_COMPUTE == "modal"
    assert DEFAULT_DIARIZER == "oss"


def test_post_new_council_requires_city(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "",  # no city
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert "city" in resp.text.lower()


def test_post_new_council_with_city_launches(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-02-10-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "Bloomington",
    }, follow_redirects=False)
    assert resp.status_code == 303


def test_post_new_other_kind_needs_no_city(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-02-10-clip")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Clip",
        "event_kind": "news_clip", "city": "",
    }, follow_redirects=False)
    assert resp.status_code == 303  # news_clip doesn't require a city


def test_new_form_shows_help_and_preview(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    # event-kind help text is rendered (from formmeta)
    assert "deliberative, links to a Chamber" in body
    # compute + diarizer help present
    assert "Modal cloud GPU" in body
    assert "pyannote.ai Precision-2" in body
    # live preview + derived-id scaffolding present, wired via new_meeting.js
    assert 'id="preview"' in body
    assert 'id="derived-id"' in body
    assert "new_meeting.js" in body


def test_new_meeting_js_wires_preview_and_city_rule():
    from pathlib import Path
    js = Path("gui/static/new_meeting.js").read_text()
    # updates the derived id, the preview, and toggles the city-required marker
    assert "derived-id" in js
    assert "preview" in js or "pv-title" in js
    assert "city-req" in js
    # slug derivation mirrors the server ({date}-{slug(meeting_type)})
    assert "toLowerCase" in js


def test_post_new_warns_on_duplicate_source(tagged_meeting_dir, tmp_meetings_dir):
    from src.checkpoint import PipelineState
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=4)
    st = PipelineState(mdir); st.source_key = "youtube:dup123"; st.save()

    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://youtu.be/dup123", "date": "2026-05-05", "meeting_type": "Regular",
        "event_kind": "other",
    }, follow_redirects=False)
    # Not launched: a confirm page (200) naming the existing meeting.
    assert resp.status_code == 200
    assert "already" in resp.text.lower()
    assert "2026-02-10-regular" in resp.text


def test_post_new_confirm_bypasses_dedup(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from src.checkpoint import PipelineState
    from gui import runner
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=4)
    st = PipelineState(mdir); st.source_key = "youtube:dup123"; st.save()
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-05-05-regular")

    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://youtu.be/dup123", "date": "2026-05-05", "meeting_type": "Regular",
        "event_kind": "other", "confirm": "1",
    }, follow_redirects=False)
    assert resp.status_code == 303  # confirmed -> launched


def test_post_new_no_duplicate_launches(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-05-05-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://youtu.be/brandnew", "date": "2026-05-05", "meeting_type": "Regular",
        "event_kind": "other",
    }, follow_redirects=False)
    assert resp.status_code == 303


def test_meeting_type_defaults_cover_all_kinds():
    from gui.formmeta import MEETING_TYPE_DEFAULTS
    from src.event_kinds import EVENT_KINDS
    assert set(MEETING_TYPE_DEFAULTS) == set(EVENT_KINDS)
    # deliberative + electoral kinds get a non-empty suggestion
    assert MEETING_TYPE_DEFAULTS["forum"] == "Candidate Forum"
    assert MEETING_TYPE_DEFAULTS["council"] == "Regular Session"
    assert MEETING_TYPE_DEFAULTS["debate"] == "Debate"
    # EVERY default must be non-empty — the field is auto-filled under a collapsed
    # "Advanced" section, and a blank value there would be an invisible trap.
    assert all(v.strip() for v in MEETING_TYPE_DEFAULTS.values())
    assert MEETING_TYPE_DEFAULTS["other"] == "Recording"


def test_meeting_type_field_demoted_to_advanced(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    # the label field now lives inside the Advanced <details>, after it in the DOM
    assert body.index("<details") < body.index('name="meeting_type"')
    # Title is now the prominent headline field
    assert "headline shown on the site" in body
    # the demoted field must NOT be `required` (it's in a collapsed section +
    # auto-filled; a required control there can't be focused to report an error)
    mtype_tag = body[body.index('name="meeting_type"'):body.index('name="meeting_type"') + 120]
    assert "required" not in mtype_tag


def test_new_form_relabels_event_label_field(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert "Event label" in body                 # relabeled (was "Meeting type")
    assert 'name="meeting_type"' in body          # backend field name unchanged
    assert "Candidate Forum" in body              # a default injected for JS/examples


def test_new_meeting_js_applies_label_default():
    from pathlib import Path
    js = Path("gui/static/new_meeting.js").read_text()
    assert "__MEETING_TYPE_DEFAULTS" in js
    # only overwrite when empty or still a known default (don't clobber custom text)
    assert "f-mtype" in js


def test_post_redo_launches_and_redirects(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    called = {}
    monkeypatch.setattr(runner, "launch_redo",
                        lambda mid, stage, **kw: called.setdefault("v", (mid, stage)) or mid)
    client = TestClient(create_app())
    resp = client.post("/meetings/2026-02-04-council/redo", data={"stage": "diarize"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-02-04-council/run"
    assert called["v"] == ("2026-02-04-council", "diarize")


def test_post_redo_invalid_stage_400(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    resp = TestClient(create_app()).post("/meetings/2026-02-04-council/redo",
                                         data={"stage": "bogus"}, follow_redirects=False)
    assert resp.status_code == 400


def test_post_redo_unknown_meeting_404(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_redo", lambda mid, stage, **kw: None)  # unknown -> None
    resp = TestClient(create_app()).post("/meetings/ghost/redo",
                                         data={"stage": "diarize"}, follow_redirects=False)
    assert resp.status_code == 404


def test_run_page_has_redo_buttons(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/run").text
    assert 'action="/meetings/2026-02-04-council/redo"' in body
    assert 'value="diarize"' in body and 'value="transcribe"' in body
    assert 'value="identify"' in body and 'value="summary"' in body


def test_post_continue_launches_and_redirects(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    seen = {}
    monkeypatch.setattr(runner, "launch_resume",
                        lambda mid, override_gate=False, **kw: seen.setdefault("v", (mid, override_gate)) or mid)
    client = TestClient(create_app())
    r = client.post("/meetings/2026-02-04-council/continue", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/meetings/2026-02-04-council/run"
    assert seen["v"] == ("2026-02-04-council", False)


def test_post_continue_override(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    seen = {}
    monkeypatch.setattr(runner, "launch_resume",
                        lambda mid, override_gate=False, **kw: seen.setdefault("og", override_gate) or mid)
    TestClient(create_app()).post("/meetings/2026-02-04-council/continue",
                                  data={"override": "1"}, follow_redirects=False)
    assert seen["og"] is True


def test_post_continue_unknown_404(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_resume", lambda mid, override_gate=False, **kw: None)
    assert TestClient(create_app()).post("/meetings/ghost/continue", data={},
                                         follow_redirects=False).status_code == 404


def test_run_page_has_continue_button(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/run").text
    assert 'action="/meetings/2026-02-04-council/continue"' in body
    assert "Continue processing" in body
    assert "override" in body.lower()  # the gate-override variant present


def test_post_new_parses_event_orgs(tmp_meetings_dir, monkeypatch):
    from gui import runner
    seen = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: seen.setdefault("orgs", p.event_orgs) or "2026-05-15-interview")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-05-15", "meeting_type": "Interview",
        "event_kind": "news_clip", "event_orgs": "CBS, NBC ,, Telemundo",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert seen["orgs"] == ["CBS", "NBC", "Telemundo"]   # split, trimmed, blanks dropped


def test_new_form_has_event_orgs_field(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert 'name="event_orgs"' in body
    assert "Produced by" in body


def test_new_form_has_body_picker(tmp_config_dir, tmp_meetings_dir):
    import json
    rosters = tmp_config_dir / "rosters"; rosters.mkdir(exist_ok=True)
    (rosters / "bloomington-common-council.json").write_text(json.dumps(
        {"body_key": "Bloomington Common Council", "politicians": [{}]}))
    body = TestClient(create_app()).get("/new").text
    assert 'name="body_slug"' in body
    assert 'value="bloomington-common-council"' in body
    assert "Bloomington Common Council" in body


def test_post_new_threads_body_slug(tmp_config_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    seen = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: seen.setdefault("body", p.body_slug) or "2026-02-04-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-04", "meeting_type": "Regular Session",
        "event_kind": "council", "city": "Bloomington",
        "body_slug": "bloomington-common-council",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert seen["body"] == "bloomington-common-council"


def test_post_new_blank_body_is_none(tmp_config_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    seen = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: seen.setdefault("body", p.body_slug) or "2026-02-04-clip")
    TestClient(create_app()).post("/new", data={
        "input": "https://x/v", "date": "2026-02-04", "meeting_type": "Clip",
        "event_kind": "news_clip", "body_slug": ""}, follow_redirects=False)
    assert seen["body"] is None


def test_source_meta_non_ytdlp_returns_empty(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/api/source-meta", params={"url": "/path/to/video.mp4"})
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "title": None, "event_org": None}


def test_source_meta_ytdlp_returns_mapped_json(tmp_meetings_dir, monkeypatch):
    from src import ingest, resolve

    # No podcast/CMS resolver match -> falls back to the yt-dlp path.
    monkeypatch.setattr(resolve, "resolve_source", lambda url: None)
    monkeypatch.setattr(ingest, "fetch_source_metadata", lambda url: {
        "title": "City Council Feb 10", "channel": "WFYI",
        "upload_date": "2026-02-10", "duration": 3600, "chapters": [],
    })
    client = TestClient(create_app())
    resp = client.get("/api/source-meta",
                      params={"url": "https://youtube.com/watch?v=x"})
    assert resp.status_code == 200
    assert resp.json() == {
        "date": "2026-02-10", "title": "City Council Feb 10", "event_org": "WFYI",
    }


def test_source_meta_prefers_resolver(tmp_meetings_dir, monkeypatch):
    from src import ingest, resolve
    from src.resolve import ResolvedSource

    monkeypatch.setattr(resolve, "resolve_source", lambda url: ResolvedSource(
        audio_url="https://cdn.example/ep1.mp3", title="Ep 1",
        date="2026-06-03", outlet="WNLA",
    ))

    def _boom(url):
        raise AssertionError("should not call yt-dlp when the resolver matched")

    monkeypatch.setattr(ingest, "fetch_source_metadata", _boom)
    client = TestClient(create_app())
    resp = client.get("/api/source-meta",
                      params={"url": "https://show.buzzsprout.com/1/ep"})
    assert resp.status_code == 200
    assert resp.json() == {"date": "2026-06-03", "title": "Ep 1", "event_org": "WNLA"}


def test_races_search_route_returns_json(monkeypatch, tmp_meetings_dir):
    import gui.races as races
    monkeypatch.setattr(races, "search_races_safe",
                        lambda q, **kw: {"results": [
                            {"race_id": "u1", "label": "Governor of Michigan · 2026",
                             "slug": "governor-michigan"}], "error": None})
    from fastapi.testclient import TestClient
    from gui.app import create_app
    resp = TestClient(create_app()).get("/api/races/search", params={"q": "gov"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["slug"] == "governor-michigan"
    assert body["error"] is None


def test_post_new_passes_guest_and_race(monkeypatch, tmp_meetings_dir):
    from gui import runner
    captured = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: captured.setdefault("p", p) or "2026-05-01-x")
    from fastapi.testclient import TestClient
    from gui.app import create_app
    resp = TestClient(create_app()).post("/new", data={
        "input": "https://x/v", "date": "2026-05-01", "meeting_type": "Interview",
        "event_kind": "news_clip", "guest": "Xavier Becerra",
        "race_id": "uuid-9", "race_slug": "ca-governor",
        "compute": "modal", "diarizer": "oss",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert captured["p"].guest == "Xavier Becerra"
    assert captured["p"].race_id == "uuid-9"
    assert captured["p"].race_slug == "ca-governor"


def test_new_form_renders_kind_fields_and_modal_default(tmp_meetings_dir):
    from fastapi.testclient import TestClient
    from gui.app import create_app
    body = TestClient(create_app()).get("/new").text
    # kind-gated fields are present in the markup (JS hides the inapplicable ones)
    assert 'data-field="guest"' in body
    assert 'data-field="race"' in body
    assert 'data-field="body"' in body
    assert 'data-field="crec_chamber"' in body
    # FIELDS_BY_KIND is injected for the client
    assert "FIELDS_BY_KIND" in body
    # compute select defaults to modal (selected option)
    assert 'value="modal" selected' in body or "__DEFAULT_COMPUTE" in body


def test_new_meeting_js_wires_race_search_and_field_gating(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/new_meeting.js").read_text()
    assert "/api/races/search" in js
    assert "data-field" in js
    assert "race_id" in js and "f-race-slug" in js

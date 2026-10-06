from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _rec(label, prefill=None, **lk):
    base = {"name": prefill or "Rachel Sample", "source": "web", "verified": bool(prefill),
            "politician_id": None, "local_slug": None, "url": "https://x.org/a" if prefill else None,
            "affiliation": None, "reason": None if prefill else "different name returned"}
    base.update(lk)
    return {"label": label, "tier": "strong", "role": "presenter", "titled": False, "partial": False,
            "conflict": None, "spoken_name": "Rachel Sample", "prefill_name": prefill,
            "evidence": [{"kind": "E1", "quote": "My name is Rachel Sample"}], "lookup": base}


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path / "cfg")
    d = tmp_path / "m1"
    d.mkdir()
    segs = [{"segment_id": i, "start_time": i * 10, "end_time": i * 10 + 9, "speaker_label": lab, "text": "hello there"}
            for i, lab in enumerate(["W", "V", "N"])]
    (d / "transcript_named.json").write_text(json.dumps({
        "meeting_id": "m1", "city": "Indianapolis", "date": "2026-01-14", "meeting_type": "Hearing",
        "event_kind": "council", "segments": segs,
        "speakers": {"N": {"speaker_label": "N", "speaker_name": "Liz Brown", "id_method": "human_review",
                           "confidence": 1.0}}}))
    (d / "name_suggestions.json").write_text(json.dumps({"warnings": ["Claude CLI cannot run lookups — run `claude-ev`, then /login"],
        "suggestions": [_rec("W", "Rachael Sample"), _rec("V"), _rec("N", "Someone Else")]}))
    return d


def _log_lines(meeting_dir):
    return [json.loads(l) for l in (meeting_dir / "name_suggestion_log.jsonl").read_text().splitlines()]


def test_review_page_shows_suggestions_banner_and_bulk(meeting):
    from gui import review_api

    page = review_api.load_review_page("m1")
    cards = {c.label: c for c in page.all_cards}
    assert cards["W"].suggestion.state == "acceptable" and cards["V"].suggestion.state == "unverified"
    assert cards["N"].suggestion is None                       # already named: no suggestion
    assert page.bulk_accept_labels == ["W"]
    assert "claude-ev" in page.suggestion_warnings[0]


def test_apply_suggestion_local_and_logs(meeting, monkeypatch):
    from gui import review_api

    calls = []
    monkeypatch.setattr(review_api, "apply_make_local_person",
                        lambda mid, lab, slug, role, name="": calls.append((mid, lab, slug, role, name)) or True)
    assert review_api.apply_suggestion("m1", "W") == "Rachael Sample"
    assert calls == [("m1", "W", "rachael-sample", "presenter", "Rachael Sample")]
    log = [json.loads(l) for l in (meeting / "name_suggestion_log.jsonl").read_text().splitlines()]
    assert log[-1]["action"] == "accepted"
    assert review_api.apply_suggestion("m1", "V") is None      # not acceptable


def test_apply_suggestion_link(meeting, monkeypatch):
    from gui import review_api

    (meeting / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Liz Brown", source="roster", politician_id="p-b")]}))
    calls = []
    monkeypatch.setattr(review_api, "apply_link",
                        lambda mid, lab, slug, pid, name="": calls.append((lab, pid, name)) or True)
    assert review_api.apply_suggestion("m1", "W") == "Liz Brown"
    assert calls == [("W", "p-b", "Liz Brown")]


def test_apply_all_only_acceptable_unnamed(meeting, monkeypatch):
    from gui import review_api

    done = []
    monkeypatch.setattr(review_api, "apply_make_local_person",
                        lambda mid, lab, slug, role, name="": done.append(lab) or True)
    assert review_api.apply_all_suggestions("m1") == [("W", "Rachael Sample")]
    assert done == ["W"]
    log = [json.loads(l) for l in (meeting / "name_suggestion_log.jsonl").read_text().splitlines()]
    assert log[-1]["action"] == "bulk_accepted"


def test_log_suggestion_edit(meeting):
    from gui import review_api

    review_api.log_suggestion_edit("m1", "W", "Rachel Sample")
    review_api.log_suggestion_edit("m1", "W", "Rachael Sample")
    actions = [json.loads(l)["action"] for l in (meeting / "name_suggestion_log.jsonl").read_text().splitlines()]
    assert actions == ["edited", "accepted"]


def test_routes(meeting, monkeypatch):
    from gui import review_api, runner
    from gui.app import create_app

    monkeypatch.setattr(review_api, "apply_suggestion", lambda mid, lab, bulk=False: "Rachael Sample")
    monkeypatch.setattr(review_api, "apply_all_suggestions", lambda mid: [("W", "Rachael Sample")])
    launched = []
    monkeypatch.setattr(runner, "launch_suggest_names", lambda mid, **kw: launched.append(mid) or mid)
    c = TestClient(create_app())
    assert c.post("/meetings/m1/speakers/W/accept-suggestion", follow_redirects=False).status_code == 303
    assert c.post("/meetings/m1/accept-suggestions", follow_redirects=False).status_code == 303
    assert c.post("/meetings/m1/suggest-names", follow_redirects=False).status_code == 303
    assert launched == ["m1"]
    monkeypatch.setattr(review_api, "apply_suggestion", lambda mid, lab, bulk=False: None)
    assert c.post("/meetings/m1/speakers/V/accept-suggestion", follow_redirects=False).status_code == 404
    r = c.post("/meetings/m1/accept-suggestions", follow_redirects=False)
    assert "Accepted+1" in r.headers["location"]


# --- beyond the brief: the guarantees the controller asked for ---------------------


def test_apply_suggestion_real_write_names_the_speaker(meeting):
    """End to end through the real apply_make_local_person: the transcript is
    written, and the card no longer offers the suggestion afterwards."""
    from gui import review_api

    assert review_api.apply_suggestion("m1", "W") == "Rachael Sample"
    sp = json.loads((meeting / "transcript_named.json").read_text())["speakers"]["W"]
    assert sp["speaker_name"] == "Rachael Sample" and sp["local_slug"] == "rachael-sample"
    page = review_api.load_review_page("m1")
    assert {c.label: c for c in page.all_cards}["W"].suggestion is None
    assert page.bulk_accept_labels == []


def test_apply_suggestion_refuses_label_named_meanwhile(meeting, monkeypatch):
    from gui import review_api

    calls = []
    monkeypatch.setattr(review_api, "apply_make_local_person", lambda *a, **k: calls.append(a) or True)
    # N has an acceptable record, but the current transcript already names N.
    assert review_api.apply_suggestion("m1", "N") is None
    # A label named after the page was loaded: also refused.
    data = json.loads((meeting / "transcript_named.json").read_text())
    data["speakers"]["W"] = {"speaker_label": "W", "speaker_name": "Somebody Typed", "id_method": "human_review",
                             "confidence": 1.0}
    (meeting / "transcript_named.json").write_text(json.dumps(data))
    assert review_api.apply_suggestion("m1", "W") is None
    assert review_api.apply_all_suggestions("m1") == []
    assert calls == []
    assert not (meeting / "name_suggestion_log.jsonl").exists()


def test_apply_suggestion_bad_slug_is_none(meeting, monkeypatch):
    from gui import review_api

    def boom(*a, **k):
        raise ValueError("slug taken")

    monkeypatch.setattr(review_api, "apply_make_local_person", boom)
    assert review_api.apply_suggestion("m1", "W") is None
    assert review_api.apply_all_suggestions("m1") == []
    assert not (meeting / "name_suggestion_log.jsonl").exists()


def test_apply_suggestion_unsafe_or_missing_meeting(meeting):
    from gui import review_api

    assert review_api.apply_suggestion("../x", "W") is None
    assert review_api.apply_suggestion("ghost", "W") is None
    assert review_api.apply_all_suggestions("ghost") == []
    review_api.log_suggestion_edit("../x", "W", "A")       # no raise
    review_api.log_suggestion_edit("m1", "ZZ", "A")        # no record: no raise, no log
    assert not (meeting / "name_suggestion_log.jsonl").exists()


def test_link_and_local_routes_log_edit_from_suggestion(meeting, monkeypatch):
    from gui import review_api
    from gui.app import create_app

    monkeypatch.setattr(review_api, "apply_link", lambda *a, **k: True)
    monkeypatch.setattr(review_api, "apply_make_local_person", lambda *a, **k: True)
    c = TestClient(create_app())
    r = c.post("/meetings/m1/speakers/W/local-person", follow_redirects=False,
               data={"slug": "rachel-sample", "role": "presenter", "name": "Rachel Sample", "from_suggestion": "W"})
    assert r.status_code == 303
    r = c.post("/meetings/m1/speakers/W/link", follow_redirects=False,
               data={"politician_id": "p-1", "name": "Rachael Sample", "from_suggestion": "W"})
    assert r.status_code == 303
    # No from_suggestion (or a different label): nothing logged.
    c.post("/meetings/m1/speakers/W/link", follow_redirects=False, data={"politician_id": "p-1", "name": "X"})
    c.post("/meetings/m1/speakers/W/link", follow_redirects=False,
           data={"politician_id": "p-1", "name": "X", "from_suggestion": "V"})
    # Name changed -> edited; same name but linked to a roster person instead of
    # the suggested local person (identity changed) -> edited too.
    assert [(l["action"], l["final_name"]) for l in _log_lines(meeting)] == [
        ("edited", "Rachel Sample"), ("edited", "Rachael Sample")]


def test_edit_logging_compares_identity(meeting, monkeypatch):
    from gui import review_api
    from gui.app import create_app

    monkeypatch.setattr(review_api, "apply_link", lambda *a, **k: True)
    monkeypatch.setattr(review_api, "apply_make_local_person", lambda *a, **k: True)
    c = TestClient(create_app())
    post = lambda path, **d: c.post(f"/meetings/m1/speakers/W/{path}", follow_redirects=False,
                                    data={"from_suggestion": "W", **d})
    # Same name, same slug, different role -> accepted (role is metadata).
    post("local-person", slug="rachael-sample", role="public_comment", name="Rachael Sample")
    # Same name, different slug -> edited.
    post("local-person", slug="rachael-sample-2", role="presenter", name="Rachael Sample")
    assert [l["action"] for l in _log_lines(meeting)] == ["accepted", "edited"]

    # A roster suggestion: same politician_id -> accepted; another id -> edited.
    (meeting / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Liz Brown", source="roster", politician_id="p-b")]}))
    post("link", politician_id="p-b", name="Liz Brown")
    post("link", politician_id="p-x", name="Liz Brown")
    post("local-person", slug="liz-brown", role="presenter", name="Liz Brown")
    assert [l["action"] for l in _log_lines(meeting)][2:] == ["accepted", "edited", "edited"]


def test_suggestion_url_must_be_http(meeting):
    from gui.app import create_app
    from src.name_suggestion_view import to_view

    for bad in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<b>x</b>", "ftp://x.org/a", " "):
        v = to_view(_rec("W", "Rachael Sample", url=bad))
        assert v.url is None and v.domain is None
    v = to_view(_rec("W", "Rachael Sample", url="https://www.x.org/a"))
    assert v.url == "https://www.x.org/a" and v.domain == "x.org"

    (meeting / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Rachael Sample", url="javascript:alert(1)")]}))
    (meeting / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    html = TestClient(create_app()).get("/meetings/m1?tab=review").text
    assert "Suggested: Rachael Sample" in html
    assert 'href="javascript:' not in html.lower() and "javascript:alert" not in html


def test_info_suggestion_has_no_actions_or_prefill(meeting):
    from gui.app import create_app

    info = _rec("W", None, name="Rachel")
    info["partial"] = True
    (meeting / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [info]}))
    (meeting / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    html = TestClient(create_app()).get("/meetings/m1/panel/review").text
    assert "Possible: Rachel" in html and "first name only" in html
    assert "from_suggestion" not in html and "data-from-suggestion" not in html
    assert 'value="Rachel"' not in html
    assert "/accept-suggestion" not in html and "Accept all verified" not in html


def test_edit_logging_never_breaks_the_form(meeting, monkeypatch):
    from gui import review_api
    from gui.app import create_app

    def boom(*a, **k):
        raise RuntimeError("log broke")

    monkeypatch.setattr(review_api, "apply_make_local_person", lambda *a, **k: True)
    monkeypatch.setattr(review_api, "log_suggestion_edit", boom)
    r = TestClient(create_app()).post(
        "/meetings/m1/speakers/W/local-person", follow_redirects=False,
        data={"slug": "a", "role": "presenter", "name": "A", "from_suggestion": "W"})
    assert r.status_code == 303


def test_launch_suggest_names_spawns_lookup(meeting):
    from gui import runner

    seen = []

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        seen.append(cmd)
        return FakeProc()

    try:
        assert runner.launch_suggest_names("m1", python_exe="py", script="run_local.py", popen=fake_popen) == "m1"
        assert seen == [["py", "run_local.py", "--suggest-names", "m1"]]
        # A run still in progress for this meeting is never clobbered.
        assert runner.launch_suggest_names("m1", python_exe="py", script="run_local.py", popen=fake_popen) is None
        assert len(seen) == 1
    finally:
        runner._RUNS.pop("m1", None)
    assert runner.launch_suggest_names("ghost", python_exe="py", script="s", popen=fake_popen) is None
    assert runner.launch_suggest_names("../x", python_exe="py", script="s", popen=fake_popen) is None


def test_review_page_html_shows_suggestion_banner_and_bulk(meeting):
    from gui.app import create_app

    (meeting / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    c = TestClient(create_app())
    html = c.get("/meetings/m1?tab=review").text
    assert "Suggested: Rachael Sample" in html
    assert "Suggested (not verified): Rachel Sample" in html
    assert "Accept all verified (1)" in html
    assert 'action="/meetings/m1/accept-suggestions"' in html
    assert "Name lookup" in html and "claude-ev" in html
    assert 'action="/meetings/m1/suggest-names"' in html
    assert 'action="/meetings/m1/speakers/W/accept-suggestion"' in html
    assert 'action="/meetings/m1/speakers/V/accept-suggestion"' not in html   # unverified: no Accept
    assert 'name="from_suggestion" value="W"' in html
    assert 'value="Rachael Sample"' in html                                   # local form pre-filled
    assert 'class="bulk-accept" data-navigate' in html     # full navigation keeps the notice
    assert "data-rerun-names" in html and 'class="rerun-msg"' in html
    assert 'data-from-suggestion="V"' in html               # unverified: Edit pre-filled


def test_bulk_accept_full_navigation_shows_notice(meeting):
    """What a browser does with the data-navigate bulk form: POST, follow the
    303s, and land on the shell with the report."""
    from gui.app import create_app

    (meeting / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    r = TestClient(create_app()).post("/meetings/m1/accept-suggestions")
    assert r.status_code == 200
    assert "Accepted 1: W Rachael Sample" in r.text
    assert json.loads((meeting / "transcript_named.json").read_text())["speakers"]["W"]["speaker_name"] == "Rachael Sample"


def test_rerun_refused_while_a_run_is_live(meeting, monkeypatch):
    from gui import runner
    from gui.app import create_app

    launched = []
    monkeypatch.setattr(runner, "run_is_live", lambda mid: True)
    monkeypatch.setattr(runner, "_spawn", lambda *a, **k: launched.append(a) or "m1")
    r = TestClient(create_app()).post("/meetings/m1/suggest-names", follow_redirects=False)
    assert r.status_code == 409 and launched == []
    assert runner.launch_suggest_names("m1", python_exe="py", script="s") is None


def test_workspace_js_reruns_in_background_and_reloads():
    from pathlib import Path

    js = Path("gui/static/workspace.js").read_text()
    assert "data-rerun-names" in js and "rerunNames" in js
    assert "409" in js and "/status" in js and 'loadPanel("review"' in js
    assert "data-from-suggestion" in js and "from_suggestion" in js


def test_review_notice_flows_through_redirect_and_is_escaped(meeting):
    from gui.app import create_app

    (meeting / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    c = TestClient(create_app())
    r = c.get("/meetings/m1/review?notice=Accepted+1%3A+W+<b>x</b>", follow_redirects=False)
    assert "notice=" in r.headers["location"]
    html = c.get(r.headers["location"]).text
    assert "Accepted 1: W &lt;b&gt;x&lt;/b&gt;" in html


def test_accept_suggestion_on_unidentified_speaker_replaces_handle(meeting):
    """A speaker marked unidentified (synthetic unidentified-... slug) is shown a
    suggestion, and accepting it replaces the handle with the real identity."""
    from gui import review_api
    from src import review
    from src.models import SpeakerMapping

    path = meeting / "transcript_named.json"
    data = json.loads(path.read_text())
    mappings = {"W": SpeakerMapping(speaker_label="W")}
    review.mark_unidentified(mappings, [], "W", "m1")
    data["speakers"]["W"] = {k: getattr(mappings["W"], k) for k in
                             ("speaker_label", "speaker_name", "local_slug", "speaker_status",
                              "id_method", "confidence", "needs_review")}
    path.write_text(json.dumps(data))
    page = review_api.load_review_page("m1")
    assert {c.label: c for c in page.all_cards}["W"].suggestion is not None
    assert review_api.apply_suggestion("m1", "W") == "Rachael Sample"
    sp = json.loads(path.read_text())["speakers"]["W"]
    assert sp["local_slug"] == "rachael-sample" and sp.get("speaker_status") in (None, "")


def test_corrupt_suggestion_records_do_not_break_review_page(meeting):
    (meeting / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Rachael Sample"),
        {"label": "V", "lookup": "bad", "evidence": [None, "x"]},
        {"label": 9, "lookup": {"name": "Z"}},
        {"label": "N", "lookup": [], "evidence": 5}]}))
    from gui import review_api
    page = review_api.load_review_page("m1")
    cards = {c.label: c for c in page.all_cards}
    assert cards["W"].suggestion is not None

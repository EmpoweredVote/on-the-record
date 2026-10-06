# tests/test_name_suggestion_view.py
from __future__ import annotations

import json

from src.name_suggestion_view import (
    AcceptAction, accept_action, apply_action_to_mappings, is_unnamed_mapping, load_suggestions,
    to_view, views_for_unnamed,
)


def rec(label="W", *, prefill=None, verified=False, conflict=None, partial=False, source="web",
        name="Rachael Sample", url=None, reason=None, pid=None, local=None, role="presenter"):
    return {"label": label, "tier": "strong", "role": role, "titled": False, "partial": partial,
            "conflict": conflict, "spoken_name": "Rachel Sample", "prefill_name": prefill,
            "evidence": [{"kind": "E1", "quote": "My name is Rachel Sample"}, {"kind": "E4", "quote": "(source captions)"}],
            "lookup": {"name": name, "source": source, "verified": verified, "politician_id": pid,
                       "local_slug": local, "url": url, "affiliation": None, "reason": reason}}


def test_states():
    v = to_view(rec(prefill="Rachael Sample", verified=True, url="https://www.example.org/team"))
    assert (v.state, v.domain, v.quotes) == ("acceptable", "example.org", ["My name is Rachel Sample"])
    v = to_view(rec(reason="different name returned"))
    assert (v.state, v.reason) == ("unverified", "different name returned")
    v = to_view(rec(conflict="name_on_two_labels"))
    assert v.state == "info" and "another speaker" in v.reason
    v = to_view(rec(partial=True, name="Rachel"))
    assert v.state == "info" and v.reason == "first name only"


def test_load_suggestions_missing_and_corrupt(tmp_path):
    assert load_suggestions(tmp_path) == ([], [])
    (tmp_path / "name_suggestions.json").write_text("{not json")
    assert load_suggestions(tmp_path) == ([], [])
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": ["w"], "suggestions": [rec()]}))
    recs, warns = load_suggestions(tmp_path)
    assert len(recs) == 1 and warns == ["w"]


def test_is_unnamed_mapping():
    assert is_unnamed_mapping(None)
    assert is_unnamed_mapping({"speaker_name": ""})
    assert is_unnamed_mapping({"speaker_name": "Unidentified 2", "speaker_status": "unidentified"})
    assert not is_unnamed_mapping({"speaker_name": "Liz Brown"})
    assert not is_unnamed_mapping({"speaker_name": "", "politician_id": "p"})
    assert not is_unnamed_mapping({"speaker_name": "", "local_slug": "x"})


def test_views_for_unnamed_filters_named_and_missing_labels(tmp_path):
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        rec("W"), rec("N"), rec("GONE")]}))
    views, _ = views_for_unnamed(tmp_path, {"N": {"speaker_name": "Liz Brown"}}, ["W", "N"])
    assert set(views) == {"W"}


def test_accept_action_mapping():
    v = to_view(rec(prefill="Liz Brown", verified=True, source="roster", name="Liz Brown", pid="p-b"))
    assert accept_action(v, "liz-brown") == AcceptAction("link", "Liz Brown", politician_id="p-b")
    v = to_view(rec(prefill="Rachael Sample", verified=True, source="local_people", local="rachael-sample"))
    assert accept_action(v, "x") == AcceptAction("local", "Rachael Sample", slug="rachael-sample", role="presenter")
    v = to_view(rec(prefill="Rachael Sample", verified=True, role=None))
    assert accept_action(v, "rachael-sample") == AcceptAction("local", "Rachael Sample", slug="rachael-sample",
                                                              role="public_comment")
    assert accept_action(to_view(rec(reason="x")), "s") is None


def test_apply_action_to_mappings_local_and_link():
    from src.models import Segment, SpeakerMapping

    segs = [Segment(segment_id=0, start_time=0, end_time=1, speaker_label="W", text="hi")]
    maps = {"W": SpeakerMapping(speaker_label="W", speaker_name=None)}
    apply_action_to_mappings(maps, segs, "W", AcceptAction("local", "Rachael Sample", slug="rachael-sample",
                                                           role="presenter"), "council")
    assert maps["W"].speaker_name == "Rachael Sample" and maps["W"].local_slug == "rachael-sample"
    maps2 = {"W": SpeakerMapping(speaker_label="W", speaker_name=None)}
    apply_action_to_mappings(maps2, segs, "W", AcceptAction("link", "Liz Brown", politician_id="p-b"), "council")
    assert maps2["W"].speaker_name == "Liz Brown" and maps2["W"].politician_id == "p-b"


def test_terminal_suggestion_line():
    from src.name_suggestion_view import terminal_suggestion_line
    v = to_view(rec(prefill="Rachael Sample", verified=True, url="https://www.example.org/x"))
    assert terminal_suggestion_line(v) == "Suggested: Rachael Sample (verified, web · example.org) — [Y] to accept"
    v = to_view(rec(reason="different name returned"))
    assert terminal_suggestion_line(v) == "Suggested (not verified): Rachael Sample — different name returned"


def _real_unidentified():
    from src import review
    mappings = {}
    review.mark_unidentified(mappings, [], "W", "m1")
    assert mappings["W"].local_slug.startswith("unidentified-")
    return mappings


def test_real_mark_unidentified_output_counts_as_unnamed():
    from src.name_suggest import unnamed_labels
    mappings = _real_unidentified()
    assert is_unnamed_mapping(mappings["W"])
    meeting = {"segments": [{"speaker_label": "W"}],
               "speakers": {"W": {k: getattr(mappings["W"], k) for k in
                                  ("speaker_name", "local_slug", "speaker_status", "politician_id", "politician_slug")}}}
    assert unnamed_labels(meeting) == {"W"}


def test_unidentified_still_excludes_real_links_and_non_speakers():
    assert not is_unnamed_mapping({"speaker_name": "X", "speaker_status": "unidentified", "politician_id": "p"})
    assert not is_unnamed_mapping({"speaker_name": "X", "speaker_status": "non_speaker", "local_slug": "unidentified-a"})


def test_views_for_unnamed_shows_unidentified_speaker(tmp_path):
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [rec("W")]}))
    views, _ = views_for_unnamed(tmp_path, _real_unidentified(), ["W"])
    assert set(views) == {"W"}


def test_corrupt_records_are_skipped_not_raised(tmp_path):
    bad = [
        {"label": "A", "lookup": "oops", "spoken_name": "Ann Lee"},
        {"label": "B", "evidence": ["str", 3, None], "lookup": {"name": "Bob Ray"}},
        {"label": 5, "lookup": {"name": "Zed"}},
        {"label": "C", "lookup": [1], "evidence": "nope"},
        {"label": "D", "lookup": {"name": 7}, "spoken_name": None},
    ]
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [rec("W")] + bad}))
    views, _ = views_for_unnamed(tmp_path, {}, ["W", "A", "B", "5", "C", "D"])
    assert "W" in views and 5 not in views and "D" not in views
    assert views["B"].quotes == [] and views["A"].name == "Ann Lee"

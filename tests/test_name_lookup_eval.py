from __future__ import annotations

from src.name_suggestion_eval import score_lookup_rows


def test_score_lookup_rows():
    rows = [
        {"gold": "Bob Costello", "spoken": "Bob Gasillo", "looked_up": "Bob Costello", "verified": True, "status": "verified"},
        {"gold": "Rachael Sample", "spoken": "Rachel Sample", "looked_up": "Rachel Sample", "verified": True, "status": "verified"},
        {"gold": "Ann Lee", "spoken": "Ann Lee", "looked_up": None, "verified": False, "status": "not_found"},
        {"gold": "Jo Fox", "spoken": "Joe Fox", "looked_up": "Joe Fox", "verified": False, "status": "not_verified"},
        {"gold": "Kim Wu", "spoken": "Kim Woo", "looked_up": None, "verified": False, "status": "unavailable"},
        {"gold": "Al Ray", "spoken": "Al Ray", "looked_up": None, "verified": False, "status": "failed"},
    ]
    s = score_lookup_rows(rows)
    assert s["n"] == 6 and s["spoken_exact"] == 2 and s["final_exact"] == 3
    assert s["verified"] == 2 and s["verified_exact"] == 1 and s["verified_precision"] == 0.5
    assert s["not_found"] == 1 and s["unavailable"] == 1 and s["failed"] == 1
    assert s["verified_wrong"] == 1 and s["regressed"] == 0
    assert s["rescued"] == 1 and s["unverified_spoken_exact"] == 2


def test_score_lookup_rows_regressed():
    rows = [{"gold": "Ann Lee", "spoken": "Ann Lee", "looked_up": "Anne Leigh", "verified": True,
             "status": "verified"}]
    s = score_lookup_rows(rows)
    assert s["regressed"] == 1 and s["verified_wrong"] == 1 and s["rescued"] == 0
    assert s["final_exact"] == 0 and s["spoken_exact"] == 1


# ---- select_witnesses ----
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_name_lookup", Path(__file__).resolve().parent.parent / "scripts" / "eval_name_lookup.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_FILLER = "and I want to thank the committee for hearing this bill today and for the time."


def _meeting(kind, people, city="Bloomington", state=None):
    """people: list of (label, spoken, gold, politician_id)."""
    segs, speakers = [], {}
    for i, (label, spoken, gold, pid) in enumerate(people):
        segs.append({"segment_id": i, "start_time": i * 10.0, "end_time": i * 10.0 + 9,
                     "speaker_label": label, "speaker_name": gold, "id_method": "human_review",
                     "text": f"Good afternoon. My name is {spoken}. {_FILLER}"})
        speakers[label] = {"politician_id": pid} if pid else {}
    m = {"event_kind": kind, "city": city, "segments": segs, "speakers": speakers}
    if state:
        m["state"] = state
    return m


def _write(root, name, meeting):
    d = root / name
    d.mkdir()
    (d / "transcript_named.json").write_text(json.dumps(meeting))


def test_select_witnesses(tmp_path):
    _write(tmp_path, "m1", _meeting("council", [
        ("A", "Ted Simons", "Ted Simmons", None),
        ("B", "Mary Jones", "Mary Jones", "pol-1"),      # politician-linked: excluded
    ], state="IN"))
    # a role gold: "Moderator" maps to the __ROLE__ sentinel
    _write(tmp_path, "m2", _meeting("forum", [
        ("A", "Rita Cole", "Rita Coal", None),
        ("B", "Sam Hill", "Moderator", None),            # ROLE gold: excluded
    ], city=None))
    _write(tmp_path, "m3", _meeting("forum", [("A", "Lou Park", "Lou Parke", None)]))
    one = _mod.select_witnesses(tmp_path, 10, 7)
    assert one == _mod.select_witnesses(tmp_path, 10, 7)
    golds = {r["gold"] for r in one}
    assert "Mary Jones" not in golds and "Moderator" not in golds and "Sam Hill" not in golds
    assert {"Ted Simmons", "Rita Coal", "Lou Parke"} <= golds
    by = {r["gold"]: r for r in one}
    assert by["Ted Simmons"]["place"] == "Bloomington, IN"
    assert by["Rita Coal"]["place"] is None
    assert by["Lou Parke"]["place"] == "Bloomington"
    # fill-to-sample across kinds
    two = _mod.select_witnesses(tmp_path, 2, 7)
    assert len(two) == 2 and {r["event_kind"] for r in two} == {"council", "forum"}

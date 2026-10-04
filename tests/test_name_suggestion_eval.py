from __future__ import annotations

from src.name_candidates import Candidate
from src.name_suggestion_eval import gold_labels, score_meeting, strip_names, summarize


def cand(label, name, tier="medium", partial=False, conflict=None):
    return Candidate(label=label, name=name, tier=tier, role=None, titled=False,
                     partial=partial, affiliation=None, evidence=[], conflict=conflict)


MEETING = {"segments": [
    {"segment_id": 0, "start_time": 0, "end_time": 1, "speaker_label": "A", "text": "hi",
     "speaker_name": "Rachel Sample", "id_method": "human_review"},
    {"segment_id": 1, "start_time": 1, "end_time": 2, "speaker_label": "B", "text": "yo",
     "speaker_name": "Host (Unknown - CRG)", "id_method": "human_review"},
    {"segment_id": 2, "start_time": 2, "end_time": 3, "speaker_label": "C", "text": "x",
     "speaker_name": "Voice Match", "id_method": "voice_profile"},
]}


def test_gold_labels_human_review_only_and_junk_is_none():
    assert gold_labels(MEETING) == {"A": "Rachel Sample", "B": None}


def test_strip_names_clears_identity():
    segs = strip_names(MEETING)
    assert all(s.speaker_name is None and s.id_method is None for s in segs)
    assert [s.text for s in segs] == ["hi", "yo", "x"]


def test_score_outcomes():
    gold = {"A": "Rachel Sample", "B": None, "C": "Peter Berezin", "D": "Ann Lee", "E": "Jo Fox"}
    cands = {"A": cand("A", "Rachel Sample", "strong"), "B": cand("B", "Ted Simons", "weak"),
             "C": cand("C", "Peter Pearson"), "E": cand("E", "Jo", partial=True)}
    rows = {r["label"]: r for r in score_meeting(gold, cands, "council")}
    assert rows["A"]["outcome"] == "correct" and rows["A"]["exact"] and rows["A"]["tier"] == "strong"
    assert rows["B"]["outcome"] == "hallucination"
    assert rows["C"]["outcome"] == "misspelled"
    assert rows["D"]["outcome"] == "miss" and rows["D"]["tier"] == "none"
    assert rows["E"]["outcome"] == "miss" and rows["E"]["tier"] == "hint" and rows["E"]["hint"] == "Jo"


def test_summarize_and_prefill_bar():
    rows = ([{"tier": "strong", "outcome": "correct", "exact": True}] * 99
            + [{"tier": "strong", "outcome": "wrong", "exact": False}])
    s = summarize(rows, "tier")["strong"]
    assert s["predicted"] == 100 and s["precision"] == 0.99 and s["bad_rate"] == 0.01
    assert s["passes_prefill_bar"] is True
    rows.append({"tier": "strong", "outcome": "hallucination", "exact": False})
    rows += [{"tier": "strong", "outcome": "wrong", "exact": False}] * 2
    assert summarize(rows, "tier")["strong"]["passes_prefill_bar"] is False

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
    assert rows["C"]["outcome"] == "wrong"  # Berezin/Pearson ratio=0.429 < 0.5, so wrong not misspelled
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


def test_misspelled_needs_two_tokens_and_surname_similarity():
    """Misspelled only when both names ≥2 sig tokens, first match, surnames ≥0.5 similar."""
    gold = {
        "A": "Mr. Smith", "B": "Dr. Liz Brown", "C": "Liz Brown",
        "D": "Brown", "E": "Bill Nelson", "F": "Jan Sorbee"
    }
    cands = {
        "A": cand("A", "Mr. Jones"),  # Different surnames (Smith vs Jones, low ratio ~0.2)
        "B": cand("B", "Dr. Liz Green"),  # Different surnames (Brown vs Green, low ratio ~0.4)
        "C": cand("C", "Liz Green"),  # Different surnames (Brown vs Green, low ratio ~0.4)
        "D": cand("D", "Brown Smith"),  # Gold is 1 token, pred is 2 tokens -> wrong
        "E": cand("E", "Bill Nelsen"),  # Nelson vs Nelsen ratio=0.833 (0.5<=r<0.85) -> misspelled
        "F": cand("F", "Jan Sorby"),  # Sorbee vs Sorby ratio=0.727 (0.5<=r<0.85) -> misspelled
    }
    rows = {r["label"]: r for r in score_meeting(gold, cands, "council")}
    assert rows["A"]["outcome"] == "wrong", "Smith/Jones should be wrong (low surname similarity)"
    assert rows["B"]["outcome"] == "wrong", "Brown/Green should be wrong (low surname similarity)"
    assert rows["C"]["outcome"] == "wrong", "Brown/Green should be wrong (low surname similarity)"
    assert rows["D"]["outcome"] == "wrong", "Gold 1-token should be wrong"
    assert rows["E"]["outcome"] == "misspelled", "Nelson/Nelsen should be misspelled (ratio=0.833)"
    assert rows["F"]["outcome"] == "misspelled", "Sorbee/Sorby should be misspelled (ratio=0.727)"


def test_gold_junk_keeps_real_titled_names():
    """Real titles/names are kept; junk names (unknowns, candidates, indexed speakers) are None."""
    # Real names to keep
    assert gold_labels({"segments": [
        {"segment_id": 0, "speaker_label": "A", "speaker_name": "Nancy Pelosi (D)", "id_method": "human_review"},
        {"segment_id": 1, "speaker_label": "B", "speaker_name": "Mary O'Neil (Chair)", "id_method": "human_review"},
        {"segment_id": 2, "speaker_label": "C", "speaker_name": "Speaker Pelosi", "id_method": "human_review"},
        {"segment_id": 3, "speaker_label": "D", "speaker_name": "Speaker Huston", "id_method": "human_review"},
    ]}) == {"A": "Nancy Pelosi (D)", "B": "Mary O'Neil (Chair)", "C": "Speaker Pelosi", "D": "Speaker Huston"}

    # Junk names to discard
    assert gold_labels({"segments": [
        {"segment_id": 0, "speaker_label": "A", "speaker_name": "Candidate7", "id_method": "human_review"},
        {"segment_id": 1, "speaker_label": "B", "speaker_name": "Host (Unknown - CRG)", "id_method": "human_review"},
        {"segment_id": 2, "speaker_label": "C", "speaker_name": "SPEAKER_03", "id_method": "human_review"},
        {"segment_id": 3, "speaker_label": "D", "speaker_name": "Unidentified Speaker", "id_method": "human_review"},
    ]}) == {"A": None, "B": None, "C": None, "D": None}


def test_gold_labels_first_usable_name():
    """gold_labels takes the first usable human_review name for a label, skipping junk."""
    assert gold_labels({"segments": [
        {"segment_id": 0, "speaker_label": "X", "speaker_name": "Host (Unknown)", "id_method": "human_review"},
        {"segment_id": 1, "speaker_label": "X", "speaker_name": "Alex Chen", "id_method": "human_review"},
    ]}) == {"X": "Alex Chen"}, "Should skip junk and use first real name"


def test_misspelled_asr_keeps_first_letter_of_surname():
    """ASR mishearings keep the first sound: Murfree/Murphy is misspelled; Berezin/Pearson is not."""
    gold = {"A": "Lauren Murfree", "B": "Peter Berezin", "C": "Liz Brown"}
    cands = {
        "A": cand("A", "Lauren Murphy"),
        "B": cand("B", "Peter Pearson"),
        "C": cand("C", "Liz Green"),
    }
    rows = {r["label"]: r for r in score_meeting(gold, cands, "council")}
    assert rows["A"]["outcome"] == "misspelled"
    assert rows["B"]["outcome"] == "wrong"
    assert rows["C"]["outcome"] == "wrong"

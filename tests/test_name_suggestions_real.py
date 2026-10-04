# tests/test_name_suggestions_real.py
"""End-to-end on real excerpts: Indiana Senate Judiciary 2026-01-14 + Bloomington 2026-05-06."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.name_candidates import build_candidates
from src.name_evidence import extract_evidence
from src.name_suggestion_eval import gold_labels, score_meeting, strip_names

FIX = Path(__file__).parent / "fixtures" / "name_suggestions"
TARGETS = {
    "in_senate_judiciary_2026_01_14.json": ["Rachael Sample", "Lauren Murfree", "Aaron Spiegel"],
    "bloomington_2026_05_06.json": ["Jordan Evans", "Michael Brahms", "Doug Horne"],
}


@pytest.mark.parametrize("fixture", sorted(TARGETS))
def test_witnesses_are_found_on_the_right_label(fixture):
    meeting = json.loads((FIX / fixture).read_text())
    cands = build_candidates(extract_evidence(strip_names(meeting)), meeting["event_kind"])
    rows = {r["gold"]: r for r in score_meeting(gold_labels(meeting), cands, meeting["event_kind"])}
    for target in TARGETS[fixture]:
        gold = next(g for g in rows if g and g.split()[-1] == target.split()[-1])
        assert rows[gold]["outcome"] in ("correct", "misspelled"), rows[gold]


@pytest.mark.parametrize("fixture", sorted(TARGETS))
def test_no_confident_wrong_names_in_excerpt(fixture):
    meeting = json.loads((FIX / fixture).read_text())
    cands = build_candidates(extract_evidence(strip_names(meeting)), meeting["event_kind"])
    rows = score_meeting(gold_labels(meeting), cands, meeting["event_kind"])
    bad = [r for r in rows if r["outcome"] in ("wrong", "hallucination") and r["tier"] in ("strong", "medium")]
    assert bad == []

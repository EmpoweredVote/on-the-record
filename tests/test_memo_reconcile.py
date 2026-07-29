"""Reconcile-planner tests: parsed memo + row snapshots -> planned writes.

The end-to-end test runs the REAL July 22 memo fixture through parse_memo
and asserts the full plan against hand-checked ground truth.
"""
from pathlib import Path

import pytest

from src.memo_parse import parse_memo
from src.memo_reconcile import (
    AgendaItemRow, SpeakerRow, build_reconcile_plan, diff_plan_against_db,
    match_speaker,
)

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-22.txt"

# July 22's real council speakers (display names as published).
SPEAKERS = [
    SpeakerRow("s-asare", "Isak Nti Asare"),
    SpeakerRow("s-daily", "Courtney Daily"),
    SpeakerRow("s-flaherty", "Matt Flaherty"),
    SpeakerRow("s-rosenbarger", "Kate Rosenbarger"),
    SpeakerRow("s-stosberg", "Hopi Stosberg"),
    SpeakerRow("s-piedmont", "Isabel Piedmont-Smith"),
    SpeakerRow("s-rollo", "Dave Rollo"),
    SpeakerRow("s-ruff", "Andy Ruff"),
    SpeakerRow("s-bolden", "Nicole Bolden"),
]


@pytest.fixture(scope="module")
def memo():
    return parse_memo(FIXTURE.read_text())


def test_match_speaker_last_name_suffix():
    assert match_speaker("Asare", SPEAKERS)[0] == "s-asare"          # multi-word surname
    assert match_speaker("Piedmont-Smith", SPEAKERS)[0] == "s-piedmont"
    assert match_speaker("piedmont-smith", SPEAKERS)[0] == "s-piedmont"


def test_match_speaker_missing_and_ambiguous():
    sid, note = match_speaker("Zulich", SPEAKERS)   # absent that night
    assert sid is None and "Zulich" in note
    two_smiths = SPEAKERS + [SpeakerRow("s-x", "Jane Piedmont-Smith")]
    sid, note = match_speaker("Piedmont-Smith", two_smiths)
    assert sid is None and "ambiguous" in note


def test_july22_plan_end_to_end(memo):
    agenda_items = [
        AgendaItemRow("i-15", 10, "Ordinance 2026-15", None),
        AgendaItemRow("i-r12", 11, "Resolution 2026-12", None),
        AgendaItemRow("i-r13", 12, "Resolution 2026-13", None),
        AgendaItemRow("i-o12", 13, "Ordinance 2026-12", "passed"),  # wrong; memo overwrites
        AgendaItemRow("i-noref", 1, None, None),
    ]
    plan = build_reconcile_plan(memo, agenda_items, SPEAKERS)

    assert sorted(plan.outcome_updates) == sorted([
        ("continued", "i-15"), ("continued", "i-r12"),
        ("passed", "i-r13"), ("failed", "i-o12"),
    ])

    assert [(v.resolution, v.result, v.agenda_item_id) for v in plan.votes] == [
        ("Ordinance 2026-15", "Continued 8–0", "i-15"),
        ("Resolution 2026-12", "Continued 8–0", "i-r12"),
        ("Resolution 2026-13", "Passed 8–0", "i-r13"),
        ("Ordinance 2026-12", "Failed 4–4", "i-o12"),
    ]
    for v in plan.votes:
        assert "moved" in v.description and "seconded" in v.description

    records = {v.resolution: v.records for v in plan.votes}
    assert records["Ordinance 2026-15"] == []
    assert records["Resolution 2026-13"] == []
    assert sorted(records["Ordinance 2026-12"]) == sorted([
        ("s-asare", "aye"), ("s-daily", "aye"),
        ("s-flaherty", "aye"), ("s-rosenbarger", "aye"),
        ("s-stosberg", "nay"), ("s-piedmont", "nay"),
        ("s-rollo", "nay"), ("s-ruff", "nay"),
    ])

    assert any("continued to 2026-07-29" in n for n in plan.notes)


def test_memo_ref_without_agenda_item_still_votes_no_update(memo):
    plan = build_reconcile_plan(memo, [], SPEAKERS)   # July 22 reality: no items
    assert plan.outcome_updates == []
    assert len(plan.votes) == 4
    assert all(v.agenda_item_id is None for v in plan.votes)
    assert any("no agenda item" in n for n in plan.notes)


def test_unmatched_member_skipped_with_note(memo):
    thin = [s for s in SPEAKERS if s.id != "s-ruff"]  # Ruff never spoke
    plan = build_reconcile_plan(memo, [], thin)
    split = [v for v in plan.votes if v.resolution == "Ordinance 2026-12"][0]
    assert len(split.records) == 7
    assert any("Ruff" in n for n in plan.notes)


def test_duplicate_agenda_refs_abstain(memo):
    dupes = [
        AgendaItemRow("i-a", 1, "Resolution 2026-13", None),
        AgendaItemRow("i-b", 2, "Resolution 2026-13", None),
    ]
    plan = build_reconcile_plan(memo, dupes, SPEAKERS)
    assert plan.outcome_updates == []
    r13 = [v for v in plan.votes if v.resolution == "Resolution 2026-13"][0]
    assert r13.agenda_item_id is None
    assert any("share this ref" in n for n in plan.notes)


def test_diff_clean_when_db_matches(memo):
    plan = build_reconcile_plan(memo, [], SPEAKERS)
    existing = [(v.resolution, v.result, len(v.records)) for v in plan.votes]
    assert diff_plan_against_db(plan, [], existing) == []


def test_diff_flags_missing_extra_and_outcome(memo):
    items = [AgendaItemRow("i-r13", 12, "Resolution 2026-13", "failed")]  # wrong outcome
    plan = build_reconcile_plan(memo, items, SPEAKERS)
    existing = [(v.resolution, v.result, len(v.records)) for v in plan.votes][1:]  # one missing
    existing.append(("Ordinance 9999-9", "Passed 1–0", 0))                          # one extra
    drift = diff_plan_against_db(plan, items, existing)
    assert any("missing" in d for d in drift)
    assert any("Ordinance 9999-9" in d and "unexpected" in d for d in drift)
    assert any("Resolution 2026-13" in d and "outcome" in d for d in drift)


def test_tie_without_tag_gets_bare_tally_result():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-98\n"
        "Daily moved and Ruff seconded that Ordinance 2026-98 be adopted. "
        "The motion received a roll call vote of Ayes: 4, Nays: 4, Abstain: 0.\n"
    )
    plan = build_reconcile_plan(parse_memo(text), [], [])
    assert plan.votes[0].result == "4–4"
    assert any("no verdict" in n for n in plan.notes)

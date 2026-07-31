"""Reconcile-planner tests: parsed memo + row snapshots -> planned writes.

The end-to-end test runs the REAL July 22 memo fixture through parse_memo
and asserts the full plan against hand-checked ground truth.
"""
from pathlib import Path

import pytest

from src.memo_parse import parse_memo
from src.memo_reconcile import (
    AgendaItemRow, PlannedVote, ReconcilePlan, SpeakerRow, build_reconcile_plan,
    diff_plan_against_db, match_speaker,
)

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-22.txt"
FIXTURE_JUNE10 = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-06-10.txt"
FIXTURE_JULY29 = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-29.txt"

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

# June 10 had the full nine-member council present (Zulich voted).
SPEAKERS_JUNE10 = SPEAKERS + [SpeakerRow("s-zulich", "Sydney Zulich")]


@pytest.fixture(scope="module")
def memo():
    return parse_memo(FIXTURE.read_text())


@pytest.fixture(scope="module")
def memo_june10():
    return parse_memo(FIXTURE_JUNE10.read_text())


@pytest.fixture(scope="module")
def memo_july29():
    return parse_memo(FIXTURE_JULY29.read_text())


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


def test_diff_reports_one_line_per_duplicate_missing_vote():
    # Two identical planned votes, nothing in the DB: Counter subtraction
    # preserves multiplicity, so this must yield two "missing" lines, not one.
    plan = ReconcilePlan(votes=[
        PlannedVote("Ordinance 2026-1", "moved and seconded", "Passed 8–0", "i-1"),
        PlannedVote("Ordinance 2026-1", "moved and seconded", "Passed 8–0", "i-2"),
    ])
    drift = diff_plan_against_db(plan, [], [])
    missing = [d for d in drift if "missing" in d]
    assert len(missing) == 2


def test_june10_plan_votes_end_to_end(memo_june10):
    plan = build_reconcile_plan(memo_june10, [], SPEAKERS_JUNE10)

    # 9 rows in memo order: 7 dispositive motions + 2 amendment rows; within
    # an item the adopt motion precedes its amendment (motion order).
    assert [(v.resolution, v.result) for v in plan.votes] == [
        ("Resolution 2026-10", "Passed 9–0"),
        ("Ordinance 2026-12", "Passed 5–4"),
        ("Ordinance 2026-12", "Passed 9–0"),
        ("Resolution 2026-09", "Passed 9–0"),
        ("Ordinance 2026-13", "Passed 7–0"),
        ("Ordinance 2026-13", "Passed 8–0"),
        ("Resolution 2026-11", "Passed 9–0"),
        ("Resolution 2026-12", "Continued 9–0"),
        ("Ordinance 2026-14", "Passed 7–2"),
    ]

    o12_adoption, o12_amendment = [
        v for v in plan.votes if v.resolution == "Ordinance 2026-12"
    ]
    assert sorted(o12_adoption.records) == sorted([
        ("s-asare", "aye"), ("s-daily", "aye"), ("s-flaherty", "aye"),
        ("s-rollo", "aye"), ("s-rosenbarger", "aye"),
        ("s-stosberg", "nay"), ("s-piedmont", "nay"),
        ("s-zulich", "nay"), ("s-ruff", "nay"),
    ])
    assert o12_amendment.records == []

    # "out of the room" annotations were dropped by the parser's guard.
    o13_rows = [v for v in plan.votes if v.resolution == "Ordinance 2026-13"]
    assert all(v.records == [] for v in o13_rows)

    o14 = [v for v in plan.votes if v.resolution == "Ordinance 2026-14"][0]
    assert sorted(o14.records) == sorted([
        ("s-asare", "nay"), ("s-rosenbarger", "nay"),
    ])

    assert any("continued to 2026-07-22" in n for n in plan.notes)


def test_june10_outcomes_and_amend_attachment(memo_june10):
    agenda = [
        AgendaItemRow("i-r10", 1, "Resolution 2026-10", None),
        AgendaItemRow("i-o12", 2, "Ordinance 2026-12", None),
        AgendaItemRow("i-r09", 3, "Resolution 2026-09", None),
        AgendaItemRow("i-o13", 4, "Ordinance 2026-13", None),
        AgendaItemRow("i-r11", 5, "Resolution 2026-11", None),
        AgendaItemRow("i-r12", 6, "Resolution 2026-12", None),
        AgendaItemRow("i-o14", 7, "Ordinance 2026-14", None),
    ]
    plan = build_reconcile_plan(memo_june10, agenda, SPEAKERS_JUNE10)

    assert sorted(plan.outcome_updates) == sorted([
        ("passed", "i-r10"), ("passed", "i-o12"), ("passed", "i-r09"),
        ("passed", "i-o13"), ("passed", "i-r11"), ("continued", "i-r12"),
        ("passed", "i-o14"),
    ])

    # Both the adoption and its amendment attach to the same agenda item.
    o12_rows = [v for v in plan.votes if v.resolution == "Ordinance 2026-12"]
    assert [v.agenda_item_id for v in o12_rows] == ["i-o12", "i-o12"]

    # Every ref matched exactly — the bare-number fallback must not fire.
    assert not any("bare number" in n for n in plan.notes)


def test_amend_vote_row_never_dispositive():
    text = (
        "7. Legislation [7:00pm]\n"
        "7.1. Ordinance 2026-50\n"
        "Daily moved and Zulich seconded to adopt Amendment 01 to Ordinance 2026-50. "
        "The motion received a roll call vote of Ayes: 9, Nays: 0, Abstain: 0.\n"
    )
    agenda = [AgendaItemRow("i-a", 1, "Ordinance 2026-50", None)]
    plan = build_reconcile_plan(parse_memo(text), agenda, [])
    assert [(v.result, v.agenda_item_id) for v in plan.votes] == [
        ("Passed 9–0", "i-a"),
    ]
    assert plan.outcome_updates == []  # amendments never settle the item


def test_july29_number_fallback_end_to_end(memo_july29):
    agenda = [
        AgendaItemRow("i-o16", 1, "Ordinance 2026-16", None),
        AgendaItemRow("i-o17", 2, "Ordinance 2026-17", None),
        AgendaItemRow("i-o15", 3, "Ordinance 2026-15", None),
    ]
    plan = build_reconcile_plan(memo_july29, agenda, SPEAKERS)

    # The memo calls Ordinance 2026-15 "Resolution 2026-15" throughout; the
    # bare-number fallback bridges the clerk's ref-type mislabel.
    assert sorted(plan.outcome_updates) == sorted([
        ("continued", "i-o16"), ("continued", "i-o17"), ("passed", "i-o15"),
    ])

    assert [(v.resolution, v.result, v.agenda_item_id) for v in plan.votes] == [
        ("Ordinance 2026-16", "Continued 8–0", "i-o16"),
        ("Ordinance 2026-17", "Continued 8–0", "i-o17"),
        ("Resolution 2026-15", "Passed 8–0", "i-o15"),
    ]

    assert any(
        "Resolution 2026-15" in n and "Ordinance 2026-15" in n for n in plan.notes
    )
    assert "Ordinance 2026-16: continued to 2026-08-05" in plan.notes
    assert "Ordinance 2026-17: continued to 2026-08-05" in plan.notes


def test_number_fallback_refuses_memo_side_collision(memo_june10):
    # June 10 has BOTH Ordinance 2026-12 and Resolution 2026-12. With only
    # the Resolution on the agenda, the Ordinance's bare number matches two
    # memo refs — the fallback must refuse, not guess.
    agenda = [AgendaItemRow("i-r12", 1, "Resolution 2026-12", None)]
    plan = build_reconcile_plan(memo_june10, agenda, SPEAKERS_JUNE10)

    assert plan.outcome_updates == [("continued", "i-r12")]
    o12_rows = [v for v in plan.votes if v.resolution == "Ordinance 2026-12"]
    assert o12_rows and all(v.agenda_item_id is None for v in o12_rows)
    assert any(
        "Ordinance 2026-12" in n and "not unique" in n for n in plan.notes
    )


def test_number_fallback_refuses_agenda_side_collision():
    text = (
        "7. Legislation [7:00pm]\n"
        "7.1. Resolution 2026-15\n"
        "Daily moved and Zulich seconded that Resolution 2026-15 be adopted. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    agenda = [
        AgendaItemRow("i-a", 1, "Ordinance 2026-15", None),
        AgendaItemRow("i-b", 2, "Appropriation Ordinance 2026-15", None),
    ]
    plan = build_reconcile_plan(parse_memo(text), agenda, [])
    assert plan.outcome_updates == []
    assert plan.votes[0].agenda_item_id is None
    assert any("not unique" in n for n in plan.notes)


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

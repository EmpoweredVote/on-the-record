"""Calibration tests for the deterministic clerk-memorandum parser.

Pinned against the REAL July 22, 2026 memo fixture (the first benchmark
memo). Ground truth was hand-checked from the memo text; see the design
spec 2026-07-28-clerk-memo-reconciler-design.md.
"""
from pathlib import Path

import pytest

from src.memo_parse import parse_memo

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-22.txt"


@pytest.fixture(scope="module")
def memo():
    return parse_memo(FIXTURE.read_text())


def _item(memo, ref):
    matches = [i for i in memo.items if i.legislation_ref == ref]
    assert len(matches) == 1, f"expected exactly one item for {ref}"
    return matches[0]


def test_finds_all_four_legislation_items(memo):
    refs = [i.legislation_ref for i in memo.items]
    assert refs == [
        "Ordinance 2026-15", "Resolution 2026-12",
        "Resolution 2026-13", "Ordinance 2026-12",
    ]


def test_ord_2026_15_continued_with_date(memo):
    item = _item(memo, "Ordinance 2026-15")
    assert item.disposition == "continued"
    motion = item.motions[item.disposition_motion]
    assert motion.kind == "continue"
    assert motion.continued_to_date == "2026-07-29"
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (8, 0, 0)


def test_clerk_typo_stays_in_2026_15_scope(memo):
    # "The motion to discuss Ordinance 2026-13 received..." is a clerk typo
    # INSIDE the 2026-15 subsection. Attribution is by subsection, so
    # 2026-15 has all three motions and 2026-13 has none of them.
    item = _item(memo, "Ordinance 2026-15")
    assert len(item.motions) == 3  # introduce, discuss, postpone
    assert [m.kind for m in item.motions] == ["procedural", "procedural", "continue"]


def test_res_2026_12_tabled_unvoted_adoption_is_not_dispositive(memo):
    item = _item(memo, "Resolution 2026-12")
    assert item.disposition == "continued"
    kinds = [m.kind for m in item.motions]
    assert kinds == ["procedural", "adopt", "continue"]
    assert item.motions[1].tally is None            # moved, never voted
    assert item.disposition_motion == 2             # the table motion


def test_res_2026_13_passed(memo):
    item = _item(memo, "Resolution 2026-13")
    assert item.disposition == "passed"
    motion = item.motions[item.disposition_motion]
    assert motion.kind == "adopt"
    assert (motion.tally.ayes, motion.tally.nays) == (8, 0)
    assert motion.ayes_names == []                  # unnamed unanimous tally


def test_ord_2026_12_failed_with_named_sides(memo):
    item = _item(memo, "Ordinance 2026-12")
    assert item.disposition == "failed"
    motion = item.motions[item.disposition_motion]
    assert motion.failed_tag is True
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (4, 4, 0)
    assert motion.ayes_names == ["Asare", "Daily", "Flaherty", "Rosenbarger"]
    assert motion.nays_names == ["Stosberg", "Piedmont-Smith", "Rollo", "Ruff"]
    assert motion.abstain_names == []


def test_action_history_block_yields_no_motions(memo):
    # 7.3's "Actions on Legislation: Council Action (June 10, 2026): Passed
    # Ayes: 5 (...)" is history, not a motion at this meeting.
    item = _item(memo, "Ordinance 2026-12")
    assert len(item.motions) == 2  # introduce + adopt only


def test_section_wallclocks(memo):
    by_ref = {i.legislation_ref: i.section_wallclock for i in memo.items}
    assert by_ref["Ordinance 2026-15"] == "6:54pm"   # First Readings section
    assert by_ref["Resolution 2026-12"] == "7:01pm"  # Second Readings section


# --- synthetic edge cases -------------------------------------------------

def test_unparseable_motion_abstains_with_note():
    text = (
        "5. Legislation for Second Readings and Resolutions [7:00pm]\n"
        "5.1. Ordinance 2026-99\n"
        "Something About Streets\n"
        "Daily moved, and Ruff seconded that Ordinance 2026-99 be frobnicated. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.disposition is None
    assert item.motions[0].kind == "unknown"
    assert any("frobnicated" in n or "unknown" in n for n in item.notes)


def test_adopt_tie_without_failed_tag_abstains():
    text = (
        "5. Legislation for Second Readings and Resolutions [7:00pm]\n"
        "5.1. Ordinance 2026-98\n"
        "Daily moved and Ruff seconded that Ordinance 2026-98 be adopted. "
        "The motion received a roll call vote of Ayes: 4, Nays: 4, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.disposition is None
    assert any("neither" in n.lower() or "tie" in n.lower() for n in item.notes)


def test_passed_tag_variant_and_withdraw():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Resolution 2026-97\n"
        "Daily moved and Ruff seconded that Resolution 2026-97 be adopted. "
        "The motion received a roll call vote: Ayes: 5 (A, B, C, D, E); "
        "Nays: 3 (F, G, H); Abstain: 0. PASSED\n"
        "5.2. Resolution 2026-96\n"
        "Daily moved and Ruff seconded to withdraw Resolution 2026-96. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    assert memo.items[0].disposition == "passed"
    assert memo.items[0].motions[0].ayes_names == ["A", "B", "C", "D", "E"]
    assert memo.items[1].disposition == "pulled"


def test_timetable_does_not_read_as_tabling():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Resolution 2026-90\n"
        "Daily moved and Ruff seconded to accept the revised timetable for "
        "Resolution 2026-90. The motion received a roll call vote of "
        "Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.motions[0].kind == "unknown"
    assert item.disposition is None


def test_continue_motion_that_did_not_carry_abstains_with_note():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-95\n"
        "Daily moved and Ruff seconded to postpone consideration of "
        "Ordinance 2026-95 until August 5, 2026. The motion received a "
        "roll call vote of Ayes: 3, Nays: 5, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.disposition is None
    assert any("did not carry" in n for n in item.notes)


def test_empty_text_yields_no_items_with_note():
    memo = parse_memo("")
    assert memo.items == [] and any("template drift" in n for n in memo.notes)

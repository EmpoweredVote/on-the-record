"""Calibration tests for the deterministic clerk-memorandum parser.

Pinned against the REAL July 22, 2026 memo fixture (the first benchmark
memo), plus the June 10 and July 29, 2026 fixtures (amendment pattern,
names/count guard, first-reading referral). Ground truth was hand-checked
from the memo text; see the design spec
2026-07-28-clerk-memo-reconciler-design.md and the plan
2026-07-31-memo-parser-v2.md.
"""
from pathlib import Path

import pytest

from src.memo_parse import parse_memo

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-22.txt"
FIXTURE_JUNE10 = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-06-10.txt"
FIXTURE_JULY29 = Path(__file__).parent / "fixtures" / "onboard" / "memo_2026-07-29.txt"


@pytest.fixture(scope="module")
def memo():
    return parse_memo(FIXTURE.read_text())


@pytest.fixture(scope="module")
def memo_june10():
    return parse_memo(FIXTURE_JUNE10.read_text())


@pytest.fixture(scope="module")
def memo_july29():
    return parse_memo(FIXTURE_JULY29.read_text())


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


def test_action_history_block_yields_no_results_either(memo):
    # The "Actions on Legislation:" history in 7.3 ("Council Action
    # (June 10, 2026): Passed Ayes: 5 (...); Nays: 4 (...); 0") precedes
    # the first motion and lacks the "roll call vote" phrase — it must
    # never become a motion or contribute a tally to one.
    item = _item(memo, "Ordinance 2026-12")
    assert [m.kind for m in item.motions] == ["procedural", "adopt"]
    intro, adopt = item.motions
    assert (intro.tally.ayes, intro.tally.nays, intro.tally.abstain) == (8, 0, 0)
    assert (adopt.tally.ayes, adopt.tally.nays, adopt.tally.abstain) == (4, 4, 0)


# --- June 10, 2026 fixture (amendment pattern + names/count guard) --------

def test_june10_items_in_order(memo_june10):
    assert [i.legislation_ref for i in memo_june10.items] == [
        "Resolution 2026-10", "Ordinance 2026-12", "Resolution 2026-09",
        "Ordinance 2026-13", "Resolution 2026-11", "Resolution 2026-12",
        "Ordinance 2026-14",
    ]


def test_june10_ord_2026_12_amended_then_adopted_5_4(memo_june10):
    item = _item(memo_june10, "Ordinance 2026-12")
    assert [m.kind for m in item.motions] == ["procedural", "adopt", "amend"]
    assert item.disposition == "passed"
    assert item.disposition_motion == 1
    adopt = item.motions[1]
    assert (adopt.tally.ayes, adopt.tally.nays, adopt.tally.abstain) == (5, 4, 0)
    assert adopt.ayes_names == ["Asare", "Daily", "Flaherty", "Rollo", "Rosenbarger"]
    assert adopt.nays_names == ["Stosberg", "Piedmont-Smith", "Zulich", "Ruff"]
    amend = item.motions[2]
    assert (amend.tally.ayes, amend.tally.nays, amend.tally.abstain) == (9, 0, 0)
    assert amend.ayes_names == [] and amend.nays_names == [] and amend.abstain_names == []


def test_june10_ord_2026_13_out_of_room_annotation_dropped(memo_june10):
    item = _item(memo_june10, "Ordinance 2026-13")
    assert item.disposition == "passed"
    adopt = item.motions[item.disposition_motion]
    assert adopt.kind == "adopt"
    assert (adopt.tally.ayes, adopt.tally.nays, adopt.tally.abstain) == (7, 0, 0)
    # "(Rosenbarger, Ruff out of the room)" is a quorum annotation on a
    # zero side, not an abstain list — the names/count guard drops it.
    assert adopt.abstain_names == []
    amends = [m for m in item.motions if m.kind == "amend"]
    assert len(amends) == 1
    amend = amends[0]
    assert (amend.tally.ayes, amend.tally.nays, amend.tally.abstain) == (8, 0, 0)
    assert amend.ayes_names == [] and amend.nays_names == [] and amend.abstain_names == []


def test_june10_res_2026_09_agenda_amend_is_procedural(memo_june10):
    item = _item(memo_june10, "Resolution 2026-09")
    assert [m.kind for m in item.motions] == ["procedural", "adopt", "procedural"]
    assert item.disposition == "passed"
    motion = item.motions[item.disposition_motion]
    assert (motion.tally.ayes, motion.tally.nays) == (9, 0)


def test_june10_res_2026_12_continued(memo_june10):
    item = _item(memo_june10, "Resolution 2026-12")
    assert item.disposition == "continued"
    motion = item.motions[item.disposition_motion]
    assert motion.kind == "continue"
    assert motion.continued_to_date == "2026-07-22"


def test_june10_ord_2026_14_passed_with_named_nays(memo_june10):
    item = _item(memo_june10, "Ordinance 2026-14")
    assert item.disposition == "passed"
    motion = item.motions[item.disposition_motion]
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (7, 2, 0)
    assert motion.nays_names == ["Asare", "Rosenbarger"]


def test_june10_unanimous_resolutions_passed(memo_june10):
    for ref in ("Resolution 2026-10", "Resolution 2026-11"):
        item = _item(memo_june10, ref)
        assert item.disposition == "passed"
        motion = item.motions[item.disposition_motion]
        assert (motion.tally.ayes, motion.tally.nays) == (9, 0)


# --- July 29, 2026 fixture (first-reading referral) ------------------------

def test_july29_items_in_order(memo_july29):
    # "Resolution 2026-15" is the clerk's mislabel of Ordinance 2026-15 —
    # the parser reports it verbatim; correction is the reconciler's job.
    assert [i.legislation_ref for i in memo_july29.items] == [
        "Ordinance 2026-16", "Ordinance 2026-17", "Resolution 2026-15",
    ]


def test_july29_first_readings_referred_to_second_reading(memo_july29):
    for ref in ("Ordinance 2026-16", "Ordinance 2026-17"):
        item = _item(memo_july29, ref)
        assert item.disposition == "continued"
        motion = item.motions[item.disposition_motion]
        assert motion.kind == "continue"
        assert motion.continued_to_date == "2026-08-05"
        assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (8, 0, 0)


def test_july29_res_2026_15_passed(memo_july29):
    item = _item(memo_july29, "Resolution 2026-15")
    assert item.disposition == "passed"
    motion = item.motions[item.disposition_motion]
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (8, 0, 0)


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


def test_unmatchable_result_desc_dropped_with_note():
    # A second result sentence whose description matches no pending motion
    # (the adopt motion already has its vote) must be dropped, loudly.
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-94\n"
        "Daily moved and Ruff seconded that Ordinance 2026-94 be adopted. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0. "
        "The motion to frobnicate Ordinance 2026-94 received a roll call vote "
        "of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert len(item.motions) == 1
    motion = item.motions[0]
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (8, 0, 0)
    assert item.disposition == "passed"
    assert any("frobnicate" in n for n in item.notes)


def test_names_count_mismatch_drops_names_keeps_tally():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-93\n"
        "Daily moved and Ruff seconded that Ordinance 2026-93 be adopted. "
        "The motion received a roll call vote of Ayes: 7, Nays: 0, "
        "Abstain: 0 (Rosenbarger, Ruff out of the room).\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    motion = item.motions[0]
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (7, 0, 0)
    assert motion.abstain_names == []
    assert item.disposition == "passed"
    assert any("names" in n.lower() for n in item.notes)


def test_two_amendments_keep_their_own_tallies():
    # Each amendment's result sits in its OWN block — the block owner must
    # win over any last-unvoted-amend fallback, or the tallies swap.
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-92\n"
        "Daily moved and Ruff seconded that Ordinance 2026-92 be adopted.\n"
        "Daily moved and Ruff seconded to adopt Amendment 01 to Ordinance 2026-92. "
        "The motion to adopt Amendment 01 to Ordinance 2026-92 received a roll call "
        "vote of Ayes: 9, Nays: 0, Abstain: 0.\n"
        "Daily moved and Ruff seconded to adopt Amendment 02 to Ordinance 2026-92. "
        "The motion to adopt Amendment 02 to Ordinance 2026-92 received a roll call "
        "vote of Ayes: 5, Nays: 4, Abstain: 0. "
        "The motion to adopt Ordinance 2026-92 as amended received a roll call vote "
        "of Ayes: 8, Nays: 1, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert [m.kind for m in item.motions] == ["adopt", "amend", "amend"]
    amend1, amend2 = item.motions[1], item.motions[2]
    assert (amend1.tally.ayes, amend1.tally.nays, amend1.tally.abstain) == (9, 0, 0)
    assert (amend2.tally.ayes, amend2.tally.nays, amend2.tally.abstain) == (5, 4, 0)
    adopt = item.motions[0]
    assert (adopt.tally.ayes, adopt.tally.nays, adopt.tally.abstain) == (8, 1, 0)
    assert item.disposition == "passed" and item.disposition_motion == 0


def test_ambiguous_foreign_block_amendment_result_abstains():
    # An amendment result stranded in a foreign block, with TWO unvoted
    # amend motions that could claim it, must be dropped loudly.
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-91\n"
        "Daily moved and Ruff seconded to adopt Amendment 01 to Ordinance 2026-91.\n"
        "Daily moved and Ruff seconded to adopt Amendment 02 to Ordinance 2026-91.\n"
        "Daily moved and Ruff seconded that Ordinance 2026-91 be adopted. "
        "The motion to adopt Amendment 01 to Ordinance 2026-91 received a roll call "
        "vote of Ayes: 9, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert [m.kind for m in item.motions] == ["amend", "amend", "adopt"]
    assert all(m.tally is None for m in item.motions if m.kind == "amend")
    assert any("ambiguous" in n for n in item.notes)


def test_unparsed_roll_call_text_leaves_drift_note():
    # v1 matched any bare "roll call vote of Ayes:" text; the stricter
    # result frame must not fail SILENTLY — a desc containing "U.S."
    # breaks the no-period bound, leaving the motion unvoted.
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-90\n"
        "Daily moved and Ruff seconded that the U.S. 46 agreement for "
        "Ordinance 2026-90 be approved. The motion to approve the U.S. 46 "
        "agreement received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    assert item.motions[0].tally is None
    assert item.disposition is None
    assert any("template drift" in n for n in item.notes)


def test_non_name_tokens_on_nonzero_side_drop_names():
    # A quorum annotation on a NON-zero side splits to the right length —
    # the guard must also check the tokens are name-shaped, or Rosenbarger
    # gets a minted abstain record.
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-89\n"
        "Daily moved and Ruff seconded that Ordinance 2026-89 be adopted. "
        "The motion received a roll call vote of Ayes: 6, Nays: 0, "
        "Abstain: 2 (Rosenbarger, Ruff out of the room).\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    motion = item.motions[0]
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (6, 0, 2)
    assert motion.abstain_names == []
    assert item.disposition == "passed"
    assert any("names" in n.lower() for n in item.notes)


def test_duplicate_result_keeps_first_tally_and_notes_the_drop():
    text = (
        "5. Legislation [7:00pm]\n"
        "5.1. Ordinance 2026-88\n"
        "Daily moved and Ruff seconded that Ordinance 2026-88 be adopted. "
        "The motion received a roll call vote of Ayes: 8, Nays: 0, Abstain: 0. "
        "The motion received a roll call vote of Ayes: 3, Nays: 5, Abstain: 0.\n"
    )
    memo = parse_memo(text)
    item = memo.items[0]
    motion = item.motions[0]
    assert (motion.tally.ayes, motion.tally.nays, motion.tally.abstain) == (8, 0, 0)
    assert item.disposition == "passed"
    # The note must identify BOTH the dropped tally and the target motion.
    assert any(
        "overwrite" in n and "Ayes 3" in n and "2026-88" in n for n in item.notes
    )


def test_pdf_round_trip_matches_frozen_text():
    from src.pdf_text import extract_text
    pdf = FIXTURE.with_suffix(".pdf")
    memo = parse_memo(extract_text(pdf))
    assert [i.legislation_ref for i in memo.items] == [
        "Ordinance 2026-15", "Resolution 2026-12",
        "Resolution 2026-13", "Ordinance 2026-12",
    ]
    assert [i.disposition for i in memo.items] == [
        "continued", "continued", "passed", "failed",
    ]


def test_pdf_round_trip_june10():
    from src.pdf_text import extract_text
    memo = parse_memo(extract_text(FIXTURE_JUNE10.with_suffix(".pdf")))
    assert [i.legislation_ref for i in memo.items] == [
        "Resolution 2026-10", "Ordinance 2026-12", "Resolution 2026-09",
        "Ordinance 2026-13", "Resolution 2026-11", "Resolution 2026-12",
        "Ordinance 2026-14",
    ]
    assert [i.disposition for i in memo.items] == [
        "passed", "passed", "passed", "passed", "passed", "continued", "passed",
    ]


def test_pdf_round_trip_july29():
    from src.pdf_text import extract_text
    memo = parse_memo(extract_text(FIXTURE_JULY29.with_suffix(".pdf")))
    assert [i.legislation_ref for i in memo.items] == [
        "Ordinance 2026-16", "Ordinance 2026-17", "Resolution 2026-15",
    ]
    assert [i.disposition for i in memo.items] == [
        "continued", "continued", "passed",
    ]


def test_dispositions_stay_within_outcome_vocabulary():
    from src.memo_parse import OUTCOME_VOCABULARY
    memo = parse_memo(FIXTURE.read_text())
    assert all(
        i.disposition in OUTCOME_VOCABULARY
        for i in memo.items if i.disposition is not None
    )

"""Tests for src/agenda_align.py — anchor-first agenda↔segment alignment.

Calibration ground truth (July 22 fixtures, verified 2026-07-28):

- The agenda parses to 15 items; the ref-bearing ones are position 9 (6A,
  Ordinance 2026-15), 10 (7A, Resolution 2026-12), 11 (7B, Resolution
  2026-13), and 12 (7C, Ordinance 2026-12).
- Both "Resolution 2026-12" and "Ordinance 2026-12" are on this agenda, so
  the bare number "2026-12" is AMBIGUOUS: it must anchor neither item.
  Bare "2026-15" / "2026-13" are unique and do anchor.
- The transcript renders mid-sentence pauses as " - ", so full-form refs
  appear as "ordinance - 2026-15" (seg 46) and "ordinance - 2026-12"
  (seg 395); full-form matching tolerates dash/space separators.
- The only explicit outcome phrase in the whole meeting is seg 509's
  "motion does not carry" (the Car Free Kirkwood veto override failing).
"""
import json
import re
from pathlib import Path

import pytest

from src.agenda_align import (
    OUTCOME_PHRASES,
    ItemSpan,
    SegmentRef,
    find_ref_anchors,
    outcome_evidence_ok,
    validate_spans,
)
from src.agenda_parse import ParsedItem, parse_agenda

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def july22_items():
    text = (FIXTURES / "onboard" / "agenda_2026-07-22.txt").read_text()
    return parse_agenda(text)


@pytest.fixture(scope="module")
def july22_segments():
    records = json.loads((FIXTURES / "alignment" / "segments_2026-07-22.json").read_text())
    return [SegmentRef(**r) for r in records]


def _item(position, title, ref=None, section="Legislation", section_number=7):
    return ParsedItem(
        position=position,
        item_number=f"{section_number}X",
        section=section,
        section_number=section_number,
        title_raw=title,
        legislation_ref=ref,
    )


def _seg(i, text, start=None, speaker="Chair"):
    start = float(i * 10) if start is None else start
    return SegmentRef(i=i, start=start, end=start + 9.0, speaker=speaker, text=text)


# ---------------------------------------------------------------------------
# find_ref_anchors — real July 22 fixture
# ---------------------------------------------------------------------------


def test_fixture_premises_hold(july22_items, july22_segments):
    """The facts the anchor tests rest on, asserted so drift is loud."""
    assert len(july22_items) == 15
    refs = {it.position: it.legislation_ref for it in july22_items if it.legislation_ref}
    assert refs == {
        9: "Ordinance 2026-15",
        10: "Resolution 2026-12",
        11: "Resolution 2026-13",
        12: "Ordinance 2026-12",
    }
    assert len(july22_segments) == 518
    # Seg 410 says the bare "2026-12" with no type word — the ambiguous case.
    text410 = july22_segments[410].text
    assert "2026-12" in text410
    assert not re.search(r"(ordinance|resolution)[\s\-]+2026-12", text410, re.IGNORECASE)


def test_find_ref_anchors_real_fixture(july22_items, july22_segments):
    """Full-form matches always anchor (incl. the transcript's 'ordinance -
    2026-NN' pause-dash form); bare numbers anchor only when unique across
    the meeting's items — so seg 410's bare '2026-12' anchors NOTHING."""
    anchors = find_ref_anchors(july22_items, july22_segments)
    assert anchors == {
        9: [46, 58],           # 46 = "ordinance - 2026-15" (dash), 58 = clean full form
        10: [145],             # Resolution 2026-12: full form only; bare 2026-12 ambiguous
        11: [177, 187, 188],   # Resolution 2026-13: full forms (bare unique adds none new)
        12: [100, 125, 395],   # Ordinance 2026-12: full forms incl. 395's dash form
    }
    for hits in anchors.values():
        assert 410 not in hits


# ---------------------------------------------------------------------------
# find_ref_anchors — synthetic rules
# ---------------------------------------------------------------------------


def test_bare_number_anchors_only_when_unique():
    items = [
        _item(1, "Ordinance 2026-15 – Fire Merit Commission", "Ordinance 2026-15"),
        _item(2, "Resolution 2026-12 Alcohol Permits", "Resolution 2026-12"),
        _item(3, "Ordinance 2026-12 - Carless Kirkwood", "Ordinance 2026-12"),
    ]
    segments = [
        _seg(0, "we will take up 2026-15 first"),       # bare, unique -> anchors item 1
        _seg(1, "now 2026-12, the big one"),            # bare, ambiguous -> anchors nothing
        _seg(2, "I move that Ordinance 2026-12 pass"),  # full form -> anchors item 3 only
    ]
    assert find_ref_anchors(items, segments) == {1: [0], 2: [], 3: [2]}


def test_full_form_tolerates_pause_dash_and_case():
    items = [_item(1, "Ordinance 2026-15", "Ordinance 2026-15")]
    segments = [
        _seg(0, "i move that ORDINANCE - 2026-15 be introduced"),
        _seg(1, "ordinance–2026-15 next"),  # en-dash separator
        _seg(2, "nothing relevant here"),
    ]
    assert find_ref_anchors(items, segments) == {1: [0, 1]}


def test_bare_number_does_not_match_inside_longer_numbers():
    items = [_item(1, "Ordinance 2026-1", "Ordinance 2026-1")]
    segments = [_seg(0, "file 2026-15 is unrelated"), _seg(1, "back to 2026-1 now")]
    assert find_ref_anchors(items, segments) == {1: [1]}


def test_items_without_refs_are_absent_and_zero_anchor_items_get_empty_list():
    items = [
        _item(1, "Roll Call"),
        _item(2, "Resolution 2026-40 Something", "Resolution 2026-40"),
    ]
    segments = [_seg(0, "good evening everyone")]
    assert find_ref_anchors(items, segments) == {2: []}


# ---------------------------------------------------------------------------
# OUTCOME_PHRASES / outcome_evidence_ok
# ---------------------------------------------------------------------------


def test_outcome_vocabulary_covers_the_publish_vocab():
    assert set(OUTCOME_PHRASES) == {"passed", "failed", "continued", "pulled"}


@pytest.mark.parametrize(
    "outcome,text",
    [
        ("passed", "the motion carries"),
        ("passed", "I move that resolution 2026-13 be adopted"),
        ("passed", "the ayes have it"),
        ("failed", "the motion does not carry"),
        ("failed", "the motion fails"),
        ("failed", "the veto override was defeated"),
        ("continued", "this item is postponed to July 22"),
        ("continued", "continued to the next meeting"),
        ("pulled", "the sponsor has withdrawn the resolution"),
    ],
)
def test_outcome_evidence_ok_positive(outcome, text):
    assert outcome_evidence_ok(outcome, text)


@pytest.mark.parametrize(
    "outcome,text",
    [
        ("passed", "the motion does not carry"),
        ("failed", "the ayes have it, the ordinance is adopted"),
        ("continued", "the motion carries"),
        ("pulled", "roll call please"),
        ("passed", "will the clerk please read"),
        ("elected", "the motion carries"),  # unknown outcome word -> never ok
    ],
)
def test_outcome_evidence_ok_negative(outcome, text):
    assert not outcome_evidence_ok(outcome, text)


def test_spoken_and_digit_tallies_count_for_passed_and_failed_only():
    for text in ("the vote is seven to two", "that passes 7-2", "roll call was 5 to 4"):
        assert outcome_evidence_ok("passed", text)
        assert outcome_evidence_ok("failed", text)
        assert not outcome_evidence_ok("continued", text)


def test_legislation_ref_is_not_mistaken_for_a_tally():
    # "2026-15" must not read as a 2026-to-15 vote tally.
    assert not outcome_evidence_ok("passed", "will the clerk read ordinance 2026-15")


def test_real_seg_509_supports_failed_not_passed(july22_segments):
    text = july22_segments[509].text
    assert "motion does not carry" in text
    assert outcome_evidence_ok("failed", text)
    assert not outcome_evidence_ok("passed", text)


# ---------------------------------------------------------------------------
# validate_spans — real July 22 fixture
# ---------------------------------------------------------------------------


def _realistic_spans():
    """Hand-built spans over the real segments, all individually valid."""
    return [
        ItemSpan(position=9, start_segment=44, end_segment=90),
        ItemSpan(position=10, start_segment=144, end_segment=160),
        ItemSpan(position=11, start_segment=177, end_segment=394),
        ItemSpan(position=12, start_segment=395, end_segment=509,
                 outcome="failed", outcome_evidence_segment=509),
    ]


def test_validate_spans_realistic_spans_survive(july22_items, july22_segments):
    result = validate_spans(july22_items, _realistic_spans(), july22_segments)
    assert [s.position for s in result] == [9, 10, 11, 12]
    for span in result:
        assert span.rejected_reason is None, f"pos {span.position}: {span.rejected_reason}"
        assert span.start_segment is not None
    kirkwood = result[-1]
    assert kirkwood.outcome == "failed"
    assert kirkwood.outcome_evidence_segment == 509


def test_validate_spans_rejects_non_monotonic_later_span(july22_items, july22_segments):
    spans = _realistic_spans()
    # Move Resolution 2026-13's span before Resolution 2026-12's start; its
    # containment still holds (segs 177+187 inside), so the rejection is
    # purely the monotonicity gate — and only the LATER span is zeroed.
    spans[2] = ItemSpan(position=11, start_segment=100, end_segment=200)
    result = validate_spans(july22_items, spans, july22_segments)
    zeroed = result[2]
    assert zeroed.position == 11
    assert zeroed.start_segment is None and zeroed.end_segment is None
    assert "monotonic" in zeroed.rejected_reason
    assert result[1].rejected_reason is None  # the earlier span keeps its span
    assert result[3].rejected_reason is None


def test_validate_spans_containment_gate_needs_the_ref(july22_items, july22_segments):
    # A span over the Ordinance 2026-12 introduction cannot claim Ordinance
    # 2026-15 (position 9): its ref never appears in segs 100..130.
    spans = [ItemSpan(position=9, start_segment=100, end_segment=130)]
    result = validate_spans(july22_items, spans, july22_segments)
    assert result[0].start_segment is None
    assert "containment" in result[0].rejected_reason


# ---------------------------------------------------------------------------
# validate_spans — synthetic gates
# ---------------------------------------------------------------------------


def test_title_token_containment_for_items_without_refs():
    items = [_item(1, "Appointments to Boards and Commissions")]
    good = [_seg(0, "we have two appointments tonight"), _seg(1, "to boards and commissions yes")]
    bad = [_seg(0, "roll call please"), _seg(1, "all present")]
    ok = validate_spans(items, [ItemSpan(1, 0, 1)], good)[0]
    assert ok.rejected_reason is None
    rejected = validate_spans(items, [ItemSpan(1, 0, 1)], bad)[0]
    assert rejected.start_segment is None
    assert "containment" in rejected.rejected_reason


def test_short_title_items_can_never_pass_containment():
    # "Roll Call" has zero >5-char tokens; the v1 gate zeroes any span for it
    # (known limitation: such items stay happened-without-span).
    items = [_item(1, "Roll Call")]
    segments = [_seg(0, "will the clerk please call the roll")]
    result = validate_spans(items, [ItemSpan(1, 0, 0)], segments)
    assert result[0].start_segment is None
    assert "containment" in result[0].rejected_reason


@pytest.mark.parametrize(
    "start,end",
    [(3, 1), (-1, 2), (0, 99), (None, 2), (1, None)],
)
def test_validate_spans_range_checks(start, end):
    items = [_item(1, "Appointments to Boards and Commissions")]
    segments = [_seg(i, "appointments to boards and commissions") for i in range(4)]
    result = validate_spans(items, [ItemSpan(1, start, end)], segments)
    assert result[0].start_segment is None
    assert result[0].end_segment is None
    assert result[0].rejected_reason is not None


def test_abstained_span_passes_through_untouched():
    items = [_item(1, "Appointments to Boards and Commissions")]
    result = validate_spans(items, [ItemSpan(1)], [_seg(0, "hello")])
    assert result[0] == ItemSpan(position=1)


def _outcome_fixture():
    items = [_item(1, "Resolution 2026-40 Community Gardens", "Resolution 2026-40")]
    segments = [
        _seg(0, "taking up resolution 2026-40 tonight"),
        _seg(1, "discussion of the gardens"),
        _seg(2, "clerk please call the roll"),
        _seg(3, "the motion carries"),
        _seg(4, "moving on"),
        _seg(5, "unrelated"),
        _seg(6, "unrelated"),
        _seg(7, "unrelated"),
        _seg(8, "the motion carries"),  # 6 past end of a 0..2 span -> too far
    ]
    return items, segments


def test_outcome_kept_with_evidence_inside_or_shortly_after_span():
    items, segments = _outcome_fixture()
    inside = validate_spans(
        items, [ItemSpan(1, 0, 3, outcome="passed", outcome_evidence_segment=3)], segments
    )[0]
    assert inside.outcome == "passed" and inside.rejected_reason is None
    after = validate_spans(
        items, [ItemSpan(1, 0, 2, outcome="passed", outcome_evidence_segment=3)], segments
    )[0]
    assert after.outcome == "passed" and after.rejected_reason is None


def test_outcome_zeroed_but_span_kept_when_evidence_fails():
    items, segments = _outcome_fixture()
    cases = {
        "too far after span": ItemSpan(1, 0, 2, outcome="passed", outcome_evidence_segment=8),
        "no evidence segment": ItemSpan(1, 0, 2, outcome="passed"),
        "phrase mismatch": ItemSpan(1, 0, 2, outcome="failed", outcome_evidence_segment=3),
        "unsupportive segment": ItemSpan(1, 0, 2, outcome="passed", outcome_evidence_segment=1),
        "out of vocabulary": ItemSpan(1, 0, 3, outcome="tabled", outcome_evidence_segment=3),
        "evidence out of range": ItemSpan(1, 0, 2, outcome="passed", outcome_evidence_segment=99),
    }
    for label, span in cases.items():
        got = validate_spans(items, [span], segments)[0]
        assert got.outcome is None, label
        assert got.outcome_evidence_segment is None, label
        assert got.rejected_reason is not None, label
        # The span itself survives — only the outcome claim is stripped.
        assert got.start_segment == 0, label
        assert got.end_segment is not None, label


def test_outcome_on_fully_abstained_span_is_stripped():
    items, segments = _outcome_fixture()
    span = ItemSpan(1, outcome="passed", outcome_evidence_segment=3)
    got = validate_spans(items, [span], segments)[0]
    assert got.start_segment is None
    assert got.outcome is None
    assert got.rejected_reason is not None


def test_rejected_span_does_not_poison_monotonicity():
    # A zeroed span must not raise the bar for the spans after it.
    items = [
        _item(1, "Resolution 2026-40 Community Gardens", "Resolution 2026-40"),
        _item(2, "Roll Call"),  # will be zeroed by containment
        _item(3, "Appointments to Boards and Commissions"),
    ]
    segments = [
        _seg(0, "resolution 2026-40 discussion"),
        _seg(1, "irrelevant text"),
        _seg(2, "appointments to boards and commissions"),
    ]
    spans = [ItemSpan(1, 0, 0), ItemSpan(2, 1, 1), ItemSpan(3, 2, 2)]
    result = validate_spans(items, spans, segments)
    assert result[0].rejected_reason is None
    assert result[1].start_segment is None
    assert result[2].rejected_reason is None

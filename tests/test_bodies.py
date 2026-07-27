"""Tests for per-body section semantics (src/bodies.py)."""

from pathlib import Path

from src.agenda_parse import ParsedItem, parse_agenda
from src.bodies import BLOOMINGTON_COMMON_COUNCIL, classify_item

FIXTURE = Path(__file__).parent / "fixtures" / "onboard" / "agenda_2026-07-29.txt"

BODY = BLOOMINGTON_COMMON_COUNCIL


def make_item(section, title, section_number=1, item_number="1A", ref=None):
    return ParsedItem(
        position=1,
        item_number=item_number,
        section=section,
        section_number=section_number,
        title_raw=title,
        legislation_ref=ref,
    )


def test_first_reading_ordinance():
    item = make_item(
        "Legislation for First Readings",
        "Ordinance 2026-16 – To Amend an Ordinance Fixing the Salaries of "
        "Officers and Employees of the Police and Fire Departments",
        section_number=6,
        item_number="6A",
        ref="Ordinance 2026-16",
    )
    c = classify_item(item, BODY)
    assert c.kind == "ordinance"
    assert c.stage == "First reading"
    assert c.public_comment is False
    assert "second reading" in c.public_comment_note


def test_second_reading_resolution_takes_comment():
    item = make_item(
        "Legislation for Second Readings and Resolutions",
        "Resolution 2026-14 - Approval of Amended Legal Representation "
        "Agreement for Common Council",
        section_number=7,
        item_number="7B",
        ref="Resolution 2026-14",
    )
    c = classify_item(item, BODY)
    assert c.kind == "resolution"
    assert c.stage == "Second reading — final vote"
    assert c.public_comment is True
    assert "before the vote" in c.public_comment_note


def test_public_comment_section():
    item = make_item(
        "Additional Public Comment* (a maximum of twenty-five minutes is set "
        "aside for this section)",
        "Additional Public Comment* (a maximum of twenty-five minutes is set "
        "aside for this section)",
        section_number=8,
        item_number="8",
    )
    c = classify_item(item, BODY)
    assert c.kind == "public-comment"
    assert c.stage is None
    assert c.public_comment is True
    assert "3 minutes" in c.public_comment_note


def test_minutes_and_appointments():
    minutes = make_item(
        "Minutes for Approval", "January 14, 2026 Regular Session",
        section_number=3, item_number="3B",
    )
    assert classify_item(minutes, BODY).kind == "minutes"

    appt = make_item(
        "Appointments to Boards and Commissions", "Memo from Clerk Bolden",
        section_number=5, item_number="5A",
    )
    assert classify_item(appt, BODY).kind == "appointment"


def test_unknown_section_is_other():
    item = make_item("Executive Session Announcements", "Some item")
    c = classify_item(item, BODY)
    assert c.kind == "other"
    assert c.stage is None
    assert c.public_comment is False
    assert c.public_comment_note is None


# --- Parenthetical keyword-collision hazards -------------------------------
# "a maximum of twenty minutes..." contains "MINUTES" case-insensitively; the
# MINUTES rule must not steal the REPORTS or ADDITIONAL PUBLIC COMMENT headers.

def test_reports_parenthetical_does_not_match_minutes():
    item = make_item(
        "Reports (a maximum of twenty minutes is set aside for each part of "
        "this section)",
        "Council members",
        section_number=4,
        item_number="4A",
    )
    assert classify_item(item, BODY).kind == "report"


def test_additional_public_comment_parenthetical_does_not_match_minutes():
    item = make_item(
        "Additional Public Comment* (a maximum of twenty-five minutes is set "
        "aside for this section)",
        "Additional Public Comment* (a maximum of twenty-five minutes is set "
        "aside for this section)",
        section_number=8,
        item_number="8",
    )
    assert classify_item(item, BODY).kind == "public-comment"


def test_plain_minutes_header_still_matches_minutes():
    item = make_item(
        "Minutes for Approval", "February 4, 2026 Regular Session",
        section_number=3, item_number="3C",
    )
    assert classify_item(item, BODY).kind == "minutes"


def test_reports_public_subitem_is_general_comment_period():
    # Real fixture titles this sub-item "Public*" (trailing footnote asterisk).
    item = make_item(
        "Reports (a maximum of twenty minutes is set aside for each part of "
        "this section)",
        "Public*",
        section_number=4,
        item_number="4D",
    )
    c = classify_item(item, BODY)
    assert c.kind == "public-comment"
    assert c.public_comment is True
    assert "3 minutes" in c.public_comment_note


# --- Real-fixture integration ----------------------------------------------

def test_classify_real_fixture():
    items = parse_agenda(FIXTURE.read_text())
    by_number = {i.item_number: i for i in items}

    c6a = classify_item(by_number["6A"], BODY)
    assert (c6a.kind, c6a.stage, c6a.public_comment) == (
        "ordinance", "First reading", False)

    c7b = classify_item(by_number["7B"], BODY)
    assert (c7b.kind, c7b.stage, c7b.public_comment) == (
        "resolution", "Second reading — final vote", True)

    c8 = classify_item(by_number["8"], BODY)
    assert c8.kind == "public-comment"
    assert c8.public_comment is True

    c1 = classify_item(by_number["1"], BODY)
    assert c1.kind == "procedural"

    c4d = classify_item(by_number["4D"], BODY)
    assert c4d.kind == "public-comment"
    assert c4d.public_comment is True

    # Everything in the real agenda classifies; nothing falls through to
    # "other".
    for item in items:
        assert classify_item(item, BODY).kind != "other", item.item_number

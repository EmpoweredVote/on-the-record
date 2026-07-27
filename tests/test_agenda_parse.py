from pathlib import Path

from src.agenda_parse import ParsedItem, parse_agenda

FIX = Path(__file__).parent / "fixtures" / "onboard"


def _real_items() -> list[ParsedItem]:
    text = (FIX / "agenda_2026-07-29.txt").read_text()
    return parse_agenda(text)


# ---------------------------------------------------------------------------
# Real-fixture tests (the fixture is the ground truth)
# ---------------------------------------------------------------------------


def test_real_fixture_positions_strictly_increasing_from_1():
    items = _real_items()
    assert items, "expected items from the real agenda"
    assert [i.position for i in items] == list(range(1, len(items) + 1))


def test_real_fixture_full_item_number_sequence():
    # Template-drift tripwire: pin the exact item sequence of the real agenda.
    items = _real_items()
    assert [i.item_number for i in items] == [
        "1", "2",
        "3A", "3B", "3C", "3D", "3E",
        "4A", "4B", "4C", "4D",
        "5A",
        "6A", "6B",
        "7A", "7B",
        "8", "9", "10",
    ]
    assert len(items) == 19


def test_real_fixture_6a_wrapped_title_and_ref():
    items = _real_items()
    by_number = {i.item_number: i for i in items}
    item = by_number["6A"]
    assert item.legislation_ref == "Ordinance 2026-16"
    assert item.title_raw.startswith("Ordinance 2026-16")
    # continuation lines (3-line wrapped title) joined into title_raw
    assert "for the Year 2026" in item.title_raw
    assert "Employees of the Police and Fire Departments" in item.title_raw
    assert "\n" not in item.title_raw


def test_real_fixture_7a_endash_ordinance_with_sponsor():
    by_number = {i.item_number: i for i in _real_items()}
    item = by_number["7A"]
    assert item.legislation_ref == "Ordinance 2026-15"
    assert item.sponsor == "CM Zulich"


def test_real_fixture_7b_hyphen_resolution_with_colon_sponsor():
    by_number = {i.item_number: i for i in _real_items()}
    item = by_number["7B"]
    assert item.legislation_ref == "Resolution 2026-14"
    assert item.sponsor == "CM Asare"


def test_real_fixture_na_sponsors_are_none():
    by_number = {i.item_number: i for i in _real_items()}
    assert by_number["6A"].sponsor is None
    assert by_number["6B"].sponsor is None


def test_real_fixture_no_footnote_bleed():
    for item in _real_items():
        blob = (item.title_raw + " " + " ".join(item.extra_lines) + " " + item.section).lower()
        assert "zoom.us" not in blob, f"footnote bled into {item.item_number}: {blob}"
        assert "youtube" not in blob, f"footnote bled into {item.item_number}: {blob}"
        assert "catstv" not in blob, f"footnote bled into {item.item_number}: {blob}"


def test_real_fixture_headline_only_sections_present():
    items = _real_items()
    sections_of_headline_items = {i.item_number: i.section for i in items if i.item_number.isdigit()}
    assert any(s.startswith("Roll Call") for s in sections_of_headline_items.values())
    assert any(s.startswith("Adjournment") for s in sections_of_headline_items.values())


def test_real_fixture_minutes_items_have_no_legislation_ref():
    items = [i for i in _real_items() if i.section_number == 3]
    assert items, "expected Minutes for Approval items"
    for item in items:
        assert item.legislation_ref is None


# ---------------------------------------------------------------------------
# Synthetic contract tests (pin the contract independent of the fixture)
# ---------------------------------------------------------------------------


def test_synthetic_template_full_shape():
    text = (
        "1. Roll Call\n"
        "2. Agenda Summation\n"
        "3. Minutes for Approval\n"
        "A. Regular Session June 3, 2026\n"
        "6. Legislation for First Readings\n"
        "A. Ordinance 2026-16 – To Amend an Ordinance Fixing Salaries\n"
        "Council Sponsor - CM Piedmont-Smith\n"
        "7. Legislation for Second Readings and Resolutions\n"
        "A. Resolution 2026-14 - To Approve an Interlocal Agreement\n"
        "Council Sponsor: CM Rosenbarger\n"
        "10. Adjournment\n"
    )
    items = parse_agenda(text)
    by_number = {i.item_number: i for i in items}
    assert by_number["3A"].legislation_ref is None
    assert by_number["6A"].legislation_ref == "Ordinance 2026-16"
    assert by_number["6A"].sponsor == "CM Piedmont-Smith"
    assert by_number["7A"].legislation_ref == "Resolution 2026-14"
    assert by_number["7A"].sponsor == "CM Rosenbarger"
    assert by_number["1"].section == "Roll Call"
    assert [i.position for i in items] == list(range(1, len(items) + 1))


def test_empty_text_returns_empty_list():
    assert parse_agenda("") == []
    assert parse_agenda("\n\n  \n") == []


def test_wrapped_title_three_lines_joined_with_single_spaces():
    text = (
        "6. Legislation for First Readings\n"
        "A. Ordinance 2026-99 – To Do a Very Long Thing That Wraps Across\n"
        "Multiple Lines of the Agenda Document Without Any\n"
        "Indentation Cue Whatsoever\n"
        "Council Sponsor - N/A\n"
    )
    items = parse_agenda(text)
    by_number = {i.item_number: i for i in items}
    item = by_number["6A"]
    assert item.title_raw == (
        "Ordinance 2026-99 – To Do a Very Long Thing That Wraps Across "
        "Multiple Lines of the Agenda Document Without Any "
        "Indentation Cue Whatsoever"
    )
    assert item.legislation_ref == "Ordinance 2026-99"
    assert item.sponsor is None


def test_footnote_line_between_header_and_first_item_skipped():
    text = (
        "7. Legislation for Second Readings and Resolutions\n"
        "1https://bloomington.zoom.us/j/x\n"
        "A. Ordinance 2026-1 – X\n"
    )
    items = parse_agenda(text)
    by_number = {i.item_number: i for i in items}
    item = by_number["7A"]
    assert item.title_raw == "Ordinance 2026-1 – X"
    assert item.legislation_ref == "Ordinance 2026-1"
    for i in items:
        assert "zoom.us" not in i.title_raw
        assert all("zoom.us" not in x for x in i.extra_lines)


def test_appropriation_ordinance_ref_normalized():
    text = (
        "6. Legislation for First Readings\n"
        "A. Appropriation Ordinance 2026-3 – To Appropriate Funds\n"
    )
    items = parse_agenda(text)
    assert items[0].legislation_ref == "Appropriation Ordinance 2026-3"


def test_extra_lines_after_sponsor_do_not_join_title():
    text = (
        "6. Legislation for First Readings\n"
        "A. Ordinance 2026-5 – A Title\n"
        "Council Sponsor - CM Smith\n"
        "Asked to be heard by the administration\n"
    )
    items = parse_agenda(text)
    item = items[0]
    assert item.title_raw == "Ordinance 2026-5 – A Title"
    assert item.sponsor == "CM Smith"
    assert item.extra_lines == ["Asked to be heard by the administration"]

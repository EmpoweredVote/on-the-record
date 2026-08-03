"""Tests for gui.politicians — the speaker-link politician picker.

Mirrors tests/test_gui_races.py: pure label/parse functions tested directly,
the DB query tested through a fake cursor.
"""
from gui import politicians
from gui.politicians import parse_name_query, politician_display


def test_parse_name_query_splits_tokens():
    assert parse_name_query("Thomas Tiffany") == ["thomas", "tiffany"]


def test_parse_name_query_drops_punctuation_and_extra_space():
    assert parse_name_query("  O'Brien,  Mary-Kate ") == ["brien", "mary", "kate"]


def test_parse_name_query_empty():
    assert parse_name_query("") == []
    assert parse_name_query("   ") == []


def test_parse_name_query_drops_a_bare_middle_initial():
    # Every token becomes an AND-ed clause, so keeping "f" would require the
    # stored row to contain an F somewhere: "John F Kennedy" returns ZERO against
    # the stored "John Kennedy" (verified against prod). Dropping 1-char tokens
    # is what makes the query survive an initial the record doesn't carry.
    assert parse_name_query("John F Kennedy") == ["john", "kennedy"]
    assert parse_name_query("Thomas P. Tiffany") == ["thomas", "tiffany"]


def test_parse_name_query_drops_generational_suffixes():
    # name_suffix is its own column and is not searched, so "Wesley Hunt Jr"
    # returns zero against the stored "Wesley Hunt" (verified against prod).
    assert parse_name_query("Wesley Hunt Jr.") == ["wesley", "hunt"]
    assert parse_name_query("Harold Ford III") == ["harold", "ford"]


def test_politician_display_name_only():
    assert politician_display({"full_name": "Mandela Barnes"}) == "Mandela Barnes"


def test_politician_display_name_and_office():
    rec = {"full_name": "Francesca Hong", "office_title": "Representative to the Assembly"}
    assert politician_display(rec) == "Francesca Hong · Representative to the Assembly"


def test_politician_display_all_fields():
    rec = {
        "full_name": "Thomas P. Tiffany",
        "office_title": "U.S. Representative",
        "district_label": "Congressional District 7",
        "government_name": "United States Federal Government",
    }
    assert politician_display(rec) == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7 "
        "· United States Federal Government"
    )


def test_politician_display_omits_empty_without_stray_separators():
    rec = {"full_name": "Janet Hong", "office_title": "", "district_label": "",
           "government_name": ""}
    assert politician_display(rec) == "Janet Hong"


def test_politician_display_skips_district_that_repeats_the_office():
    # essentials stores d.label == o.title for many single-seat offices
    # ("Texas Attorney General" twice); printing it twice is noise.
    rec = {"full_name": "Ken Paxton", "office_title": "Texas Attorney General",
           "district_label": "Texas Attorney General", "government_name": ""}
    assert politician_display(rec) == "Ken Paxton · Texas Attorney General"


def test_politician_display_drops_a_district_contained_in_the_office():
    # 1141 prod rows are redundant by containment, not exact equality.
    rec = {"full_name": "Tara T. Hong",
           "office_title": "Representative, 18th Middlesex District",
           "district_label": "18th Middlesex District", "government_name": ""}
    assert politician_display(rec) == (
        "Tara T. Hong · Representative, 18th Middlesex District")


def test_politician_display_keeps_the_longer_side_when_office_is_contained():
    # When they differ, the district is often the MORE informative side.
    rec = {"full_name": "Ken Paxton", "office_title": "Attorney General",
           "district_label": "Texas Attorney General", "government_name": ""}
    assert politician_display(rec) == "Ken Paxton · Texas Attorney General"


def test_politician_display_keeps_both_when_neither_contains_the_other():
    rec = {"full_name": "Thomas P. Tiffany", "office_title": "U.S. Representative",
           "district_label": "Congressional District 7", "government_name": ""}
    assert politician_display(rec) == (
        "Thomas P. Tiffany · U.S. Representative · Congressional District 7")

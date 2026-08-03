"""Tests for gui.politicians — the speaker-link politician picker.

Mirrors tests/test_gui_races.py: pure label/parse functions tested directly,
the DB query tested through a fake cursor.
"""
from gui import politicians
from gui.politicians import (
    candidacy_display,
    mark_duplicate_names,
    parse_name_query,
    politician_display,
)
from gui.races import race_display


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


def _cand(position_name="Governor", state="WI", primary_party="Republican",
          election_type="primary", year=2026, status="active"):
    return {"position_name": position_name, "state": state,
            "primary_party": primary_party, "election_type": election_type,
            "year": year, "status": status}


def test_candidacy_display_none_is_a_warning_string():
    from gui.politicians import NO_CANDIDACIES
    assert candidacy_display([]) == NO_CANDIDACIES
    assert candidacy_display(None) == NO_CANDIDACIES
    assert NO_CANDIDACIES == "no candidacies"


def test_candidacy_display_one_matches_race_display():
    expected = race_display("Governor", 2026, "WI", "Republican", "primary")
    assert candidacy_display([_cand()]) == f"running: {expected}"
    assert "WI · Governor · Republican primary · 2026" in candidacy_display([_cand()])


def test_candidacy_display_joins_several_with_semicolons():
    out = candidacy_display([_cand(), _cand(election_type="general", primary_party="")])
    assert out.startswith("running: ")
    assert out.count("; ") == 1
    assert "Republican primary" in out and "General" in out


def test_candidacy_display_prefixes_non_active_status():
    # No "running:" lead when nothing is active — "running: withdrawn: ..." reads
    # as nonsense, and a withdrawn-only person is exactly the case a curator
    # needs to notice.
    out = candidacy_display([_cand(status="withdrawn")])
    assert out == "withdrawn: WI · Governor · Republican primary · 2026"


def test_candidacy_display_treats_filed_as_running():
    # candidate_status is {active, filed, withdrawn} and "filed" is 111 live rows.
    # A filed candidate IS contesting the race, so prefixing it like a withdrawal
    # and dropping the "running:" lead would misread the data.
    assert candidacy_display([_cand(status="filed")]) == (
        "running: WI · Governor · Republican primary · 2026")


def test_candidacy_display_prefix_is_the_actual_status():
    # Pins that the prefix comes from the data, not a hardcoded "withdrawn".
    assert candidacy_display([_cand(status="disqualified")]).startswith("disqualified: ")


def test_candidacy_display_missing_status_counts_as_running():
    c = _cand()
    del c["status"]
    assert candidacy_display([c]).startswith("running: ")


def test_candidacy_display_normalizes_status_case_and_whitespace():
    assert candidacy_display([_cand(status="  ACTIVE ")]).startswith("running: ")


def test_candidacy_display_exactly_three_has_no_tail():
    cands = [_cand(position_name=f"Office {i}") for i in range(3)]
    out = candidacy_display(cands)
    assert "more" not in out
    assert out.count("; ") == 2


def test_candidacy_display_leads_with_running_when_any_is_active():
    out = candidacy_display([_cand(status="withdrawn"),
                             _cand(position_name="Senate")])
    assert out.startswith("running: ")
    assert "withdrawn: WI · Governor" in out


def test_candidacy_display_caps_at_three_and_counts_the_rest():
    cands = [_cand(position_name=f"Office {i}") for i in range(5)]
    out = candidacy_display(cands)
    # 3 shown => 2 separators between them, plus one before the "+N more" tail,
    # which is itself just another item in the list.
    assert out.count("; ") == 3
    assert out.endswith("; +2 more")
    assert "Office 3" not in out


def test_candidacy_display_skips_malformed_entries():
    out = candidacy_display(["not-a-dict", _cand(), None])
    assert out == "running: WI · Governor · Republican primary · 2026"


def test_candidacy_display_all_malformed_reads_as_none():
    assert candidacy_display(["not-a-dict", None]) == "no candidacies"


def test_mark_duplicate_names_leaves_unique_names_alone():
    rows = [{"full_name": "Mandela Barnes"}, {"full_name": "Kelda Roys"}]
    out = mark_duplicate_names(rows)
    assert [r.get("duplicate_note", "") for r in out] == ["", ""]


def test_mark_duplicate_names_flags_both_rows_of_a_pair():
    rows = [{"full_name": "Francesca Hong"}, {"full_name": "Francesca Hong"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 records for this name" for r in out)


def test_mark_duplicate_names_ignores_middle_initials_and_case():
    rows = [{"full_name": "Thomas P. Tiffany"}, {"full_name": "thomas tiffany"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] for r in out)


def test_mark_duplicate_names_counts_the_whole_group():
    rows = [{"full_name": "Mike Rogers"}] * 3
    out = mark_duplicate_names([dict(r) for r in rows])
    assert all(r["duplicate_note"] == "⚠ 3 records for this name" for r in out)


def test_mark_duplicate_names_flags_genuinely_different_people_too():
    # Two real distinct Mike Rogers still both get flagged — correct: the
    # curator must look, and we can't tell them apart from names alone.
    rows = [{"full_name": "Mike Rogers", "office_title": "U.S. Representative"},
            {"full_name": "Mike Rogers", "office_title": "Senator"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] for r in out)


def test_mark_duplicate_names_does_not_collide_two_juniors():
    # Without dropping the suffix both would key to "john jr" — prod has plenty
    # of these (John G. Roberts Jr., John P. Wiley Jr.).
    rows = [{"full_name": "John G. Roberts Jr."}, {"full_name": "John P. Wiley Jr."}]
    out = mark_duplicate_names(rows)
    assert [r["duplicate_note"] for r in out] == ["", ""]


def test_mark_duplicate_names_still_matches_across_a_suffix():
    rows = [{"full_name": "Harold Ford III"}, {"full_name": "Harold Ford"}]
    out = mark_duplicate_names(rows)
    assert all(r["duplicate_note"] == "⚠ 2 records for this name" for r in out)


def test_mark_duplicate_names_does_not_flag_a_lone_row():
    out = mark_duplicate_names([{"full_name": "Thomas P. Tiffany"}])
    assert out[0]["duplicate_note"] == ""

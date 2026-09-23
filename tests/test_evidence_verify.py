from src.evidence.verify import find_raw, normalize, quote_runs, verbatim_ok

SRC = ("Mayor Bass said, “We will build 30,000 units of housing, "
       "and we will do it with union labor,” during the forum.")

def test_exact_substring_passes():
    assert verbatim_ok("We will build 30,000 units of housing", SRC)

def test_curly_and_straight_quotes_normalize_equal():
    # SRC wraps the clause in curly double-quotes; the same span written with
    # straight double-quotes must still match after normalization.
    assert verbatim_ok(
        '"We will build 30,000 units of housing, and we will do it with union labor,"',
        SRC)

def test_ellipsis_tolerant_runs_must_be_in_order():
    assert verbatim_ok("We will build 30,000 units of housing … with union labor", SRC)
    assert not verbatim_ok("with union labor … We will build 30,000 units", SRC)

def test_non_substring_fails():
    assert not verbatim_ok("We will build affordable homes for everyone", SRC)

def test_verbatim_ok_contiguous_full_passage_passes():
    page = ("Intro. When I'm mayor, we will build 40,000 units by cutting permit timelines "
            "and converting motels to housing. Later text.")
    quote = ("When I'm mayor, we will build 40,000 units by cutting permit timelines "
             "and converting motels to housing.")
    assert verbatim_ok(quote, page) is True

def test_verbatim_ok_reworded_quote_fails():
    page = "When I'm mayor, we will build 40,000 units by cutting permit timelines."
    reworded = "As mayor she plans to construct 40,000 homes by streamlining permits."
    assert verbatim_ok(reworded, page) is False

def test_quote_runs_are_normalized_ellipsis_separated_pieces():
    assert quote_runs("…We’ll build  housing... and parks …") == ["we'll build housing", "and parks"]
    assert quote_runs(" ... ") == []

def test_find_raw_maps_a_normalized_hit_back_to_its_raw_offset():
    # Each prefix shifts the raw offset away from the normalized one: "…" grows
    # to "...", a whitespace run collapses, a curly apostrophe stays one char.
    for prefix in ("", "…Well…  ", "Intro.\n\n   ", "They’re “here”. "):
        hay = prefix + "So What I’d do\n in a next term."
        i = find_raw(hay, "what i'd do in a next term")
        assert hay[i:].startswith("What I’d do"), (prefix, i)

def test_find_raw_returns_minus_one_when_absent():
    assert find_raw("We will build housing.", "we will build parks") == -1
    assert find_raw("We will build housing.", "") == -1

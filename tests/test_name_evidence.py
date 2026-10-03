from __future__ import annotations

from src.models import Segment
from src.name_evidence import build_turns, find_self_intros, is_mention


def seg(i: int, label: str, text: str) -> Segment:
    return Segment(segment_id=i, start_time=float(i * 10), end_time=float(i * 10 + 9),
                   speaker_label=label, text=text)


FILLER = "and I want to thank the committee for hearing this bill today and for the time."


def test_build_turns_groups_consecutive_labels_and_skips_empty():
    turns = build_turns([seg(0, "A", "Hello there."), seg(1, "A", "More words."),
                         seg(2, "B", ""), seg(3, "B", "Hi."), seg(4, "A", "Back.")])
    assert [t.label for t in turns] == ["A", "B", "A"]
    assert turns[0].segment_ids == [0, 1]
    assert turns[0].text == "Hello there. More words."


def test_my_name_is_with_title_and_affiliation():
    ev = find_self_intros(build_turns([seg(0, "W", "Good afternoon. My name is Rabbi Aaron Spiegel, "
                                                  "I'm with the Indy Multi-Faith Alliance. " + FILLER)]))
    assert len(ev) == 1
    e = ev[0]
    assert (e.kind, e.label, e.name, e.title) == ("E1", "W", "Aaron Spiegel", "Rabbi")
    assert e.affiliation == "the Indy Multi-Faith Alliance"
    assert e.segment_id == 0 and "Spiegel" in e.quote


def test_im_full_name_counts_but_im_sorry_does_not():
    ok = find_self_intros(build_turns([seg(0, "H", "Good evening. I'm Ted Simons. Tonight we continue " + FILLER)]))
    assert [e.name for e in ok] == ["Ted Simons"]
    bad = find_self_intros(build_turns([seg(0, "H", "Oh, I'm sorry. I'm Just going to say " + FILLER)]))
    assert bad == []


def test_im_with_office_title_single_surname():
    ev = find_self_intros(build_turns([seg(0, "S", "I'm Senator Brown, and I represent district fifteen " + FILLER)]))
    assert [(e.name, e.title) for e in ev] == [("Brown", "Senator")]


def test_first_name_only_is_partial():
    ev = find_self_intros(build_turns([seg(0, "W", "Yeah, so my name is Nitya. I'm a council member " + FILLER)]))
    assert ev[0].name == "Nitya" and ev[0].partial


def test_x3_only_first_intro_in_turn_counts():
    ev = find_self_intros(build_turns([seg(0, "W", "My name is Sophie McGowan and I'm here with "
                                                  "Jackson Franklin from the coalition. " + FILLER)]))
    assert [e.name for e in ev] == ["Sophie McGowan"]


def test_x2_intro_outside_window_is_ignored():
    long_lead = " ".join(["word"] * 70)
    ev = find_self_intros(build_turns([seg(0, "W", long_lead + " my name is Late Person.")]))
    assert ev == []


def test_short_first_turn_is_skipped_for_first_substantial_turn():
    turns = build_turns([seg(0, "W", "Thank you."), seg(1, "C", "Go ahead."),
                         seg(2, "W", "My name is Lauren Murphy, from Fishers. " + FILLER)])
    assert [e.name for e in find_self_intros(turns)] == ["Lauren Murphy"]


# NEW TESTS FOR FIXES

def test_x1_cue_distant_from_name_passes():
    """X1 should only filter when cue is immediately before the name."""
    # "Like I said" is not immediately before "my name" → should pass
    ev = find_self_intros(build_turns([seg(0, "W", "Like I said, my name is John Smith, from Carmel. " + FILLER)]))
    assert [e.name for e in ev] == ["John Smith"]
    # "My son" then later "my name" → should pass
    ev2 = find_self_intros(build_turns([seg(0, "W", "My son and I came today. My name is John Smith, here. " + FILLER)]))
    assert [e.name for e in ev2] == ["John Smith"]




def test_x1_as_cue_with_said_filters():
    """X1 (b): 'as [name/title] said/says/asked' marks that person as mentioned."""
    # "as Mary said" → Mary is a mention
    ev = find_self_intros(build_turns([seg(0, "W", "as Mary said I'm John Jones here. " + FILLER)]))
    # Mary should be filtered; should only get John Jones
    assert [e.name for e in ev] == ["John Jones"]


def test_x1_unit_tests_is_mention():
    """Direct unit tests for is_mention boundary checking."""
    # is_mention(text, name_start, name_end) returns True if name is a mention

    # Test case: "my colleague Senator Brown"
    # "colleague" before "Brown" (after skipping "Senator" title) → True
    text = "my colleague Senator Brown"
    name_start = text.index("Brown")
    name_end = name_start + len("Brown")
    assert is_mention(text, name_start, name_end) is True

    # Test case: "the bill by Senator Koch"
    # "by" before "Koch" (after skipping "Senator") → True
    text2 = "the bill by Senator Koch"
    name_start2 = text2.index("Koch")
    name_end2 = name_start2 + len("Koch")
    assert is_mention(text2, name_start2, name_end2) is True

    # Test case: "as Mary said"
    # "as" before "Mary", "said" after → True
    text3 = "as Mary said"
    name_start3 = text3.index("Mary")
    name_end3 = name_start3 + len("Mary")
    assert is_mention(text3, name_start3, name_end3) is True

    # Test case: "my name is John Smith" (NOT a mention)
    text4 = "my name is John Smith"
    name_start4 = text4.index("Smith")
    name_end4 = name_start4 + len("Smith")
    assert is_mention(text4, name_start4, name_end4) is False


def test_x_contractions_over_capture():
    """Clean names with trailing contractions (I'm, n't, etc)."""
    # "John Smith and I'm" - regex matches "John Smith" before "and", then clean_name doesn't need to trim
    ev = find_self_intros(build_turns([seg(0, "W", "My name is John Smith and I'm the director. " + FILLER)]))
    assert [e.name for e in ev] == ["John Smith"]
    # "Mary Johnson and I'll" - similar case
    ev2 = find_self_intros(build_turns([seg(0, "W", "I'm Mary Johnson and I'll see you. " + FILLER)]))
    assert [e.name for e in ev2] == ["Mary Johnson"]


def test_x_possessive_over_capture():
    """Names with possessive 's should be rejected."""
    ev = find_self_intros(build_turns([seg(0, "W", "I'm John Smith's colleague here today. " + FILLER)]))
    # "John Smith's" is possessive, so it should be rejected
    assert ev == []


def test_x_curly_apostrophes():
    """Regexes accept both straight (') and curly (') apostrophes."""
    # Straight apostrophe with I'm
    ev1 = find_self_intros(build_turns([seg(0, "W", "Hi, I'm Jane Doe from Fishers. " + FILLER)]))
    assert [e.name for e in ev1] == ["Jane Doe"]
    # Curly apostrophe with I'm (U+2019)
    ev2 = find_self_intros(build_turns([seg(0, "W", "Hi, I’m Jane Doe from Fishers. " + FILLER)]))
    assert [e.name for e in ev2] == ["Jane Doe"]
    # Test with straight my name's
    ev3 = find_self_intros(build_turns([seg(0, "W", "Well, my name's John Brown, and I'm here. " + FILLER)]))
    assert [e.name for e in ev3] == ["John Brown"]
    # Test with curly my name's
    ev4 = find_self_intros(build_turns([seg(0, "W", "Well, my name’s John Brown, and I’m here. " + FILLER)]))
    assert [e.name for e in ev4] == ["John Brown"]

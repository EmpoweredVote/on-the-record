from __future__ import annotations

from src.models import Segment
from src.name_evidence import build_turns, find_self_intros, is_mention, find_chair_calls, find_thank_backs


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


def test_x_straight_apostrophes():
    """Regexes accept straight (‘) apostrophes."""
    # Straight apostrophe with I’m
    ev1 = find_self_intros(build_turns([seg(0, "W", "Hi, I'm Jane Doe from Fishers. " + FILLER)]))
    assert [e.name for e in ev1] == ["Jane Doe"]
    # Test with straight my name’s
    ev2 = find_self_intros(build_turns([seg(0, "W", "Well, my name's John Brown, and I'm here. " + FILLER)]))
    assert [e.name for e in ev2] == ["John Brown"]


def test_x_curly_apostrophes():
    """Regexes accept curly (‘) apostrophes (U+2019)."""
    # Curly apostrophe with I’m (U+2019)
    ev1 = find_self_intros(build_turns([seg(0, "W", "Hi, I’m Jane Doe from Fishers. " + FILLER)]))
    assert [e.name for e in ev1] == ["Jane Doe"]
    # Test with curly my name’s
    ev2 = find_self_intros(build_turns([seg(0, "W", "Well, my name’s John Brown, and I’m here. " + FILLER)]))
    assert [e.name for e in ev2] == ["John Brown"]


# TASK 2: E2 CHAIR CALLS AND E3 THANK-BACKS


def test_e2_yes_senator_names_next_speaker():
    turns = build_turns([seg(0, "CHAIR", "Thank you, Senator Garten. Any questions? Yes, Senator Brown."),
                         seg(1, "B", "Thank you, Madam Chair. I support the bill.")])
    ev = find_chair_calls(turns)
    assert [(e.kind, e.label, e.name, e.title) for e in ev] == [("E2", "B", "Brown", "Senator")]


def test_e2_name_then_invitation():
    turns = build_turns([seg(0, "CHAIR", "All right. Senator Garten, would you like to close?"),
                         seg(1, "G", "Yes, thank you.")])
    assert [(e.label, e.name) for e in find_chair_calls(turns)] == [("G", "Garten")]


def test_e2_next_we_hear_from():
    turns = build_turns([seg(0, "CHAIR", "Okay, next we will hear from Rabbi Aaron Spiegel."),
                         seg(1, "W", "Thank you.")])
    assert [(e.label, e.name, e.title) for e in find_chair_calls(turns)] == [("W", "Aaron Spiegel", "Rabbi")]


def test_e2_thank_you_name_is_not_a_call():
    turns = build_turns([seg(0, "CHAIR", "Thank you, Senator Garten."), seg(1, "B", "I have a question.")])
    assert find_chair_calls(turns) == []


def test_e2_x1_bill_by_senator_is_not_a_call():
    # "Senator Koch, please" would be a call, but "by" marks a mention of the author (X1).
    turns = build_turns([seg(0, "CHAIR", "We are hearing the bill by Senator Koch, please hold questions."),
                         seg(1, "W", "Hi.")])
    assert find_chair_calls(turns) == []


def test_e2_only_window_before_turn_change():
    early = "Yes, Senator Brown. " + " ".join(["filler"] * 40)
    turns = build_turns([seg(0, "CHAIR", early), seg(1, "B", "Hello.")])
    assert find_chair_calls(turns) == []


def test_e3_thank_back_names_previous_speaker():
    turns = build_turns([seg(0, "W", "That concludes my testimony on this bill."),
                         seg(1, "CHAIR", "Thank you, Ms. Sample. Any questions for Ms. Sample?")])
    ev = find_thank_backs(turns)
    assert [(e.kind, e.label, e.name, e.title) for e in ev] == [("E3", "W", "Sample", "Ms.")]


def test_e3_madam_chair_is_not_a_name():
    turns = build_turns([seg(0, "CHAIR", "Go ahead."), seg(1, "W", "Thank you, Madam Chair, members.")])
    assert find_thank_backs(turns) == []


# FIXES: Same-sentence thanks, bare titles, spec gap patterns

def test_e2_across_sentence_boundary_thank_is_allowed():
    """Thank at end of previous sentence should not exclude next sentence's call."""
    # "Thank you, Erin. Yes, Senator Taylor." → Taylor is a call, not excluded
    turns = build_turns([seg(0, "CHAIR", "Thank you, Erin. Yes, Senator Taylor."),
                         seg(1, "T", "Thank you.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name) for e in ev] == [("T", "Taylor")]


def test_e2_thank_at_sentence_end_then_call():
    """Thank period-separated from call should not exclude."""
    turns = build_turns([seg(0, "CHAIR", "Thank you. Yes, Senator Taylor."),
                         seg(1, "T", "Hello.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name) for e in ev] == [("T", "Taylor")]


def test_e2_thank_separated_then_invitation():
    """Thank period-separated from invitation should not exclude."""
    turns = build_turns([seg(0, "CHAIR", "Thank you. Please go ahead, Senator Garten."),
                         seg(1, "G", "Thank you.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name) for e in ev] == [("G", "Garten")]


def test_e2_thank_same_sentence_call_excluded():
    """Thank in same sentence as call should still exclude."""
    turns = build_turns([seg(0, "CHAIR", "Thank you, Senator Garten."),
                         seg(1, "B", "I have a question.")])
    ev = find_chair_calls(turns)
    assert ev == []


def test_e3_bare_title_rejected():
    """Bare title with no name should be rejected."""
    turns = build_turns([seg(0, "W", "That concludes my remarks."),
                         seg(1, "CHAIR", "Thank you, Senator. Any questions?")])
    ev = find_thank_backs(turns)
    assert ev == []


def test_e2_bare_title_rejected():
    """Chair call ending with bare title should be rejected."""
    turns = build_turns([seg(0, "CHAIR", "... Any questions? Yes, Senator."),
                         seg(1, "W", "Thank you.")])
    ev = find_chair_calls(turns)
    assert ev == []


def test_e2_we_will_call_with_name():
    """'We will call' should be recognized as a call verb."""
    turns = build_turns([seg(0, "CHAIR", "We will call Dr. Smith to testify."),
                         seg(1, "S", "Thank you.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name, e.title) for e in ev] == [("S", "Smith", "Dr.")]


def test_e2_well_call_with_name():
    """'We' + apostrophe + 'll call' should be recognized as a call verb."""
    turns = build_turns([seg(0, "CHAIR", "We'll call Dr. Smith next."),
                         seg(1, "S", "I'm ready.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name, e.title) for e in ev] == [("S", "Smith", "Dr.")]


def test_e2_bare_office_title_at_sentence_end():
    """OFFICE_TITLES + name at sentence/window end should be recognized."""
    turns = build_turns([seg(0, "CHAIR", "Any questions? Senator Garten."),
                         seg(1, "G", "Thank you.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name) for e in ev] == [("G", "Garten")]


def test_e2_bare_title_rejects_courtesy_only():
    """Bare title pattern should only match OFFICE_TITLES, rejecting bare courtesy titles."""
    # "Professor Jones" at end of window should NOT match bare pattern (Professor is courtesy)
    turns = build_turns([seg(0, "CHAIR", "Questions for Professor Jones?"),
                         seg(1, "J", "No.")])
    ev = find_chair_calls(turns)
    # Should have no matches because bare pattern only uses OFFICE_TITLES
    assert ev == []


def test_e2_bare_title_only_whole_sentence_as_i_told():
    """Bare title pattern should NOT match mid-sentence 'Title Name'."""
    # "As I told Senator Brown." has Senator Brown but is not a call (mid-sentence)
    turns = build_turns([seg(0, "CHAIR", "As I told Senator Brown."),
                         seg(1, "B", "Thanks.")])
    ev = find_chair_calls(turns)
    assert ev == []


def test_e2_bare_title_only_whole_sentence_we_worked():
    """Bare title pattern should NOT match mid-sentence 'Title Name'."""
    # "We worked with Senator Brown." has Senator Brown but is not a call
    turns = build_turns([seg(0, "CHAIR", "We worked with Senator Brown."),
                         seg(1, "B", "Thanks.")])
    ev = find_chair_calls(turns)
    assert ev == []


def test_e2_bare_title_only_whole_sentence_i_spoke():
    """Bare title pattern should NOT match mid-sentence 'Title Name'."""
    # "I spoke to Mayor Smith." has Mayor Smith but is not a call
    turns = build_turns([seg(0, "CHAIR", "I spoke to Mayor Smith."),
                         seg(1, "S", "Thanks.")])
    ev = find_chair_calls(turns)
    assert ev == []


def test_e2_bare_title_whole_sentence_only_title_name():
    """Bare title pattern should match when sentence is only 'Title Name'."""
    # "Thank you, Madam Chair. Senator Koch." is a call (second sentence is just title+name)
    turns = build_turns([seg(0, "CHAIR", "Thank you, Madam Chair. Senator Koch."),
                         seg(1, "K", "Thank you.")])
    ev = find_chair_calls(turns)
    assert [(e.label, e.name) for e in ev] == [("K", "Koch")]

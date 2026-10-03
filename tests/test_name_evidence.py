from __future__ import annotations

from src.models import Segment
from src.name_evidence import build_turns, find_self_intros


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


def test_x1_mention_of_others_is_ignored():
    ev = find_self_intros(build_turns([seg(0, "W", "As my colleague said, I'm Glad Tidings Lane "
                                                  "is not a name here " + FILLER)]))
    assert ev == []
    ev2 = find_self_intros(build_turns([seg(0, "W", "My wife told me my name is Mud with this crowd " + FILLER)]))
    assert ev2 == []


def test_x2_intro_outside_window_is_ignored():
    long_lead = " ".join(["word"] * 70)
    ev = find_self_intros(build_turns([seg(0, "W", long_lead + " my name is Late Person.")]))
    assert ev == []


def test_short_first_turn_is_skipped_for_first_substantial_turn():
    turns = build_turns([seg(0, "W", "Thank you."), seg(1, "C", "Go ahead."),
                         seg(2, "W", "My name is Lauren Murphy, from Fishers. " + FILLER)])
    assert [e.name for e in find_self_intros(turns)] == ["Lauren Murphy"]

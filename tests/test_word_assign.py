from __future__ import annotations

from src.models import Segment, Word
from src.word_assign import assign_words_to_segments, snap_segment_boundaries


def _seg(seg_id, start, end, label):
    return Segment(segment_id=seg_id, start_time=start, end_time=end, speaker_label=label)


def _tokens(seg):
    return [w.word for w in seg.words]


def test_assigns_word_by_midpoint_to_containing_segment():
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "B")]
    words = [Word("hello", 0.2, 0.8), Word("there", 2.2, 2.8)]

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[0].words] == ["hello"]
    assert [w.word for w in segs[1].words] == ["there"]
    assert segs[0].text == "hello"
    assert segs[1].text == "there"


def test_hilton_clip_assigns_continuous_words_to_correct_speakers():
    # Diarization turns (from diarization.json, ~17:06-17:15).
    segs = [
        _seg(0, 1026.655, 1029.254, "SPEAKER_00"),  # Steve
        _seg(1, 1029.322, 1029.777, "SPEAKER_01"),  # Hailey (short)
        _seg(2, 1029.777, 1030.283, "SPEAKER_00"),  # Steve
        _seg(3, 1030.570, 1034.148, "SPEAKER_00"),  # Steve
        _seg(4, 1034.148, 1034.519, "SPEAKER_01"),  # Hailey (short)
        _seg(5, 1034.603, 1036.949, "SPEAKER_00"),  # Steve
    ]
    # Continuous-transcription word timings (from spike: drift-free, accurate).
    words = [
        Word("abortion", 1028.18, 1028.64),
        Word("tourism", 1028.64, 1029.20),
        Word("where", 1029.20, 1030.20),
        Word("other", 1033.44, 1033.70),
        Word("states", 1033.70, 1034.20),
    ]

    assign_words_to_segments(words, segs)

    def owner(token):
        for s in segs:
            if any(w.word == token for w in s.words):
                return s.speaker_label
        return None

    assert owner("abortion") == "SPEAKER_00"  # Steve's "abortion tourism"
    assert owner("tourism") == "SPEAKER_00"
    assert owner("other") == "SPEAKER_00"      # Steve's "ads in other states"


def test_short_turn_does_not_steal_boundary_word_from_dominant():
    # Steve speaks continuously; a 0.37s Hailey turn sits at a word boundary.
    segs = [
        _seg(0, 1030.570, 1034.148, "SPEAKER_00"),  # Steve (long)
        _seg(1, 1034.148, 1034.519, "SPEAKER_01"),  # Hailey (0.371s, short)
        _seg(2, 1034.603, 1036.949, "SPEAKER_00"),  # Steve (long)
    ]
    # "saying" spans the boundary; midpoint 1034.56 lies in the gap, not inside
    # the short Hailey turn. It must NOT be handed to Hailey.
    words = [Word("saying", 1034.20, 1034.92)]

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[1].words] == []        # Hailey gets nothing
    assert "saying" in [w.word for w in segs[0].words] or \
           "saying" in [w.word for w in segs[2].words]  # stays with Steve


def test_short_turn_keeps_word_that_mostly_fits_inside_it():
    # Roll-call shape: clerk calls a name, member answers "Here." in a brief
    # turn, clerk continues. The member's word mostly fills their short turn, so
    # it must stay with the member (protects council vote/roll-call records).
    segs = [
        _seg(0, 0.0, 5.0, "CLERK"),       # long
        _seg(1, 5.0, 5.5, "MEMBER"),      # 0.5s short turn
        _seg(2, 5.6, 10.0, "CLERK"),      # long
    ]
    words = [Word("Here.", 5.05, 5.45)]   # 0.4s word, ~100% inside MEMBER turn

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[1].words] == ["Here."]  # member keeps it
    assert segs[0].words == [] and segs[2].words == []


def test_gap_word_snaps_to_preceding_long_turn():
    # A word landing in the silent gap between two turns (overlapping neither)
    # snaps to the preceding turn.
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 5.0, 7.0, "B")]
    words = [Word("trailing", 2.4, 2.8)]  # midpoint 2.6 in the 2.0-5.0 gap

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[0].words] == ["trailing"]
    assert segs[1].words == []


def test_no_midpoint_match_falls_back_to_max_overlap_long_turn():
    # Word midpoint lands in a gap but the word overlaps a long turn; it goes to
    # that turn via the overlap fallback (not dropped).
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.5, 6.0, "B")]
    words = [Word("spanning", 1.9, 2.7)]  # midpoint 2.3 in gap; overlaps both

    assign_words_to_segments(words, segs)

    owner = "A" if any(w.word == "spanning" for w in segs[0].words) else \
            ("B" if any(w.word == "spanning" for w in segs[1].words) else None)
    assert owner is not None  # not dropped


# --- boundary-snap: fixes turn-boundary word bleed -------------------------

def test_marker_leading_fragment_moves_to_previous_segment():
    # The reported symptom: a moderator's sentence tail ("district.") is captured
    # at the START of the next speaker's segment, right before the '>>' marker.
    # Mirrors real seg 18/19 of the CD1 debate. "district." must move back.
    a = _seg(0, 490.60, 597.52, "SPEAKER_03")
    b = _seg(1, 597.52, 609.42, "SPEAKER_05")
    a.words = [Word("this", 597.38, 597.50)]
    b.words = [
        Word("district.", 597.50, 597.63),   # trailing tail of A, bled into B
        Word(">>", 598.18, 598.29),
        Word("Ms.", 598.29, 598.39),
        Word("Colon", 598.39, 598.50),
        Word("Woods", 598.50, 598.61),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["this", "district."]
    assert _tokens(b) == [">>", "Ms.", "Colon", "Woods"]


def test_marker_does_not_move_long_lead_before_late_marker():
    # A full candidate turn that merely ENDS with ">> Okay." (real seg 45). The
    # long lead is the candidate's own speech and must NOT be dragged backward.
    a = _seg(0, 1200.0, 1300.0, "MODERATOR")
    b = _seg(1, 1300.0, 1400.0, "CANDIDATE")
    a.words = [Word("question?", 1299.5, 1300.0)]
    lead = [Word(f"w{i}", 1300.0 + i * 0.1, 1300.0 + i * 0.1 + 0.09) for i in range(150)]
    lead[-1] = Word("interests.", lead[-1].start, lead[-1].end)
    b.words = lead + [Word(">>", 1399.0, 1399.1), Word("Okay.", 1399.1, 1399.4)]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["question?"]              # unchanged
    assert _tokens(b)[0] == "w0"                     # lead stayed with B


def test_marker_trailing_fragment_moves_to_next_segment():
    # The next speaker's opening ("Thank") captured at the TAIL of a moderator
    # turn after a late '>>' (real seg 6). It must move to the next segment.
    a = _seg(0, 100.0, 126.0, "MODERATOR")
    b = _seg(1, 126.24, 189.60, "CANDIDATE")
    a.words = [
        Word(">>", 100.0, 100.1),
        Word("one", 100.1, 100.3),
        Word("minute.", 125.4, 125.6),
        Word(">>", 125.9, 126.0),
        Word("Thank", 126.0, 126.2),
    ]
    b.words = [Word("you", 126.24, 126.4), Word("very", 126.4, 126.6)]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == [">>", "one", "minute."]
    assert _tokens(b) == [">>", "Thank", "you", "very"]


def test_pause_and_punctuation_move_bleed_word_without_marker():
    # Whole-audio Whisper meetings carry no '>>'. Fall back to the acoustic
    # signal: a terminal-punctuation word butted against A (gap≈0) with a real
    # pause before B's own content groups with A.
    a = _seg(0, 0.0, 5.0, "A")
    b = _seg(1, 5.0, 12.0, "B")
    a.words = [Word("in", 4.7, 4.9), Word("this", 4.9, 5.02)]
    b.words = [
        Word("district.", 5.02, 5.20),   # gap_before≈0.0, continuous with A
        Word("So,", 5.95, 6.10),         # gap_after≈0.75 → real pause
        Word("thank", 6.10, 6.30),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["in", "this", "district."]
    assert _tokens(b) == ["So,", "thank"]


def test_no_move_when_word_is_own_sentence_after_turn_pause():
    # A genuine short opener: "Yes." starts B after a real turn-change silence
    # (gap_before is large, not near-zero). It must stay with B.
    a = _seg(0, 0.0, 5.0, "A")
    b = _seg(1, 5.5, 12.0, "B")
    a.words = [Word("agree?", 4.7, 4.95)]
    b.words = [
        Word("Yes.", 5.55, 5.75),   # gap_before≈0.6 (turn silence) → B's own word
        Word("So", 6.30, 6.45),
        Word("here", 6.45, 6.65),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["agree?"]
    assert _tokens(b) == ["Yes.", "So", "here"]


def test_snap_never_empties_a_segment():
    # A one-word segment that looks like a leading fragment must not be emptied.
    a = _seg(0, 0.0, 5.0, "A")
    b = _seg(1, 5.0, 6.0, "B")
    a.words = [Word("this", 4.8, 5.0)]
    b.words = [Word("done.", 5.0, 5.2)]   # single terminal word, no following word

    snap_segment_boundaries([a, b])

    assert _tokens(b) == ["done."]   # kept — moving it would empty B


def test_pause_rule_does_not_steal_vote_from_overlapping_turn():
    # Real council roll-call shape: the clerk's diarized span (long) OVERLAPS a
    # member's brief "Yes." turn, so the clerk's last word ends AFTER the
    # member's word starts (negative gap_before). That is not a bleed — the
    # member's vote must stay with the member, not be handed to the clerk.
    clerk = _seg(0, 122.78, 132.26, "CLERK")
    member = _seg(1, 123.12, 125.23, "MEMBER")
    clerk.words = [Word("Ready?", 122.78, 123.26), Word("Piedmont", 131.9, 132.2)]
    member.words = [
        Word("Yes.", 123.12, 123.90),      # the member's vote
        Word("Stasberg?", 124.26, 124.70),
        Word("Yes.", 124.94, 125.00),
    ]

    snap_segment_boundaries([clerk, member])

    assert _tokens(member)[0] == "Yes."           # vote stays with the member
    assert "Yes." not in _tokens(clerk)[1:]        # clerk did not steal it


def test_snap_is_idempotent_across_degenerate_segments():
    # Real shape from an interview: an under-segmented turn with several mid-turn
    # '>>' markers, next to a turn whose word timestamps run past its own span. A
    # single left-to-right pass relays a trailing '>> Yes.' fragment but does not
    # settle — a second pass moves it again. Snapping must reach a fixpoint, so
    # snapping twice equals snapping once.
    s12 = _seg(12, 198.667, 201.113, "S0")
    s12.words = [Word(">>", 198.833, 199.01), Word("it.", 199.893, 200.07),
                 Word("step.", 201.281, 201.39)]
    s13 = _seg(13, 201.113, 206.952, "S6")
    s13.words = [
        Word(">>", 201.856, 201.947), Word("happen.", 204.698, 204.771),
        Word("okay,", 204.844, 204.917), Word("so", 204.917, 204.99),
        Word(">>", 205.287, 205.334), Word("I", 205.334, 205.382),
        Word("agree.", 205.382, 205.43), Word(">>", 205.727, 205.823),
        Word("it.", 206.014, 206.11), Word(">>", 206.353, 206.412),
        Word("Yes.", 206.412, 206.47),
    ]
    s14 = _seg(14, 203.645, 205.467, "S0")
    s14.words = [Word(">>", 206.842, 207.023)]
    segs = [s12, s13, s14]

    snap_segment_boundaries(segs)
    once = [_tokens(s) for s in segs]
    snap_segment_boundaries(segs)
    twice = [_tokens(s) for s in segs]

    assert once == twice

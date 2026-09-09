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


# --- Trailing self-introduction bleed (marker-less) -------------------------
#
# Diarization can miss a stretch of speech entirely; _segment_for_gap_word then
# snaps the whole un-diarized gap onto the preceding turn, so a chair's turn
# swallows the next speaker's opening self-introduction. The word lists and
# timings below are lifted verbatim from the corpus (transcript_named.json).


def test_trailing_self_intro_moves_off_the_chair_bloomington_july():
    # 2026-07-22-bloomington-regular-session seg 18: the chair's 2.1s turn
    # carries 9.8s of words, ending with the next speaker's introduction. The
    # published page showed councilmember Isak Nti Asare saying "my name is
    # Emma Williams" — Emma Williams is the speaker of the following turn.
    a = _seg(18, 497.101, 501.303, "SPEAKER_05")   # Isak Nti Asare
    b = _seg(19, 507.023, 582.758, "SPEAKER_09")   # Emma Williams
    a.words = [
        Word("-", 496.93, 497.474),
        Word("There", 497.474, 498.018),
        Word("is", 498.018, 498.562),
        Word("no", 498.562, 499.106),
        Word("wrong", 499.106, 499.65),
        Word("side", 499.65, 500.194),
        Word("Perfect,", 500.194, 500.738),
        Word("thank", 500.738, 501.282),
        Word("you", 501.282, 501.826),
        Word("So", 501.826, 502.37),
        Word("good", 502.37, 502.914),
        Word("evening,", 502.914, 503.459),
        Word("my", 503.459, 504.003),
        Word("name", 504.003, 504.547),
        Word("is", 504.547, 505.091),
        Word("Emma", 505.091, 505.635),
        Word("Williams", 505.635, 506.179),
        Word("and", 506.179, 506.723),
    ]
    b.words = [
        Word("I", 506.723, 507.267),
        Word("serve", 507.267, 507.811),
        Word("as", 507.811, 508.355),
        Word("the", 508.355, 508.899),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "evening,"
    assert _tokens(b)[:6] == ["my", "name", "is", "Emma", "Williams", "and"]


def test_trailing_self_intro_moves_off_the_chair_bloomington_june():
    # bloomington-city-council-2026-06-10 seg 454: the chair's 2.16s turn
    # carries 14.3s of words. "my name is Paul Gillard I'm" belongs to the
    # following turn, whose text continues "a former business owner...".
    a = _seg(454, 9270.869, 9273.029, "SPEAKER_22")   # At-Large Asare
    b = _seg(455, 9285.297, 9338.69, "SPEAKER_01")    # Paul Gillard
    a.words = [
        Word("Thank", 9270.655, 9271.452),
        Word("you.", 9271.452, 9272.248),
        Word("Thank", 9272.248, 9273.045),
        Word("you", 9273.045, 9273.842),
        Word("so", 9273.842, 9274.639),
        Word("much", 9274.639, 9275.435),
        Word("next", 9275.435, 9276.232),
        Word("person", 9276.232, 9277.029),
        Word("in", 9277.029, 9277.826),
        Word("chambers", 9277.826, 9278.622),
        Word("Thank", 9278.622, 9279.419),
        Word("you,", 9279.419, 9280.216),
        Word("my", 9280.216, 9281.013),
        Word("name", 9281.013, 9281.809),
        Word("is", 9281.809, 9282.606),
        Word("Paul", 9282.606, 9283.403),
        Word("Gillard", 9283.403, 9284.2),
        Word("I'm", 9284.2, 9284.996),
    ]
    b.words = [
        Word("a", 9284.996, 9285.793),
        Word("former", 9285.793, 9286.59),
        Word("-", 9286.59, 9287.408),
        Word("business", 9287.408, 9288.226),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "you,"
    assert _tokens(b)[:6] == ["my", "name", "is", "Paul", "Gillard", "I'm"]


def test_trailing_self_intro_moves_even_when_the_name_is_cut_off():
    # 2026-07-22-bloomington-regular-session seg 190. The bleed stops at "My
    # name is", so a detector that needs a matchable name cannot see it, but
    # the next turn opens "Nathan Ferrer. I'm the executive director...".
    a = _seg(190, 2124.172, 2132.204, "SPEAKER_05")   # Isak Nti Asare
    b = _seg(191, 2135.528, 2162.14, "SPEAKER_24")    # Nathan Ferreira
    a.words = [
        Word("adopted.", 2123.982, 2124.386),
        Word("Second.", 2124.386, 2124.791),
        Word("-", 2124.791, 2125.173),
        Word("All", 2125.173, 2125.555),
        Word("right.", 2125.555, 2125.938),
        Word("Do", 2125.938, 2126.32),
        Word("we", 2126.32, 2126.702),
        Word("have", 2126.702, 2127.084),
        Word("someone", 2127.084, 2127.466),
        Word("here", 2127.466, 2127.849),
        Word("to", 2127.849, 2128.231),
        Word("present?", 2128.231, 2128.613),
        Word("I", 2128.613, 2128.995),
        Word("assume", 2128.995, 2129.378),
        Word("that", 2129.378, 2129.76),
        Word("fantastic.", 2129.76, 2130.142),
        Word("-", 2131.394, 2131.809),
        Word("Take", 2131.809, 2132.224),
        Word("it", 2132.224, 2132.64),
        Word("away.", 2132.64, 2133.055),
        Word("Good", 2133.055, 2133.47),
        Word("evening.", 2133.47, 2133.885),
        Word("My", 2133.885, 2134.301),
        Word("name", 2134.301, 2134.716),
        Word("is", 2134.716, 2135.131),
    ]
    b.words = [
        Word("Nathan", 2135.131, 2135.546),
        Word("Ferrer.", 2135.546, 2135.961),
        Word("I'm", 2135.961, 2136.377),
        Word("the", 2136.377, 2136.792),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "evening."
    assert _tokens(b)[:3] == ["My", "name", "is"]


def test_trailing_self_intro_takes_the_greeting_after_a_sentence_end():
    # bloomington-city-council-2026-06-10 seg 475 (head trimmed). "people."
    # ends the chair's sentence, so the greeting "Hi," goes with the
    # introduction rather than staying behind.
    a = _seg(475, 10013.403, 10028.658, "SPEAKER_22")   # At-Large Asare
    b = _seg(476, 10030.463, 10119.867, "SPEAKER_10")   # Alex Jorck
    a.words = [
        Word("people.", 10025.958, 10026.387),
        Word("Go", 10026.387, 10026.816),
        Word("ahead,", 10026.816, 10027.246),
        Word("two", 10027.246, 10027.675),
        Word("more", 10027.675, 10028.104),
        Word("people.", 10028.104, 10028.534),
        Word("Hi,", 10028.534, 10028.963),
        Word("my", 10028.963, 10029.392),
        Word("name", 10029.392, 10029.822),
        Word("is", 10029.822, 10030.251),
    ]
    b.words = [
        Word("Alex", 10030.251, 10030.68),
        Word("York,", 10030.68, 10031.11),
        Word("for", 10031.11, 10031.539),
        Word("-", 10031.539, 10031.959),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "people."
    assert _tokens(b)[:4] == ["Hi,", "my", "name", "is"]


def test_trailing_self_intro_moves_the_whole_run_past_the_name():
    # bloomington-city-council-2026-06-10 seg 440 (head trimmed). The bleed
    # runs past the name into the speaker's next clause; the following turn
    # continues "want to reiterate support for this."
    a = _seg(440, 8685.492, 8698.182, "SPEAKER_22")   # At-Large Asare
    b = _seg(441, 8703.667, 8744.352, "SPEAKER_30")   # Claire Woods
    a.words = [
        Word("for", 8699.523, 8699.847),
        Word("it.", 8699.847, 8700.17),
        Word("I'll", 8700.17, 8700.494),
        Word("go", 8700.494, 8700.817),
        Word("next.", 8700.817, 8701.141),
        Word("My", 8701.141, 8701.464),
        Word("name's", 8701.464, 8701.787),
        Word("Claire", 8701.787, 8702.111),
        Word("Woods.", 8702.111, 8702.434),
        Word("I", 8702.434, 8702.758),
        Word("just", 8702.758, 8703.081),
        Word("also", 8703.081, 8703.405),
    ]
    b.words = [
        Word("want", 8703.405, 8703.728),
        Word("-", 8703.728, 8704.096),
        Word("to", 8704.096, 8704.465),
        Word("reiterate", 8704.465, 8704.833),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "next."
    assert _tokens(b)[:7] == ["My", "name's", "Claire", "Woods.", "I", "just", "also"]


def test_trailing_self_intro_moves_off_a_very_long_turn():
    # bloomington-city-council-2026-06-10 seg 194 (head trimmed from 825
    # words). A long, correctly-attributed turn still ends in someone else's
    # introduction; the next turn opens "Peter Pearson. I'm the chief
    # economist...".
    a = _seg(194, 2724.213, 3072.665, "SPEAKER_36")   # District 4 Rollo
    b = _seg(195, 3076.057, 3253.447, "SPEAKER_11")   # Peter Berezin
    a.words = [
        Word("short", 3070.97, 3071.442),
        Word("video?", 3071.442, 3071.915),
        Word("It's", 3071.915, 3072.387),
        Word("about", 3072.387, 3072.859),
        Word("three", 3072.859, 3073.332),
        Word("minutes.", 3073.332, 3073.804),
        Word("Hello,", 3073.804, 3074.277),
        Word("my", 3074.277, 3074.749),
        Word("name", 3074.749, 3075.222),
        Word("is", 3075.222, 3075.694),
    ]
    b.words = [
        Word("Peter", 3075.694, 3076.167),
        Word("Pearson.", 3076.167, 3076.639),
        Word("-", 3076.639, 3077.078),
        Word("I'm", 3077.078, 3077.517),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "minutes."
    assert _tokens(b)[:4] == ["Hello,", "my", "name", "is"]


def test_trailing_self_intro_stays_put_when_the_next_turn_has_no_words():
    # bloomington-city-council-2026-06-10 seg 416 (head trimmed). The next turn
    # carries no words at all, so there is no real destination: publish drops
    # empty segments, and the bleed names "Hartzell" while that turn's speaker
    # is someone else. Moving the words would mint a new, unverifiable claim.
    a = _seg(416, 8076.575, 8082.633, "SPEAKER_22")   # At-Large Asare
    b = _seg(417, 8077.773, 8077.773, "SPEAKER_18")   # Hilary Martel, no words
    a.words = [
        Word("those", 8090.168, 8090.774),
        Word("comments", 8090.774, 8091.381),
        Word("My", 8091.381, 8091.987),
        Word("name", 8091.987, 8092.593),
        Word("is", 8092.593, 8093.2),
        Word("Hartzell", 8093.2, 8093.806),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["those", "comments", "My", "name", "is", "Hartzell"]
    assert _tokens(b) == []


def test_self_intro_inside_the_turns_own_span_is_not_a_bleed():
    # 2026-02-04-council seg 97: Bob Costello introducing himself in his own
    # turn, which ASR spells "Bob Gasillo". The introduction lies inside the
    # turn's own diarized span, so it is real speech and must not move. This
    # is what keeps the rule off ASR spelling variants of the same person.
    a = _seg(97, 2444.003, 2581.585, "SPEAKER_28")   # Bob Costello
    b = _seg(98, 2583.003, 2587.643, "SPEAKER_21")   # Isak Nti Asare
    a.words = [
        Word("Thank", 2444.003, 2444.003),
        Word("you", 2444.003, 2444.003),
        Word("very", 2444.003, 2444.003),
        Word("much.", 2444.003, 2444.023),
        Word("Hi,", 2445.033, 2445.253),
        Word("my", 2445.353, 2445.413),
        Word("name", 2445.413, 2445.533),
        Word("is", 2445.533, 2445.733),
        Word("Bob", 2445.973, 2445.973),
        Word("Gasillo.", 2445.973, 2446.413),
        Word("I'm", 2446.593, 2446.833),
    ]
    b.words = [
        Word("Thank", 2583.003, 2583.343),
        Word("you.", 2583.343, 2583.883),
        Word("We", 2584.003, 2584.123),
        Word("have", 2584.123, 2584.483),
    ]
    before_a, before_b = _tokens(a), _tokens(b)

    snap_segment_boundaries([a, b])

    assert _tokens(a) == before_a
    assert _tokens(b) == before_b


def test_leading_self_intro_is_the_speakers_own_turn():
    # An introduction opening a turn is that speaker's own, even when the words
    # spill past the diarized span. Moving it would hand the turn's opening to
    # the wrong person.
    a = _seg(0, 100.0, 102.0, "SPEAKER_00")
    b = _seg(1, 110.0, 120.0, "SPEAKER_01")
    a.words = [
        Word("my", 103.0, 103.4),
        Word("name", 103.4, 103.8),
        Word("is", 103.8, 104.2),
        Word("Dana", 104.2, 104.6),
        Word("Reed", 104.6, 105.0),
    ]
    b.words = [Word("Next", 110.0, 110.4), Word("item.", 110.4, 110.8)]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["my", "name", "is", "Dana", "Reed"]
    assert _tokens(b) == ["Next", "item."]


def test_trailing_self_intro_snap_is_idempotent():
    # The backfill re-snaps in place, so a second pass must move nothing.
    def build():
        a = _seg(454, 9270.869, 9273.029, "SPEAKER_22")
        b = _seg(455, 9285.297, 9338.69, "SPEAKER_01")
        a.words = [
            Word("Thank", 9270.655, 9271.452), Word("you.", 9271.452, 9272.248),
            Word("Thank", 9272.248, 9273.045), Word("you", 9273.045, 9273.842),
            Word("so", 9273.842, 9274.639), Word("much", 9274.639, 9275.435),
            Word("next", 9275.435, 9276.232), Word("person", 9276.232, 9277.029),
            Word("in", 9277.029, 9277.826), Word("chambers", 9277.826, 9278.622),
            Word("Thank", 9278.622, 9279.419), Word("you,", 9279.419, 9280.216),
            Word("my", 9280.216, 9281.013), Word("name", 9281.013, 9281.809),
            Word("is", 9281.809, 9282.606), Word("Paul", 9282.606, 9283.403),
            Word("Gillard", 9283.403, 9284.2), Word("I'm", 9284.2, 9284.996),
        ]
        b.words = [Word("a", 9284.996, 9285.793), Word("former", 9285.793, 9286.59)]
        return [a, b]

    segs = build()
    snap_segment_boundaries(segs)
    once = [_tokens(s) for s in segs]
    snap_segment_boundaries(segs)
    twice = [_tokens(s) for s in segs]

    assert once == twice

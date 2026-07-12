"""Tests for scoped acoustic re-verification of short wedged turns.

A "wedged turn" is a short segment by speaker X sandwiched between two segments
of speaker Y (with small gaps), carrying 1-2 words. These are where Whisper's
per-segment slicing steals a word from the dominant speaker (e.g. "abortion"
attributed to the listener). The re-verifier re-checks each wedged turn's audio
against the two neighbour speakers' voice embeddings and proposes reassignment.
"""

from __future__ import annotations

import numpy as np

from src.models import Segment, Word


def _seg(seg_id, start, end, label, n_words=1, text="word"):
    words = [
        Word(word=f"{text}{i}", start=start, end=end) for i in range(n_words)
    ]
    return Segment(
        segment_id=seg_id,
        start_time=start,
        end_time=end,
        speaker_label=label,
        text=text,
        words=words,
    )


def test_find_wedged_turns_detects_short_sandwiched_turn():
    from src.reverify import find_wedged_turns

    segments = [
        _seg(0, 0.0, 5.0, "SPEAKER_00", n_words=10),   # dominant run
        _seg(1, 5.1, 5.5, "SPEAKER_01", n_words=1),    # wedged 0.4s, 1 word
        _seg(2, 5.6, 10.0, "SPEAKER_00", n_words=10),  # dominant resumes
    ]

    assert find_wedged_turns(segments) == [1]


def test_find_wedged_turns_ignores_non_wedged():
    from src.reverify import find_wedged_turns

    segments = [
        # neighbours are different speakers -> normal turn-taking, not wedged
        _seg(0, 0.0, 5.0, "SPEAKER_00", n_words=10),
        _seg(1, 5.1, 5.5, "SPEAKER_01", n_words=1),
        _seg(2, 5.6, 10.0, "SPEAKER_02", n_words=10),
        # long turn between same speaker -> a real turn, not a stolen fragment
        _seg(3, 10.1, 13.0, "SPEAKER_01", n_words=1),
        _seg(4, 13.1, 16.0, "SPEAKER_00", n_words=10),
        # big gap before -> a deliberate response, not an interruption
        _seg(5, 30.0, 35.0, "SPEAKER_00", n_words=10),
        _seg(6, 40.0, 40.4, "SPEAKER_01", n_words=1),
        _seg(7, 40.5, 45.0, "SPEAKER_00", n_words=10),
        # too many words -> substantive, leave for normal handling
        _seg(8, 45.1, 45.6, "SPEAKER_01", n_words=5),
        _seg(9, 45.7, 50.0, "SPEAKER_00", n_words=10),
    ]

    assert find_wedged_turns(segments) == []


def test_decide_speaker_matches_dominant_voice():
    from src.reverify import decide_speaker

    dominant = np.array([1.0, 0.0])
    interrupter = np.array([0.0, 1.0])
    turn = np.array([0.95, 0.05])  # clearly the dominant speaker's voice

    assert decide_speaker(turn, dominant, interrupter, margin=0.1) == "dominant"


def test_decide_speaker_keeps_interrupter_voice():
    from src.reverify import decide_speaker

    dominant = np.array([1.0, 0.0])
    interrupter = np.array([0.0, 1.0])
    turn = np.array([0.05, 0.95])  # the interrupter's own voice (legit backchannel)

    assert decide_speaker(turn, dominant, interrupter, margin=0.1) == "interrupter"


def test_decide_speaker_ambiguous_when_within_margin():
    from src.reverify import decide_speaker

    dominant = np.array([1.0, 0.0])
    interrupter = np.array([0.0, 1.0])
    turn = np.array([1.0, 1.0])  # equidistant -> too close to call

    assert decide_speaker(turn, dominant, interrupter, margin=0.1) == "ambiguous"


def test_reverify_proposes_reassign_for_stolen_word_and_keep_for_backchannel():
    from src.reverify import reverify

    centroids = {
        "SPEAKER_00": np.array([1.0, 0.0]),  # dominant (interviewee)
        "SPEAKER_01": np.array([0.0, 1.0]),  # interrupter (listener)
    }
    segments = [
        _seg(0, 0.0, 5.0, "SPEAKER_00", n_words=10),
        _seg(1, 5.1, 5.6, "SPEAKER_01", n_words=1, text="abortion"),  # STOLEN
        _seg(2, 5.7, 10.0, "SPEAKER_00", n_words=10),
        _seg(3, 10.1, 10.5, "SPEAKER_01", n_words=1, text="yeah"),    # LEGIT
        _seg(4, 10.6, 15.0, "SPEAKER_00", n_words=10),
    ]

    def embed_fn(start, end):
        # turn at 5.1 is really the dominant's voice; turn at 10.1 is the interrupter's
        if abs(start - 5.1) < 0.01:
            return np.array([0.97, 0.03])
        return np.array([0.03, 0.97])

    proposals = reverify(segments, centroids, embed_fn, margin=0.1, min_embed_dur=0.3)

    assert [p.index for p in proposals] == [1, 3]
    assert proposals[0].decision == "dominant"
    assert proposals[0].action == "reassign"
    assert proposals[0].dominant_label == "SPEAKER_00"
    assert proposals[1].decision == "interrupter"
    assert proposals[1].action == "keep"
    # dry-run: segments are NOT mutated
    assert segments[1].speaker_label == "SPEAKER_01"


def test_reverify_flags_unembeddable_short_turn_for_review():
    from src.reverify import reverify

    centroids = {"SPEAKER_00": np.array([1.0, 0.0]), "SPEAKER_01": np.array([0.0, 1.0])}
    segments = [
        _seg(0, 0.0, 5.0, "SPEAKER_00", n_words=10),
        _seg(1, 5.10, 5.25, "SPEAKER_01", n_words=1),  # 0.15s — below embed floor
        _seg(2, 5.30, 10.0, "SPEAKER_00", n_words=10),
    ]

    def embed_fn(start, end):  # pragma: no cover - should not be called
        raise AssertionError("must not embed a sub-floor turn")

    proposals = reverify(segments, centroids, embed_fn, margin=0.1, min_embed_dur=0.3)

    assert len(proposals) == 1
    assert proposals[0].action == "flag"
    assert proposals[0].decision == "unembeddable"


def test_apply_proposals_relabels_only_reassignments():
    from src.reverify import Proposal, apply_proposals

    segments = [
        _seg(0, 0.0, 5.0, "SPEAKER_00", n_words=10),
        _seg(1, 5.1, 5.6, "SPEAKER_01", n_words=1, text="abortion"),
        _seg(2, 5.7, 10.0, "SPEAKER_00", n_words=10),
        _seg(3, 10.1, 10.5, "SPEAKER_01", n_words=1, text="yeah"),
        _seg(4, 10.6, 15.0, "SPEAKER_00", n_words=10),
    ]
    proposals = [
        Proposal(1, "abortion", "SPEAKER_01", "SPEAKER_00", "dominant", "reassign"),
        Proposal(3, "yeah", "SPEAKER_01", "SPEAKER_00", "interrupter", "keep"),
    ]

    n = apply_proposals(segments, proposals)

    assert n == 1
    assert segments[1].speaker_label == "SPEAKER_00"   # stolen word reassigned
    assert segments[1].id_method == "acoustic_reverify"
    assert segments[3].speaker_label == "SPEAKER_01"   # backchannel left alone

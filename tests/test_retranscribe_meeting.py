"""retranscribe_meeting.py: loop detection and the text-only merge."""
from __future__ import annotations

import pytest

import retranscribe_meeting as rt
from src.models import Segment, Word


def test_repetition_runs_finds_whisper_loop():
    text = "it was a big, big, big, big, big, big, big switch"
    assert rt.repetition_runs(text) == [("big,", 7)]


def test_repetition_runs_ignores_normal_speech():
    assert rt.repetition_runs("thank you thank you so much for having me") == []


def test_repetition_runs_finds_multiword_loop():
    text = "Thank you. " * 6 + "Next speaker."
    assert rt.repetition_runs(text) == [("Thank you.", 6)]


def _seg(i, a, b, label, name=None):
    s = Segment(i, a, b, label)
    s.speaker_name = name
    return s


def test_apply_new_text_keeps_times_labels_and_names():
    segs = [_seg(0, 0.0, 2.0, "SPEAKER_00", "Ann"), _seg(1, 2.0, 4.0, "SPEAKER_01", "Bo")]
    fresh = [
        {**_seg(0, 0.0, 1.9, "X").to_dict(), "text": "Hello there.",
         "words": [Word("Hello", 0.1, 0.5).to_dict(), Word("there.", 0.5, 0.9).to_dict()]},
        {**_seg(1, 2.1, 4.0, "Y").to_dict(), "text": "Hi.",
         "words": [Word("Hi.", 2.2, 2.5).to_dict()]},
    ]
    rt.apply_new_text(segs, fresh)
    assert [s.text for s in segs] == ["Hello there.", "Hi."]
    assert [(s.start_time, s.end_time, s.speaker_label, s.speaker_name) for s in segs] == [
        (0.0, 2.0, "SPEAKER_00", "Ann"), (2.0, 4.0, "SPEAKER_01", "Bo")]


def test_apply_new_text_rejects_mismatched_ids():
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "B")]
    with pytest.raises(ValueError):
        rt.apply_new_text(segs, [_seg(0, 0.0, 2.0, "A").to_dict()])


def test_reassign_raw_moves_words_onto_premerge_turns():
    named = [_seg(0, 0.0, 4.0, "A")]
    named[0].words = [Word("one", 0.2, 0.6), Word("two", 2.4, 2.8)]
    raw = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "A")]
    rt.reassign_raw(raw, named)
    assert [s.text for s in raw] == ["one", "two"]

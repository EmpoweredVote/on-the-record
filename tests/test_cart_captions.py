"""Live CART caption cleanup (all caps, >> turn markers, timing offset)."""
from __future__ import annotations

from pathlib import Path

from src.cart_captions import (
    anchor_pairs,
    clean_cart_words,
    estimate_lag,
    looks_like_cart,
    sentence_case,
    strip_markers,
    warp,
)
from src.models import Segment, Word
from src.vtt_align import _deduplicated_words, align_vtt_to_segments, parse_vtt

FIX = Path(__file__).parent / "fixtures" / "iga"


def align_vtt_to_segments_from_words(words, segs):
    from src.word_assign import assign_words_to_segments

    assign_words_to_segments(words, segs)
    return segs


def _w(text: str, start: float, step: float = 0.5) -> list[Word]:
    return [Word(word=t, start=start + i * step, end=start + (i + 1) * step)
            for i, t in enumerate(text.split())]


def _seg(label: str, start: float, end: float, sid: int = 0) -> Segment:
    return Segment(segment_id=sid, start_time=start, end_time=end, speaker_label=label)


def test_looks_like_cart_needs_caps_and_markers():
    assert looks_like_cart(_w(">> THANK YOU, MADAM CHAIR.", 0))
    assert not looks_like_cart(_w("THANK YOU, MADAM CHAIR.", 0))       # no markers
    assert not looks_like_cart(_w(">> thank you, madam chair.", 0))    # not caps


def test_strip_markers_drops_marker_and_label_and_records_turns():
    words = _w(">> CHAIRPERSON: WELCOME. >>THANK YOU.", 10)
    clean, marks, turns = strip_markers(words)
    assert [w.word for w in clean] == ["WELCOME.", "THANK", "YOU."]
    assert marks == [10.0, 11.5]
    assert turns == [0, 1]


def test_strip_markers_keeps_ordinary_words_after_marker():
    clean, _, _ = strip_markers(_w(">> THAT WOULD BE A SPECIAL ASSESSMENT, SENATOR BUCK.", 0))
    assert clean[0].word == "THAT" and len(clean) == 8


def test_sentence_case_with_turns_and_names():
    words = _w("THANK YOU, MADAM CHAIR. I SUPPORT SENATE BILL 256, SENATOR KOCH. ANY QUESTIONS", 0)
    sentence_case(words, turn_idx=[12], proper_nouns=["Koch"])  # 12 = QUESTIONS
    assert " ".join(w.word for w in words) == (
        "Thank you, madam chair. I support Senate bill 256, Senator Koch. Any Questions")


def test_estimate_lag_finds_negative_offset():
    changes = [10.0 * k for k in range(1, 30)]
    marks = [c - 1.5 for c in changes]          # captions 1.5s early
    assert estimate_lag(marks, changes) == -1.5
    assert estimate_lag(marks[:5], changes) is None  # too few markers to trust


def test_anchor_and_warp_pin_markers_to_speaker_changes():
    words = _w("A B C D", 9.0, step=0.5)        # 9.0-11.0; marker at 10.0 before "C"
    pairs = anchor_pairs([10.0, 20.0], [10.6, 20.6])
    assert pairs == [(10.0, 10.6), (20.0, 20.6)]
    warp(words, pairs)
    assert words[1].end <= 10.6 <= words[2].start


def test_clean_cart_words_end_to_end_splits_turns_correctly():
    # Two speakers; captions run 1.5s early. A marker mid-cue starts speaker B.
    segs, words = [], []
    for k in range(12):
        t = 10.0 * k
        segs.append(_seg("A" if k % 2 == 0 else "B", t, t + 10.0, k))
        words += _w(">> HELLO THERE FRIENDS.", t - 1.5 if k else 0.0, step=0.5)
    clean, lag = clean_cart_words(words, segs, proper_nouns=[])
    assert lag == -1.5
    assert clean[0].word == "Hello"
    # Every turn's words land inside that turn's diarized segment.
    out = align_vtt_to_segments_from_words(clean, segs)
    assert all(s.text == "Hello there friends." for s in out), [s.text for s in out]


def test_real_iga_caption_head_aligns_without_caps_or_markers():
    cues = parse_vtt(FIX / "captions_head.vtt")
    words = _deduplicated_words(cues)
    assert looks_like_cart(words)
    segs = [_seg("S0", 620.0, 700.0, 0)]
    out = align_vtt_to_segments(FIX / "captions_head.vtt", segs)
    text = out[0].text
    assert ">>" not in text and text != text.upper()
    assert "Senate bill 256" in text or "Senate Bill 256" in text

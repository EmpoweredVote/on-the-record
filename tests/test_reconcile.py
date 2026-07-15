# tests/test_reconcile.py
from __future__ import annotations

from dataclasses import dataclass

from src.reconcile import word_overlap_ratio, reconcile_segments


@dataclass
class _Seg:
    segment_id: int
    start_time: float
    end_time: float
    speaker_label: str
    text: str


def _segs():
    return [
        _Seg(0, 0.0, 2.0, "A", "welcome to ask the mare"),   # 'mare' misheard
        _Seg(1, 2.0, 4.0, "B", "glad to be here"),
    ]


def test_word_overlap_ratio_high_and_low():
    assert word_overlap_ratio("welcome to the show", "welcome to the show") == 1.0
    assert word_overlap_ratio("welcome to the show", "utterly different words here") < 0.3


def test_reconcile_applies_corrections_preserving_timing_and_speaker():
    reference = "Welcome to Ask the Mayor.\n\nGlad to be here."
    def fake_llm(prompt):
        # Return corrected text keyed by segment index.
        return '{"0": "Welcome to Ask the Mayor.", "1": "Glad to be here."}'
    segs = _segs()
    out, applied = reconcile_segments(segs, reference, call_llm=fake_llm)
    assert applied is True
    assert out[0].text == "Welcome to Ask the Mayor."
    assert out[0].start_time == 0.0 and out[0].end_time == 2.0
    assert out[0].speaker_label == "A"
    assert out[1].text == "Glad to be here."


def test_reconcile_skipped_when_overlap_too_low():
    reference = "This transcript is about something else entirely, unrelated."
    called = {"n": 0}
    def fake_llm(prompt):
        called["n"] += 1
        return "{}"
    segs = _segs()
    out, applied = reconcile_segments(segs, reference, call_llm=fake_llm, min_overlap=0.5)
    assert applied is False
    assert called["n"] == 0                     # never calls the LLM
    assert out[0].text == "welcome to ask the mare"   # unchanged


def test_reconcile_noop_when_no_reference():
    segs = _segs()
    out, applied = reconcile_segments(segs, "", call_llm=lambda p: "{}")
    assert applied is False
    assert out is segs

# tests/test_reconcile.py
from __future__ import annotations

from dataclasses import dataclass

from src.reconcile import word_overlap_ratio, reconcile_segments
import src.reconcile as reconcile


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


def test_reconcile_isolation_blocks_foreign_only_correction(monkeypatch):
    # One segment per chunk. When segment 0's chunk is processed, the LLM
    # hallucinates a correction for segment 1 (a FOREIGN id not in that chunk).
    # Segment 1's own chunk returns nothing. Without per-chunk isolation, seg0's
    # chunk would corrupt seg1 via the global id map; with isolation, it can't.
    monkeypatch.setattr(reconcile, "_CHUNK_SEGMENTS", 1)
    reference = "Welcome to Ask the Mayor. Glad to be here."

    def llm(prompt):
        # seg0's chunk prompt contains seg0's whisper text; only then hallucinate id 1.
        if "welcome to ask the mare" in prompt:
            return '{"1": "HALLUCINATED BY CHUNK 0"}'
        # seg1's own chunk: no correction.
        return "{}"

    segs = _segs()
    out, applied = reconcile.reconcile_segments(segs, reference, call_llm=llm)
    assert applied is True
    assert out[0].text == "welcome to ask the mare"   # seg0: no id-0 correction returned
    assert out[1].text == "glad to be here"           # seg1: foreign correction from chunk 0 rejected


def test_reconcile_applied_true_even_if_llm_raises():
    reference = "Welcome to Ask the Mayor. Glad to be here."
    def boom(prompt):
        raise RuntimeError("llm down")
    segs = _segs()
    out, applied = reconcile.reconcile_segments(segs, reference, call_llm=boom)
    assert applied is True                              # gate passed
    assert out[0].text == "welcome to ask the mare"     # unchanged on error


def test_reconcile_ignores_malformed_and_nondict_output():
    reference = "Welcome to Ask the Mayor. Glad to be here."
    for bad in ("not json at all", "[1,2,3]", "{bad"):
        segs = _segs()
        out, applied = reconcile.reconcile_segments(segs, reference, call_llm=lambda p, b=bad: b)
        assert applied is True
        assert out[0].text == "welcome to ask the mare"   # unchanged


def test_reconcile_ignores_unknown_segment_id():
    reference = "Welcome to Ask the Mayor. Glad to be here."
    def llm(prompt):
        return '{"999": "ghost", "abc": "bad key"}'
    segs = _segs()
    out, applied = reconcile.reconcile_segments(segs, reference, call_llm=llm)
    assert applied is True
    assert out[0].text == "welcome to ask the mare"
    assert out[1].text == "glad to be here"

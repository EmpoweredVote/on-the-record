"""Unit tests for the meeting confidence gate scoring (src/quality.py)."""
from __future__ import annotations

from src import quality
from src.models import Meeting, Segment, SpeakerMapping


def _seg(seg_id, label, start, end, name=None):
    return Segment(segment_id=seg_id, start_time=start, end_time=end,
                   speaker_label=label, text="x", speaker_name=name)


def _meeting(segments, speakers, event_kind="council"):
    return Meeting(meeting_id="m", city="C", date="2026-01-01",
                   event_kind=event_kind, segments=segments, speakers=speakers)


# --- tier classification ---

def test_classify_trusted_methods():
    for m in ("human_review", "human_confirmed", "voice_profile",
              "roll_call", "self_identification", "chair_recognition"):
        assert quality.classify_method(m) == quality.TIER_TRUSTED


def test_classify_returning_voice_is_probable():
    assert quality.classify_method("voice_profile (returning_2)") == quality.TIER_PROBABLE
    assert quality.classify_method("voice_profile (returning_3+)") == quality.TIER_PROBABLE


def test_classify_unverified_methods():
    for m in ("llm", "name_addressing", "title_context"):
        assert quality.classify_method(m) == quality.TIER_UNVERIFIED


def test_classify_unknown():
    assert quality.classify_method(None) == quality.TIER_UNKNOWN
    assert quality.classify_method("") == quality.TIER_UNKNOWN
    assert quality.classify_method("mystery") == quality.TIER_UNKNOWN


# --- coverage + verdict ---

def test_all_trusted_long_speakers_passes():
    segs = [_seg(0, "S0", 0, 600, "Mayor A"), _seg(1, "S1", 600, 1200, "Member B")]
    speakers = {
        "S0": SpeakerMapping("S0", "Mayor A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", "Member B", 0.95, "roll_call"),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert report["verdict"] == quality.VERDICT_PASS
    assert report["trusted_coverage"] == 1.0


def test_incidental_short_speaker_excluded_from_denominator():
    # Two long trusted council members + one 30s unknown public commenter.
    segs = [
        _seg(0, "S0", 0, 600, "Mayor A"),
        _seg(1, "S1", 600, 1200, "Member B"),
        _seg(2, "S2", 1200, 1230, None),   # 30s < 60s floor -> incidental
    ]
    speakers = {
        "S0": SpeakerMapping("S0", "Mayor A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", "Member B", 0.95, "roll_call"),
        "S2": SpeakerMapping("S2", None, 0.0, None),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    # The unknown 30s speaker is excluded, so coverage stays 1.0 and it passes.
    assert report["trusted_coverage"] == 1.0
    assert report["verdict"] == quality.VERDICT_PASS


def test_long_unknown_speaker_routes_to_review():
    # A long (10m) unidentified principal speaker tanks coverage to ~0.5.
    segs = [_seg(0, "S0", 0, 600, "Mayor A"), _seg(1, "S1", 600, 1200, None)]
    speakers = {
        "S0": SpeakerMapping("S0", "Mayor A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", None, 0.0, None),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert report["verdict"] == quality.VERDICT_REVIEW
    assert abs(report["trusted_coverage"] - 0.5) < 1e-6


def test_probable_counts_at_discount():
    # 50% trusted + 50% probable -> effective = 0.5 + 0.5*0.5 = 0.75 -> review (council high=0.90).
    segs = [_seg(0, "S0", 0, 600, "A"), _seg(1, "S1", 600, 1200, "B")]
    speakers = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", "B", 0.80, "voice_profile (returning_3+)"),
    }
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert abs(report["effective_coverage"] - 0.75) < 1e-6
    assert report["verdict"] == quality.VERDICT_REVIEW


def test_below_low_is_failed():
    segs = [_seg(0, "S0", 0, 1200, None)]
    speakers = {"S0": SpeakerMapping("S0", None, 0.0, None)}
    report = quality.evaluate_meeting(_meeting(segs, speakers))
    assert report["verdict"] == quality.VERDICT_FAILED


def test_no_speech_is_failed():
    report = quality.evaluate_meeting(_meeting([], {}))
    assert report["verdict"] == quality.VERDICT_FAILED
    assert report["total_speech_seconds"] == 0.0


def test_event_kind_threshold_applied():
    # Debate requires high=0.95; 0.90 trusted coverage -> review for debate.
    segs = [_seg(0, "S0", 0, 900, "A"), _seg(1, "S1", 900, 1000, None)]
    speakers = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
        "S1": SpeakerMapping("S1", None, 0.0, None),
    }
    # S1 is 100s (>= 60s floor) so it counts; coverage = 900/1000 = 0.9.
    report = quality.evaluate_meeting(_meeting(segs, speakers, event_kind="debate"))
    assert abs(report["trusted_coverage"] - 0.9) < 1e-6
    assert report["verdict"] == quality.VERDICT_REVIEW


# --- identity key (link-first) ---

def test_identity_key_prefers_politician_slug():
    m = SpeakerMapping("S0", "Mayor John Hamilton", 0.9, "voice_profile",
                       politician_slug="hamilton-john")
    assert quality.identity_key(m) == "essentials:hamilton-john"


def test_identity_key_local_slug_second():
    m = SpeakerMapping("S0", "Jane Doe", 0.9, "human_review", local_slug="jane-doe")
    assert quality.identity_key(m) == "local:jane-doe"


def test_identity_key_normalized_name_fallback():
    a = SpeakerMapping("S0", "Mayor Hamilton", 0.9, "roll_call")
    b = SpeakerMapping("S1", "hamilton", 0.9, "llm")
    assert quality.identity_key(a) == quality.identity_key(b)


def test_identity_key_none_when_unidentified():
    assert quality.identity_key(SpeakerMapping("S0", None, 0.0, None)) is None

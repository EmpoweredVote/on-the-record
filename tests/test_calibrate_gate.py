"""Tests for the calibration harness precision math + non-destructiveness."""
from __future__ import annotations

import json

import numpy as np

import importlib.util
from pathlib import Path

# Load bench/calibrate_gate.py as a module.
_spec = importlib.util.spec_from_file_location(
    "calibrate_gate", Path(__file__).resolve().parent.parent / "bench" / "calibrate_gate.py")
calibrate_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibrate_gate)

from src.models import Meeting, Segment, SpeakerMapping


def _truth():
    segs = [Segment(0, 0, 600, "S0", "x", speaker_name="A"),
            Segment(1, 600, 1200, "S1", "x", speaker_name="B")]
    speakers = {
        "S0": SpeakerMapping("S0", "A", 1.0, "human_confirmed", politician_slug="a"),
        "S1": SpeakerMapping("S1", "B", 1.0, "human_confirmed", politician_slug="b"),
    }
    return Meeting(meeting_id="m", city="C", date="2026-01-01",
                   event_kind="council", segments=segs, speakers=speakers)


def test_precision_perfect_when_auto_matches_truth():
    truth = _truth()
    auto = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile", politician_slug="a"),
        "S1": SpeakerMapping("S1", "B", 0.95, "voice_profile", politician_slug="b"),
    }
    res = calibrate_gate.compare(truth, auto)
    assert res["trusted_claimed_seconds"] == 1200.0
    assert res["trusted_correct_seconds"] == 1200.0
    assert res["trusted_precision"] == 1.0


def test_precision_drops_on_false_positive_voice_match():
    truth = _truth()
    # S1 is voice-matched to the WRONG person at the lowered returning threshold.
    auto = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile", politician_slug="a"),
        "S1": SpeakerMapping("S1", "C", 0.72, "voice_profile (returning_3+)", politician_slug="c"),
    }
    res = calibrate_gate.compare(truth, auto)
    # 600s of S0 correct out of 1200s claimed (S1 wrong) -> 0.5 precision.
    assert res["trusted_correct_seconds"] == 600.0
    assert res["trusted_precision"] == 0.5


def test_unidentified_auto_not_counted_as_claim():
    truth = _truth()
    auto = {
        "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile", politician_slug="a"),
        "S1": SpeakerMapping("S1", None, 0.0, None),
    }
    res = calibrate_gate.compare(truth, auto)
    # Only S0 was claimed; precision over claims is 100%, coverage is partial.
    assert res["trusted_claimed_seconds"] == 600.0
    assert res["trusted_precision"] == 1.0


def test_same_person_matches_despite_link_drift():
    # Truth was reviewed before identity-linking existed: bare name, no slug.
    # The current automated re-run links the same person to a slug. Same person.
    truth = SpeakerMapping("S0", "Steve Hilton", 1.0, "human_confirmed")
    auto = SpeakerMapping("S0", "Steve Hilton", 0.9, "voice_profile",
                          politician_slug="hilton-steve")
    assert calibrate_gate._same_identity(auto, truth) is True


def test_different_slugs_do_not_match():
    truth = SpeakerMapping("S0", "Jane Smith", 1.0, "human_confirmed",
                           politician_slug="smith-jane")
    auto = SpeakerMapping("S0", "Steve Hilton", 0.9, "voice_profile",
                          politician_slug="hilton-steve")
    assert calibrate_gate._same_identity(auto, truth) is False


def test_link_drift_counts_as_correct_in_compare():
    # Truth has names only (pre-linking review); auto links to slugs. All correct.
    segs = [Segment(0, 0, 600, "S0", "x", speaker_name="Steve Hilton")]
    speakers = {"S0": SpeakerMapping("S0", "Steve Hilton", 1.0, "human_confirmed")}
    truth = Meeting(meeting_id="m", city="C", date="2026-01-01",
                    event_kind="council", segments=segs, speakers=speakers)
    auto = {"S0": SpeakerMapping("S0", "Steve Hilton", 0.9, "voice_profile",
                                 politician_slug="hilton-steve")}
    res = calibrate_gate.compare(truth, auto)
    assert res["trusted_precision"] == 1.0

"""Tests for the Stage-4 gate helper in run_local.py."""
from __future__ import annotations

import json

import run_local
from src.checkpoint import PipelineState
from src.models import Meeting, Segment, SpeakerMapping


def _meeting(verdict_kind):
    if verdict_kind == "pass":
        segs = [Segment(0, 0, 600, "S0", "x", speaker_name="A"),
                Segment(1, 600, 1200, "S1", "x", speaker_name="B")]
        speakers = {
            "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
            "S1": SpeakerMapping("S1", "B", 0.95, "roll_call"),
        }
    else:  # review/failed: one long unknown
        segs = [Segment(0, 0, 600, "S0", "x", speaker_name="A"),
                Segment(1, 600, 1200, "S1", "x", speaker_name=None)]
        speakers = {
            "S0": SpeakerMapping("S0", "A", 0.99, "voice_profile"),
            "S1": SpeakerMapping("S1", None, 0.0, None),
        }
    return Meeting(meeting_id="m", city="C", date="2026-01-01",
                   event_kind="council", segments=segs, speakers=speakers)


def test_apply_gate_writes_quality_json_and_state(tmp_path):
    meeting = _meeting("pass")
    state = PipelineState(tmp_path)
    report = run_local._apply_gate(meeting, tmp_path, state)

    assert report["verdict"] == "pass"
    quality_file = tmp_path / "quality.json"
    assert quality_file.exists()
    on_disk = json.loads(quality_file.read_text())
    assert on_disk["verdict"] == "pass"

    reloaded = PipelineState(tmp_path)
    assert reloaded.review_status == "pass"
    assert reloaded.trusted_coverage == report["trusted_coverage"]


def test_apply_gate_records_review_verdict(tmp_path):
    meeting = _meeting("review")
    state = PipelineState(tmp_path)
    report = run_local._apply_gate(meeting, tmp_path, state)
    assert report["verdict"] == "review"
    assert PipelineState(tmp_path).review_status == "review"


def test_may_publish_only_on_pass():
    assert run_local._may_publish("pass", False) is True
    assert run_local._may_publish("review", False) is False
    assert run_local._may_publish("failed", False) is False
    assert run_local._may_publish(None, False) is False


def test_may_publish_override():
    assert run_local._may_publish("review", True) is True
    assert run_local._may_publish("failed", True) is True
    assert run_local._may_publish(None, True) is True

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


def test_review_meeting_recomputes_gate(tmp_path, monkeypatch):
    # After --review, the persisted verdict must reflect the meeting's current
    # attributions so a direct --publish-meeting isn't blocked by a stale status.
    from src import config

    meetings = tmp_path / "meetings"
    profiles = tmp_path / "profiles"
    meetings.mkdir()
    profiles.mkdir()
    monkeypatch.setattr(config, "MEETINGS_DIR", meetings)
    monkeypatch.setattr(config, "PROFILES_DIR", profiles)

    mdir = meetings / "2026-01-01-x"
    mdir.mkdir()
    meeting = _meeting("review")
    meeting.meeting_id = "2026-01-01-x"
    (mdir / "transcript_named.json").write_text(json.dumps(meeting.to_dict()))

    # Skip the interactive review loop (no TTY in tests); no changes made.
    monkeypatch.setattr(run_local, "_interactive_speaker_review", lambda *a, **k: [])

    run_local._review_meeting("2026-01-01-x")

    assert (mdir / "quality.json").exists()
    assert PipelineState(mdir).review_status == "review"

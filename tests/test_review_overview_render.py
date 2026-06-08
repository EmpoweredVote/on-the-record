"""The overview tables render from build_review_state views without error."""
from __future__ import annotations

import json
import numpy as np
import run_local
from src.models import Meeting, Segment, SpeakerMapping


def _seg(label, start, end, text="x"):
    return Segment(segment_id=0, start_time=start, end_time=end, speaker_label=label, text=text)


def _make_named_meeting(mdir, mid):
    segments = [_seg("SPEAKER_00", 0, 30, "hello"), _seg("SPEAKER_01", 30, 50, "world")]
    m0 = SpeakerMapping(speaker_label="SPEAKER_00"); m0.speaker_name = "Mayor"; m0.confidence = 1.0
    m1 = SpeakerMapping(speaker_label="SPEAKER_01")
    meeting = Meeting(meeting_id=mid, city="Bloomington", date="2026-02-10", meeting_type="Regular")
    meeting.segments = segments
    meeting.speakers = {"SPEAKER_00": m0, "SPEAKER_01": m1}
    (mdir / "transcript_named.json").write_text(json.dumps(meeting.to_dict()), encoding="utf-8")
    (mdir / "diarization.json").write_text(json.dumps([s.to_dict() for s in segments]), encoding="utf-8")
    (mdir / "embeddings.json").write_text(json.dumps({"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]}), encoding="utf-8")
    (mdir / "audio.wav").write_bytes(b"")


def test_review_meeting_overview_renders(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "m-rev"
    mdir = tmp_path / mid; mdir.mkdir(parents=True)
    _make_named_meeting(mdir, mid)
    monkeypatch.setattr(run_local, "_interactive_speaker_review", lambda *a, **k: [])
    run_local._review_meeting(mid)
    out = capsys.readouterr().out
    assert "Current Name" in out and "Method" in out
    assert "SPEAKER_00" in out and "Mayor" in out
    assert "Speakers: 2" in out


def test_identify_speakers_overview_renders(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "m-id"
    mdir = tmp_path / mid; mdir.mkdir(parents=True)
    _make_named_meeting(mdir, mid)
    monkeypatch.setattr(run_local, "_interactive_speaker_review", lambda *a, **k: [])
    run_local._identify_speakers_standalone(mid)
    out = capsys.readouterr().out
    assert "Voice Hint" in out
    assert "SPEAKER_00" in out and "Mayor" in out
    assert "Speakers: 2" in out

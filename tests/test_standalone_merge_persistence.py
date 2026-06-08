"""A merge via _identify_speakers_standalone must keep transcript_named.json,
embeddings.json, and diarization.json consistent (no orphaned source label)."""
from __future__ import annotations

import json

import numpy as np

import run_local
from src import review
from src.models import Meeting, Segment, SpeakerMapping


def _seg(label, start, end, text="x"):
    return Segment(segment_id=0, start_time=start, end_time=end, speaker_label=label, text=text)


def test_standalone_merge_keeps_files_consistent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "m-merge"
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)

    segments = [_seg("SPEAKER_00", 0, 10, "hello"), _seg("SPEAKER_01", 10, 40, "world")]
    m0 = SpeakerMapping(speaker_label="SPEAKER_00"); m0.speaker_name = "Mayor"; m0.confidence = 1.0
    m1 = SpeakerMapping(speaker_label="SPEAKER_01"); m1.speaker_name = "Mystery Person"; m1.confidence = 1.0
    meeting = Meeting(meeting_id=mid, city="Bloomington", date="2026-02-10", meeting_type="Regular")
    meeting.segments = segments
    meeting.speakers = {"SPEAKER_00": m0, "SPEAKER_01": m1}

    (mdir / "transcript_named.json").write_text(json.dumps(meeting.to_dict()), encoding="utf-8")
    (mdir / "diarization.json").write_text(json.dumps([s.to_dict() for s in segments]), encoding="utf-8")
    (mdir / "embeddings.json").write_text(json.dumps({"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]}), encoding="utf-8")
    (mdir / "audio.wav").write_bytes(b"")  # presence only

    # Stub the interactive loop to merge SPEAKER_01 into SPEAKER_00.
    def fake_loop(segs, mappings, embeddings, profile_db, video, audio, **kw):
        review.merge_speakers(segs, embeddings, mappings, "SPEAKER_01", "SPEAKER_00")
        return [{"label": "SPEAKER_01", "merged_into": "SPEAKER_00"}]

    monkeypatch.setattr(run_local, "_interactive_speaker_review", fake_loop)

    run_local._identify_speakers_standalone(mid)

    named = json.loads((mdir / "transcript_named.json").read_text())
    emb = json.loads((mdir / "embeddings.json").read_text())

    # embeddings merged
    assert set(emb.keys()) == {"SPEAKER_00"}
    # transcript speakers no longer contains the merged-away source
    assert "SPEAKER_01" not in named["speakers"]
    # no segment is still labeled the source / carries the stale name
    seg_labels = {s["speaker_label"] for s in named["segments"]}
    assert "SPEAKER_01" not in seg_labels
    assert all(s.get("speaker_name") != "Mystery Person" for s in named["segments"])

import json
import os
from datetime import datetime

import pytest

from src.models import (
    Meeting,
    MeetingSummary,
    ProcessingMetadata,
    Segment,
    SpeakerMapping,
)
from src.repair import RepairError, repair_transcript


def _write_minimal_meeting(meeting_dir, *, summary):
    exports_dir = meeting_dir / "exports"
    exports_dir.mkdir(parents=True)
    (meeting_dir / "pipeline_state.json").write_text(
        '{"completed_stage": 7}',
        encoding="utf-8",
    )
    (meeting_dir / "diarization.json").write_text(
        json.dumps([Segment(0, 10.0, 20.0, "SPEAKER_00").to_dict()]),
        encoding="utf-8",
    )
    (meeting_dir / "captions.vtt").write_text(
        """WEBVTT

00:00:10.000 --> 00:00:15.000
Repaired caption text
""",
        encoding="utf-8",
    )
    meeting = Meeting(
        meeting_id=meeting_dir.name,
        city="Los Angeles",
        date="2026-06-13",
        segments=[
            Segment(
                0,
                10.0,
                20.0,
                "SPEAKER_00",
                text="OLD NAMED",
                speaker_name="Nithya Raman",
            )
        ],
        speakers={
            "SPEAKER_00": SpeakerMapping(
                speaker_label="SPEAKER_00",
                speaker_name="Nithya Raman",
                confidence=0.98,
                id_method="voice_profile",
            )
        },
        summary=summary,
    )
    (meeting_dir / "transcript_named.json").write_text(
        json.dumps(meeting.to_dict()),
        encoding="utf-8",
    )
    return exports_dir


def test_repair_transcript_rebuilds_from_captions_and_backs_up_live_files(tmp_path):
    meeting_dir = tmp_path / "2026-06-13-regular-session"
    exports_dir = meeting_dir / "exports"
    exports_dir.mkdir(parents=True)

    pipeline_state = b'{\n  "completed_stage": 7,\n  "body_slug": "la-city-council"\n}\n'
    (meeting_dir / "pipeline_state.json").write_bytes(pipeline_state)

    diarization = [
        Segment(0, 10.0, 18.0, "SPEAKER_00"),
        Segment(1, 17.0, 21.0, "SPEAKER_01"),
    ]
    (meeting_dir / "diarization.json").write_text(
        json.dumps([segment.to_dict() for segment in diarization], indent=2),
        encoding="utf-8",
    )
    (meeting_dir / "captions.vtt").write_text(
        """WEBVTT

00:00:10.000 --> 00:00:14.000
only 13 homes are completed and ready

00:00:12.000 --> 00:00:16.000
only 13 homes are completed and ready for move-in

00:00:15.000 --> 00:00:18.000
for move-in Do you have a permit

00:00:17.000 --> 00:00:20.000
Do you have a permit No
""",
        encoding="utf-8",
    )

    speakers = {
        "SPEAKER_00": SpeakerMapping(
            speaker_label="SPEAKER_00",
            speaker_name="Nithya Raman",
            confidence=0.98,
            id_method="voice_profile",
            politician_slug="nithya-raman",
            politician_id="politician-raman",
        ),
        "SPEAKER_01": SpeakerMapping(
            speaker_label="SPEAKER_01",
            speaker_name="Peter Branch",
            confidence=0.87,
            id_method="manual_review",
        ),
    }
    original_meeting = Meeting(
        meeting_id="2026-06-13-regular-session",
        city="Los Angeles",
        date="2026-06-13",
        meeting_type="Regular Session",
        audio_source="https://example.com/council-video",
        duration_seconds=3600.0,
        segments=[
            Segment(
                0,
                0.0,
                1.0,
                "SPEAKER_00",
                text="OLD NAMED TRANSCRIPT",
                speaker_name="Nithya Raman",
                confidence=0.98,
                id_method="voice_profile",
            )
        ],
        speakers=speakers,
        summary=MeetingSummary(
            executive_summary="Preserved summary.",
            key_decisions=["Preserved decision."],
            model="summary-model",
            generated_at="2026-06-13T12:00:00",
        ),
        processing_metadata=ProcessingMetadata(
            pipeline_version="2.3.4",
            diarization_model="diarization-model",
            transcription_model="caption-backed",
            gpu_used=True,
            processing_time_seconds=42.5,
        ),
    )
    old_named = json.dumps(original_meeting.to_dict(), indent=2)
    (meeting_dir / "transcript_named.json").write_text(
        old_named, encoding="utf-8"
    )

    old_raw = json.dumps(
        [Segment(0, 0.0, 1.0, "SPEAKER_00", text="OLD RAW TRANSCRIPT").to_dict()],
        indent=2,
    )
    (meeting_dir / "transcript_raw.json").write_text(old_raw, encoding="utf-8")

    old_exports = {
        "transcript.md": "OLD MARKDOWN EXPORT",
        "transcript.json": "OLD JSON EXPORT",
        "subtitles.srt": "OLD SRT EXPORT",
        "summary.md": "OLD SUMMARY EXPORT",
    }
    for filename, content in old_exports.items():
        (exports_dir / filename).write_text(content, encoding="utf-8")

    result = repair_transcript(
        meeting_dir,
        now=datetime(2026, 6, 13, 13, 45, 0),
    )

    assert result.meeting_id == "2026-06-13-regular-session"
    assert result.segment_count == 2
    assert result.backup_dir == (
        meeting_dir / "backups" / "transcript-repair-20260613-134500"
    )

    repaired_raw = json.loads(
        (meeting_dir / "transcript_raw.json").read_text(encoding="utf-8")
    )
    assert [segment["text"] for segment in repaired_raw] == [
        "only 13 homes are completed and ready for move-in Do you have a permit",
        "No",
    ]
    assert repaired_raw[1]["start_time"] == 18.0
    assert all("speaker_name" not in segment for segment in repaired_raw)

    repaired_named = json.loads(
        (meeting_dir / "transcript_named.json").read_text(encoding="utf-8")
    )
    assert [
        (
            segment["speaker_name"],
            segment["confidence"],
            segment["id_method"],
        )
        for segment in repaired_named["segments"]
    ] == [
        ("Nithya Raman", 0.98, "voice_profile"),
        ("Peter Branch", 0.87, "manual_review"),
    ]
    assert repaired_named["speakers"]["SPEAKER_00"]["politician_slug"] == (
        "nithya-raman"
    )
    assert repaired_named["speakers"]["SPEAKER_00"]["politician_id"] == (
        "politician-raman"
    )
    assert repaired_named["summary"] == original_meeting.summary.to_dict()
    assert repaired_named["processing_metadata"] == (
        original_meeting.processing_metadata.to_dict()
    )

    assert (meeting_dir / "pipeline_state.json").read_bytes() == pipeline_state
    assert "Nithya Raman" in result.exports["markdown"].read_text(encoding="utf-8")
    assert "[Peter Branch] No" in result.exports["srt"].read_text(encoding="utf-8")

    backup_dir = result.backup_dir
    assert (backup_dir / "transcript_raw.json").read_text(encoding="utf-8") == old_raw
    assert (
        backup_dir / "transcript_named.json"
    ).read_text(encoding="utf-8") == old_named
    for filename, content in old_exports.items():
        assert (backup_dir / "exports" / filename).read_text(
            encoding="utf-8"
        ) == content


def test_install_failure_rolls_back_every_changed_live_artifact(
    tmp_path, monkeypatch
):
    meeting_dir = tmp_path / "rollback-meeting"
    exports_dir = _write_minimal_meeting(
        meeting_dir,
        summary=MeetingSummary(executive_summary="Preserved summary."),
    )
    tracked_paths = [
        meeting_dir / "transcript_raw.json",
        meeting_dir / "transcript_named.json",
        exports_dir / "transcript.md",
        exports_dir / "transcript.json",
        exports_dir / "subtitles.srt",
        exports_dir / "summary.md",
    ]
    original_bytes = {}
    for path in tracked_paths:
        if path.name == "transcript.md":
            continue
        if path.name == "transcript_named.json":
            original_bytes[path] = path.read_bytes()
        else:
            original_bytes[path] = f"ORIGINAL {path.name}".encode()
    for path, content in original_bytes.items():
        path.write_bytes(content)

    real_replace = os.replace
    install_failure = OSError("simulated install failure")

    def fail_during_install(source, destination):
        if (
            destination == exports_dir / "transcript.json"
            and ".transcript-repair-" in str(source)
        ):
            raise install_failure
        real_replace(source, destination)

    monkeypatch.setattr("src.repair.os.replace", fail_during_install)

    with pytest.raises(RepairError, match="simulated install failure"):
        repair_transcript(
            meeting_dir,
            now=datetime(2026, 6, 13, 14, 0, 0),
        )

    for path, content in original_bytes.items():
        assert path.read_bytes() == content
    assert not (exports_dir / "transcript.md").exists()


def test_repair_without_summary_removes_stale_summary_after_backing_it_up(tmp_path):
    meeting_dir = tmp_path / "no-summary-meeting"
    exports_dir = _write_minimal_meeting(meeting_dir, summary=None)
    old_summary = b"OLD STALE SUMMARY"
    (exports_dir / "summary.md").write_bytes(old_summary)

    result = repair_transcript(
        meeting_dir,
        now=datetime(2026, 6, 13, 14, 5, 0),
    )

    assert not (exports_dir / "summary.md").exists()
    assert (result.backup_dir / "exports" / "summary.md").read_bytes() == old_summary

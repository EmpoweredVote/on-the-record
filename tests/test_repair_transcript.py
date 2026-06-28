import json
import os
from datetime import datetime

import pytest

from src.export import export_all as real_export_all
from src.models import (
    Meeting,
    MeetingSummary,
    ProcessingMetadata,
    Segment,
    SpeakerMapping,
)
from src.repair import RepairError, repair_transcript


_LIVE_ARTIFACTS = (
    "transcript_raw.json",
    "transcript_named.json",
    "exports/transcript.md",
    "exports/transcript.json",
    "exports/subtitles.srt",
    "exports/summary.md",
)


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


def _write_and_capture_live_artifacts(meeting_dir):
    original_bytes = {}
    for relative_path in _LIVE_ARTIFACTS:
        path = meeting_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative_path == "transcript_named.json":
            content = path.read_bytes()
        else:
            content = f"ORIGINAL {relative_path}".encode()
            path.write_bytes(content)
        original_bytes[path] = content
    return original_bytes


def _assert_live_artifacts_unchanged(original_bytes):
    for path, content in original_bytes.items():
        assert path.read_bytes() == content


def _assert_no_repair_residue(meeting_dir):
    assert not (meeting_dir / "backups").exists()
    assert not list(meeting_dir.glob(".transcript-repair-*"))


@pytest.mark.parametrize(
    "missing_filename",
    [
        "pipeline_state.json",
        "diarization.json",
        "captions.vtt",
        "transcript_named.json",
    ],
)
def test_repair_requires_every_input_file(tmp_path, missing_filename):
    meeting_dir = tmp_path / "missing-input-meeting"
    _write_minimal_meeting(meeting_dir, summary=None)
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)
    (meeting_dir / missing_filename).unlink()
    original_bytes.pop(meeting_dir / missing_filename, None)

    with pytest.raises(RepairError, match=missing_filename):
        repair_transcript(meeting_dir)

    _assert_live_artifacts_unchanged(original_bytes)
    _assert_no_repair_residue(meeting_dir)


def test_invalid_named_json_fails_before_backup_or_live_changes(tmp_path):
    meeting_dir = tmp_path / "invalid-named-meeting"
    _write_minimal_meeting(meeting_dir, summary=None)
    original_raw = b"ORIGINAL RAW TRANSCRIPT"
    (meeting_dir / "transcript_raw.json").write_bytes(original_raw)
    (meeting_dir / "transcript_named.json").write_text(
        "{not valid JSON",
        encoding="utf-8",
    )

    with pytest.raises(RepairError, match="valid JSON"):
        repair_transcript(meeting_dir)

    assert (meeting_dir / "transcript_raw.json").read_bytes() == original_raw
    assert not (meeting_dir / "backups").exists()


def test_invalid_pipeline_state_json_fails_before_backup_or_live_changes(tmp_path):
    meeting_dir = tmp_path / "invalid-pipeline-state-meeting"
    _write_minimal_meeting(meeting_dir, summary=None)
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)
    (meeting_dir / "pipeline_state.json").write_text(
        "{not valid JSON",
        encoding="utf-8",
    )

    with pytest.raises(RepairError, match="valid JSON"):
        repair_transcript(meeting_dir)

    _assert_live_artifacts_unchanged(original_bytes)
    assert not (meeting_dir / "backups").exists()


def test_caption_file_without_usable_cues_fails_before_backup_or_live_changes(
    tmp_path,
):
    meeting_dir = tmp_path / "empty-captions-meeting"
    _write_minimal_meeting(meeting_dir, summary=None)
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)
    (meeting_dir / "captions.vtt").write_text("WEBVTT\n", encoding="utf-8")

    with pytest.raises(RepairError, match="no usable cues"):
        repair_transcript(meeting_dir)

    _assert_live_artifacts_unchanged(original_bytes)
    assert not (meeting_dir / "backups").exists()


def test_export_failure_fails_before_backup_or_live_changes(tmp_path, monkeypatch):
    meeting_dir = tmp_path / "export-failure-meeting"
    _write_minimal_meeting(
        meeting_dir,
        summary=MeetingSummary(executive_summary="Preserved summary."),
    )
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)

    def fail_export(*args, **kwargs):
        raise TypeError("simulated serialization failure")

    monkeypatch.setattr("src.repair.export_all", fail_export)

    with pytest.raises(
        RepairError,
        match="Transcript repair failed: simulated serialization failure",
    ) as exc_info:
        repair_transcript(meeting_dir)

    assert "Could not install" not in str(exc_info.value)
    _assert_live_artifacts_unchanged(original_bytes)
    _assert_no_repair_residue(meeting_dir)


def test_backup_copy_failure_cleans_new_backup_without_touching_live_files(
    tmp_path, monkeypatch
):
    meeting_dir = tmp_path / "backup-failure-meeting"
    _write_minimal_meeting(
        meeting_dir,
        summary=MeetingSummary(executive_summary="Preserved summary."),
    )
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)
    older_backup = meeting_dir / "backups" / "transcript-repair-20260612-120000"
    older_backup.mkdir(parents=True)
    sentinel = older_backup / "sentinel.txt"
    sentinel.write_bytes(b"KEEP THIS BACKUP")
    now = datetime(2026, 6, 13, 15, 0, 0)
    failed_backup = meeting_dir / "backups" / "transcript-repair-20260613-150000"

    def fail_copy(*args, **kwargs):
        raise OSError("simulated backup failure")

    monkeypatch.setattr("src.repair.shutil.copy2", fail_copy)

    with pytest.raises(RepairError, match="Could not create repair backup"):
        repair_transcript(meeting_dir, now=now)

    _assert_live_artifacts_unchanged(original_bytes)
    assert not failed_backup.exists()
    assert sentinel.read_bytes() == b"KEEP THIS BACKUP"


def test_existing_backup_timestamp_collision_is_not_modified(tmp_path):
    meeting_dir = tmp_path / "backup-collision-meeting"
    _write_minimal_meeting(
        meeting_dir,
        summary=MeetingSummary(executive_summary="Preserved summary."),
    )
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)
    now = datetime(2026, 6, 13, 15, 30, 0)
    existing_backup = (
        meeting_dir / "backups" / "transcript-repair-20260613-153000"
    )
    existing_backup.mkdir(parents=True)
    sentinel = existing_backup / "sentinel.txt"
    sentinel.write_bytes(b"DO NOT REPLACE")

    with pytest.raises(RepairError):
        repair_transcript(meeting_dir, now=now)

    _assert_live_artifacts_unchanged(original_bytes)
    assert sentinel.read_bytes() == b"DO NOT REPLACE"
    assert sorted(path.name for path in existing_backup.iterdir()) == [
        "sentinel.txt"
    ]


def test_dynamic_export_is_backed_up_and_restored_after_later_install_failure(
    tmp_path, monkeypatch
):
    meeting_dir = tmp_path / "dynamic-export-rollback-meeting"
    exports_dir = _write_minimal_meeting(
        meeting_dir,
        summary=MeetingSummary(executive_summary="Preserved summary."),
    )
    original_bytes = _write_and_capture_live_artifacts(meeting_dir)
    live_extra = exports_dir / "extra.txt"
    old_extra = b"ORIGINAL EXTRA EXPORT"
    live_extra.write_bytes(old_extra)
    now = datetime(2026, 6, 13, 15, 45, 0)
    backup_dir = meeting_dir / "backups" / "transcript-repair-20260613-154500"

    def export_with_extra(meeting, export_dir):
        exports = real_export_all(meeting, export_dir)
        staged_extra = export_dir / "extra.txt"
        staged_extra.write_bytes(b"REPAIRED EXTRA EXPORT")
        return {"extra": staged_extra, **exports}

    real_replace = os.replace

    def fail_after_extra_install(source, destination):
        if (
            destination == exports_dir / "transcript.json"
            and ".transcript-repair-" in str(source)
        ):
            raise OSError("simulated later install failure")
        real_replace(source, destination)

    monkeypatch.setattr("src.repair.export_all", export_with_extra)
    monkeypatch.setattr("src.repair.os.replace", fail_after_extra_install)

    with pytest.raises(RepairError, match="simulated later install failure"):
        repair_transcript(meeting_dir, now=now)

    _assert_live_artifacts_unchanged(original_bytes)
    assert live_extra.read_bytes() == old_extra
    assert (backup_dir / "exports" / "extra.txt").read_bytes() == old_extra


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
            highlights=["Preserved decision."],
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


def test_repair_rebases_full_source_captions_for_clip_meetings(tmp_path):
    """A clip meeting stores full-source captions.vtt but clip-local diarization.
    repair_transcript must rebase cue times by clip_start_seconds (the same
    clip_offset the live pipeline applies); otherwise in-window text aligns to
    no segment and the repair drops every word."""
    meeting_dir = tmp_path / "2026-04-14-clip"
    (meeting_dir / "exports").mkdir(parents=True)
    (meeting_dir / "pipeline_state.json").write_text(
        '{"completed_stage": 7}', encoding="utf-8"
    )
    # Clip starts at source second 100. Diarization is clip-local [0, 20].
    (meeting_dir / "diarization.json").write_text(
        json.dumps([Segment(0, 0.0, 20.0, "SPEAKER_00").to_dict()]),
        encoding="utf-8",
    )
    # Full-source captions: cue at 105s -> clip-local 5s (inside the window);
    # cue at 10s -> clip-local -90s (before the window, must drop).
    (meeting_dir / "captions.vtt").write_text(
        """WEBVTT

00:00:10.000 --> 00:00:14.000
before the window noise

00:01:45.000 --> 00:01:50.000
inside the interview window
""",
        encoding="utf-8",
    )
    meeting = Meeting(
        meeting_id="2026-04-14-clip",
        city=None,
        date="2026-04-14",
        segments=[Segment(0, 0.0, 20.0, "SPEAKER_00", text="OLD", speaker_name="Nithya Raman")],
        speakers={
            "SPEAKER_00": SpeakerMapping(
                speaker_label="SPEAKER_00",
                speaker_name="Nithya Raman",
                confidence=0.98,
                id_method="voice_profile",
            )
        },
        clip_start_seconds=100.0,
        clip_end_seconds=2194.0,
    )
    (meeting_dir / "transcript_named.json").write_text(
        json.dumps(meeting.to_dict()), encoding="utf-8"
    )

    repair_transcript(meeting_dir, now=datetime(2026, 4, 14, 12, 0, 0))

    repaired = json.loads(
        (meeting_dir / "transcript_named.json").read_text(encoding="utf-8")
    )
    texts = " ".join(seg["text"] for seg in repaired["segments"])
    assert "inside the interview window" in texts
    assert "before the window noise" not in texts
    # Curated speaker identity is preserved across the repair.
    assert repaired["segments"][0]["speaker_name"] == "Nithya Raman"


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


def test_keyboard_interrupt_rolls_back_and_is_reraised(tmp_path, monkeypatch):
    meeting_dir = tmp_path / "interrupted-meeting"
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
    interrupt = KeyboardInterrupt("simulated interrupt")

    def interrupt_during_install(source, destination):
        if (
            destination == exports_dir / "transcript.json"
            and ".transcript-repair-" in str(source)
        ):
            raise interrupt
        real_replace(source, destination)

    monkeypatch.setattr("src.repair.os.replace", interrupt_during_install)

    with pytest.raises(KeyboardInterrupt) as exc_info:
        repair_transcript(
            meeting_dir,
            now=datetime(2026, 6, 13, 14, 10, 0),
        )

    assert exc_info.value is interrupt
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

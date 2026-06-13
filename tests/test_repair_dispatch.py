"""Dispatch and output behavior for standalone transcript repair."""

from __future__ import annotations

import sys

import pytest

import run_local
from src.repair import RepairError, RepairResult


def test_repair_transcript_dispatches_without_running_pipeline(monkeypatch):
    called = {}
    monkeypatch.setattr(
        run_local,
        "_repair_transcript_standalone",
        lambda meeting_id: called.setdefault("meeting_id", meeting_id),
        raising=False,
    )
    monkeypatch.setattr(
        run_local,
        "run_pipeline",
        lambda args: pytest.fail("run_pipeline must not run"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_local.py", "--repair-transcript", "meeting-1"],
    )

    run_local.main()

    assert called == {"meeting_id": "meeting-1"}


@pytest.mark.parametrize(
    ("conflicting_args", "conflict_flags"),
    [
        (["--input", "meeting.mp4"], ["--input"]),
        (["--browse-catstv"], ["--browse-catstv"]),
        (["--resume", "meeting-1"], ["--resume"]),
        (
            ["--redo", "transcribe", "--resume", "meeting-1"],
            ["--redo", "--resume"],
        ),
        (["--batch", "meetings.txt"], ["--batch"]),
        (["--review", "meeting-1"], ["--review"]),
        (["--review-meeting", "meeting-1"], ["--review-meeting"]),
        (["--identify-speakers", "meeting-1"], ["--identify-speakers"]),
    ],
)
def test_repair_transcript_rejects_conflicting_commands(
    monkeypatch,
    capsys,
    conflicting_args,
    conflict_flags,
):
    called = []
    monkeypatch.setattr(
        run_local,
        "_repair_transcript_standalone",
        called.append,
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_local.py", "--repair-transcript", "repair-me"] + conflicting_args,
    )

    with pytest.raises(SystemExit) as exc_info:
        run_local.main()

    assert exc_info.value.code == 2
    assert called == []
    error = capsys.readouterr().err
    assert "--repair-transcript" in error
    for flag in conflict_flags:
        assert flag in error


def test_repair_transcript_handler_prints_result(monkeypatch, tmp_path, capsys):
    meeting_id = "meeting-1"
    backup_dir = tmp_path / meeting_id / "backups" / "transcript-repair-fixed"
    exports = {
        "markdown": tmp_path / meeting_id / "exports" / "transcript.md",
        "json": tmp_path / meeting_id / "exports" / "transcript.json",
        "srt": tmp_path / meeting_id / "exports" / "transcript.srt",
    }
    called = []

    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)

    def fake_repair_transcript(meeting_dir):
        called.append(meeting_dir)
        return RepairResult(
            meeting_id=meeting_id,
            segment_count=7,
            backup_dir=backup_dir,
            exports=exports,
        )

    monkeypatch.setattr("src.repair.repair_transcript", fake_repair_transcript)

    run_local._repair_transcript_standalone(meeting_id)

    assert called == [tmp_path / meeting_id]
    output = capsys.readouterr().out
    assert meeting_id in output
    assert "7" in output
    assert str(backup_dir) in output
    for export_name, export_path in exports.items():
        assert export_name in output
        assert str(export_path) in output


def test_repair_transcript_handler_exits_on_repair_error(
    monkeypatch,
    capsys,
):
    def fail_repair(meeting_dir):
        raise RepairError("captions are unavailable")

    monkeypatch.setattr("src.repair.repair_transcript", fail_repair)

    with pytest.raises(SystemExit) as exc_info:
        run_local._repair_transcript_standalone("meeting-1")

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "Transcript repair failed:" in output
    assert "captions are unavailable" in output

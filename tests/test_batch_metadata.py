"""Batch parsing leaves metadata unset (no hardcoded Bloomington); batch
resolution is non-interactive and hard-fails under-specified entries."""
from __future__ import annotations

import argparse
import pytest
import run_local


def test_parse_batch_dir_does_not_hardcode_city(tmp_path):
    (tmp_path / "2026-05-01-something.mp4").write_bytes(b"x")
    entries = run_local._parse_batch_inputs(str(tmp_path))
    assert len(entries) == 1
    e = entries[0]
    assert e["date"] == "2026-05-01"
    assert e["city"] is None
    assert e["meeting_type"] is None


def test_parse_batch_textfile_omitted_fields_are_none(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("/videos/a.mp4 2026-05-01\n")  # only path + date
    entries = run_local._parse_batch_inputs(str(f))
    assert entries[0]["city"] is None
    assert entries[0]["meeting_type"] is None


def test_parse_batch_textfile_keeps_supplied_fields(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("/videos/a.mp4 2026-05-01 Bloomington Special\n")
    e = run_local._parse_batch_inputs(str(f))[0]
    assert e["city"] == "Bloomington"
    assert e["meeting_type"] == "Special"


def test_batch_underspecified_entry_resolution_raises(monkeypatch):
    # batch_mode forces non-interactive even on a TTY -> ValueError, which
    # _run_batch records per-entry as a failure (sibling entries continue).
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    args = argparse.Namespace(
        city=None, date="2026-05-01", meeting_type=None, title=None,
        event_kind=None, default=False, batch_mode=True,
    )
    with pytest.raises(ValueError):
        run_local._resolve_metadata(args)


def test_batch_resume_skip_uses_resolved_meeting_type(monkeypatch, tmp_path):
    # --batch-resume should short-circuit an already-complete meeting even when
    # the batch entry omits meeting_type and relies on --default to fill it.
    # The skip precheck must read the RESOLVED meeting_type, not the raw entry.
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: pytest.fail("complete meeting must be skipped"))

    batchfile = tmp_path / "list.txt"
    batchfile.write_text("/videos/a.mp4 2026-05-01\n")  # path + date only

    # Pre-create the already-complete meeting under its resolved id.
    mid = "2026-05-01-regular-session"
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)
    (mdir / "pipeline_state.json").write_text(
        '{"completed_stage": 4}', encoding="utf-8")  # IDENTIFIED

    args = argparse.Namespace(
        batch=str(batchfile), batch_resume=True, default=True,
        skip_llm=False, merge=False, use_vtt=False, diarizer="oss",
        compute="local", body=None, race_id=None, force_retag=False,
        event_kind=None, title=None,
    )
    run_local._run_batch(args)  # must not raise (would if run_pipeline ran)

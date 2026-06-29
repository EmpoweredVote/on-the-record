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

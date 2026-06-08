"""main() routes --review to the right handler based on transcript presence."""
from __future__ import annotations

import sys

import run_local


def _patch_handlers(monkeypatch):
    called = {}
    monkeypatch.setattr(run_local, "_review_meeting", lambda mid: called.setdefault("review_meeting", mid))
    monkeypatch.setattr(run_local, "_identify_speakers_standalone", lambda mid: called.setdefault("identify", mid))
    return called


def test_review_routes_to_review_meeting_when_named_transcript_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "m-named"
    (tmp_path / mid).mkdir(parents=True)
    (tmp_path / mid / "transcript_named.json").write_text("{}", encoding="utf-8")
    called = _patch_handlers(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--review", mid])
    run_local.main()
    assert called == {"review_meeting": mid}


def test_review_routes_to_identify_when_no_named_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "m-bare"
    (tmp_path / mid).mkdir(parents=True)
    called = _patch_handlers(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--review", mid])
    run_local.main()
    assert called == {"identify": mid}

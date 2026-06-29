"""--resume metadata contract: a resume restores metadata from
transcript_named.json / pipeline_state.json, but a genuinely-missing field
hard-fails (exit 2) rather than being silently stamped Bloomington / council /
today. --default still opts into civic defaults. Mirrors _resolve_metadata.
"""
from __future__ import annotations

import sys
import pytest
import run_local


def _reconstruct_meeting(tmp_path, mid, state: dict):
    """Make a resume target with audio.wav + pipeline_state.json but no
    transcript_named.json (the reconstruct path, which used to silently
    default lost metadata)."""
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)
    (mdir / "audio.wav").write_bytes(b"\x00")
    import json
    (mdir / "pipeline_state.json").write_text(json.dumps(state), encoding="utf-8")
    return mdir


def test_resume_lost_metadata_hard_fails(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: pytest.fail("must not run pipeline"))
    mid = "2026-05-01-regular-session"
    _reconstruct_meeting(tmp_path, mid, {"completed_stage": 3})  # no metadata
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid])
    with pytest.raises(SystemExit) as exc:
        run_local.main()
    assert exc.value.code == 2


def test_resume_council_with_lost_city_hard_fails(monkeypatch, tmp_path):
    # The precise regression: a council meeting whose saved state lost its city
    # must NOT be silently stamped "Bloomington".
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: pytest.fail("must not run pipeline"))
    mid = "2026-05-01-regular-session"
    _reconstruct_meeting(tmp_path, mid, {
        "completed_stage": 3, "event_kind": "council",
        "date": "2026-05-01", "meeting_type": "Regular Session",
    })  # city missing
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid])
    with pytest.raises(SystemExit) as exc:
        run_local.main()
    assert exc.value.code == 2


def test_resume_default_flag_fills_civic(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    captured = {}
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: captured.update(vars(args)))
    mid = "2026-05-01-regular-session"
    # date is restorable; the rest are filled by --default (never date).
    _reconstruct_meeting(tmp_path, mid, {"completed_stage": 3, "date": "2026-05-01"})
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid, "--default"])
    run_local.main()
    assert captured["event_kind"] == "council"
    assert captured["city"] == "Bloomington"
    assert captured["meeting_type"] == "Regular Session"
    assert captured["date"] == "2026-05-01"


def test_resume_restored_metadata_is_kept(monkeypatch, tmp_path):
    # Fully-restored state resumes cleanly and is not overwritten with defaults.
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    captured = {}
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: captured.update(vars(args)))
    mid = "2026-02-10-special"
    _reconstruct_meeting(tmp_path, mid, {
        "completed_stage": 4, "event_kind": "council", "city": "Carmel",
        "date": "2026-02-10", "meeting_type": "Special",
    })
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid])
    run_local.main()
    assert captured["city"] == "Carmel"
    assert captured["event_kind"] == "council"
    assert captured["meeting_type"] == "Special"
    assert captured["date"] == "2026-02-10"

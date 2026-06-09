"""--redo requires --resume; maps stage names to rewind_to."""
from __future__ import annotations

import sys
import pytest
import run_local


def test_redo_requires_resume(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--input", "x.mp4", "--redo", "identify"])
    with pytest.raises(SystemExit):   # argparse parser.error → SystemExit(2)
        run_local.main()


def test_redo_calls_rewind_to_identify(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "2026-02-10-regular-session"
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)
    (mdir / "pipeline_state.json").write_text('{"completed_stage": 7}', encoding="utf-8")
    (mdir / "transcript_named.json").write_text(
        '{"audio_source": "src.mp4", "city": "B", "date": "2026-02-10", "meeting_type": "Regular", "segments": [], "speakers": {}}',
        encoding="utf-8",
    )

    calls = {}
    from src.checkpoint import PipelineStage
    def fake_rewind(self, stage):
        calls["stage"] = stage
    monkeypatch.setattr("src.checkpoint.PipelineState.rewind_to", fake_rewind)
    monkeypatch.setattr(run_local, "run_pipeline", lambda args: calls.setdefault("ran", True))

    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid, "--redo", "identify"])
    run_local.main()

    assert calls["stage"] == PipelineStage.IDENTIFIED
    assert calls.get("ran") is True

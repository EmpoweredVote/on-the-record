"""--redo behavior: the argparse guard and the stage→rewind_to mapping.

Since commits 7282cb0 / 7d8e0f1, --redo works with EITHER --resume or --input
(the guard only rejects --redo with neither), and the stage rewind happens
inside run_pipeline via _redo_map — not in main() before dispatch.
"""
from __future__ import annotations

import sys
import pytest
import run_local


def test_redo_requires_resume_or_input(monkeypatch):
    """--redo with neither --resume nor --input is an argparse error."""
    monkeypatch.setattr(sys, "argv", ["run_local.py", "--redo", "identify"])
    with pytest.raises(SystemExit):  # argparse parser.error → SystemExit(2)
        run_local.main()


def test_redo_with_input_is_allowed(monkeypatch, tmp_path):
    """--redo paired with --input passes the guard (regression for 7282cb0).

    run_pipeline is stubbed so we only exercise the argparse guard + dispatch,
    not the full pipeline.
    """
    monkeypatch.setattr(run_local, "run_pipeline", lambda args: None)
    monkeypatch.setattr(
        sys, "argv",
        ["run_local.py", "--input", str(tmp_path / "x.mp4"), "--redo", "identify", "--default"],
    )
    run_local.main()  # must not raise SystemExit


def test_redo_calls_rewind_to_identify(monkeypatch, tmp_path):
    """--resume <id> --redo identify rewinds to the IDENTIFIED stage.

    The rewind now lives inside run_pipeline (after meeting_dir/state are
    resolved), so we let run_pipeline run far enough to hit the redo block and
    use a sentinel-raising fake rewind_to to stop before the heavy stages.
    """
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    mid = "2026-02-10-regular-session"
    mdir = tmp_path / mid
    mdir.mkdir(parents=True)
    (mdir / "pipeline_state.json").write_text('{"completed_stage": 7}', encoding="utf-8")
    (mdir / "transcript_named.json").write_text(
        '{"audio_source": "src.mp4", "city": "B", "date": "2026-02-10", "meeting_type": "Regular", "segments": [], "speakers": {}}',
        encoding="utf-8",
    )

    from src.checkpoint import PipelineStage

    calls = {}

    class _Stop(Exception):
        """Sentinel to halt run_pipeline right after the redo rewind."""

    def fake_rewind(self, stage):
        calls["stage"] = stage
        raise _Stop

    monkeypatch.setattr("src.checkpoint.PipelineState.rewind_to", fake_rewind)
    # Avoid the HuggingFace token prompt/lookup so we reach the redo block.
    monkeypatch.setattr(run_local, "get_hf_token", lambda: "hf_dummy_token")

    monkeypatch.setattr(sys, "argv", ["run_local.py", "--resume", mid, "--redo", "identify"])
    with pytest.raises(_Stop):
        run_local.main()

    assert calls["stage"] == PipelineStage.IDENTIFIED

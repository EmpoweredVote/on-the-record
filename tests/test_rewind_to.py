"""PipelineState.rewind_to deletes stage>=N artifacts and rewinds completed_stage."""
from __future__ import annotations

import json
from src.checkpoint import PipelineState, PipelineStage


def _touch(p, name):
    (p / name).write_text("x", encoding="utf-8")


def test_rewind_to_identify_deletes_stage4_plus_keeps_diar_audio(tmp_path):
    for n in ("audio.wav", "diarization.json", "embeddings.json", "transcript_raw.json",
              "transcript_named.json", "pre_identifications.json", "summary.json"):
        _touch(tmp_path, n)
    (tmp_path / "exports").mkdir()
    _touch(tmp_path / "exports", "transcript.md")

    state = PipelineState(tmp_path)
    state.completed_stage = PipelineStage.EXPORTED
    state.save()

    state.rewind_to(PipelineStage.IDENTIFIED)

    assert state.completed_stage == PipelineStage.TRANSCRIBED  # one before IDENTIFIED
    assert not (tmp_path / "transcript_named.json").exists()
    assert not (tmp_path / "pre_identifications.json").exists()
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "exports" / "transcript.md").exists()
    assert (tmp_path / "audio.wav").exists()
    assert (tmp_path / "diarization.json").exists()
    assert (tmp_path / "transcript_raw.json").exists()
    data = json.loads((tmp_path / "pipeline_state.json").read_text())
    assert data["completed_stage"] == int(PipelineStage.TRANSCRIBED)


def test_rewind_to_diarize_keeps_audio_resets_progress(tmp_path):
    for n in ("audio.wav", "diarization.json", "embeddings.json", "transcript_raw.json"):
        _touch(tmp_path, n)
    state = PipelineState(tmp_path)
    state.completed_stage = PipelineStage.TRANSCRIBED
    state.transcription_progress = 50
    state.total_segments = 100
    state.save()

    state.rewind_to(PipelineStage.DIARIZED)

    assert state.completed_stage == PipelineStage.INGESTED
    assert not (tmp_path / "diarization.json").exists()
    assert not (tmp_path / "embeddings.json").exists()
    assert not (tmp_path / "transcript_raw.json").exists()
    assert (tmp_path / "audio.wav").exists()
    assert state.transcription_progress == 0
    assert state.total_segments == 0


def test_rewind_to_summary_only_clears_summary_and_exports(tmp_path):
    for n in ("transcript_named.json", "summary.json"):
        _touch(tmp_path, n)
    (tmp_path / "exports").mkdir(); _touch(tmp_path / "exports", "x.md")
    state = PipelineState(tmp_path)
    state.completed_stage = PipelineStage.EXPORTED
    state.save()

    state.rewind_to(PipelineStage.SUMMARIZED)

    assert state.completed_stage == PipelineStage.IDENTIFIED
    assert (tmp_path / "transcript_named.json").exists()
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "exports" / "x.md").exists()

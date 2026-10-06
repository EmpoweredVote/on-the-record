"""Clip-window persistence on PipelineState."""

from src.checkpoint import PipelineState


def test_clip_window_persists_and_reloads(tmp_path):
    state = PipelineState(tmp_path)
    assert state.clip_start_seconds is None
    assert state.clip_end_seconds is None

    state.clip_start_seconds = 1380.0
    state.clip_end_seconds = 2880.0
    state.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.clip_start_seconds == 1380.0
    assert reloaded.clip_end_seconds == 2880.0


def test_clip_window_absent_in_legacy_state_file(tmp_path):
    (tmp_path / "pipeline_state.json").write_text('{"completed_stage": 1}')
    state = PipelineState(tmp_path)
    assert state.clip_start_seconds is None
    assert state.clip_end_seconds is None


def test_rewind_to_identified_deletes_name_suggestions_but_not_log(tmp_path):
    from src.checkpoint import PipelineStage
    state = PipelineState(tmp_path)
    for n in ("transcript_named.json", "name_suggestions.json", "name_suggestion_log.jsonl"):
        (tmp_path / n).write_text("{}")
    state.rewind_to(PipelineStage.IDENTIFIED)
    assert not (tmp_path / "name_suggestions.json").exists()
    assert (tmp_path / "name_suggestion_log.jsonl").exists()

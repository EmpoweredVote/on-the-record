"""Reconciliation of the --clip flag against persisted PipelineState."""

import pytest

from src.checkpoint import PipelineStage, PipelineState
from run_local import _reconcile_clip_window


def test_first_run_persists_window(tmp_path):
    state = PipelineState(tmp_path)
    start, end = _reconcile_clip_window(state, 1380.0, 2880.0)
    assert (start, end) == (1380.0, 2880.0)
    assert PipelineState(tmp_path).clip_start_seconds == 1380.0


def test_resume_without_flag_reads_persisted(tmp_path):
    state = PipelineState(tmp_path)
    state.clip_start_seconds, state.clip_end_seconds = 1380.0, 2880.0
    state.save()
    start, end = _reconcile_clip_window(state, None, None)
    assert (start, end) == (1380.0, 2880.0)


def test_repassing_same_window_is_noop(tmp_path):
    state = PipelineState(tmp_path)
    state.clip_start_seconds, state.clip_end_seconds = 1380.0, 2880.0
    state.save()
    start, end = _reconcile_clip_window(state, 1380.0, 2880.0)
    assert (start, end) == (1380.0, 2880.0)


def test_conflicting_window_after_ingest_errors(tmp_path):
    state = PipelineState(tmp_path)
    state.clip_start_seconds, state.clip_end_seconds = 1380.0, 2880.0
    state.completed_stage = PipelineStage.INGESTED
    state.save()
    with pytest.raises(SystemExit):
        _reconcile_clip_window(state, 1500.0, 3000.0)


def test_no_clip_anywhere_returns_none(tmp_path):
    state = PipelineState(tmp_path)
    assert _reconcile_clip_window(state, None, None) == (None, None)

"""Round-trip tests for the gate fields on PipelineState."""
from __future__ import annotations

from src.checkpoint import PipelineState


def test_gate_fields_default_none(tmp_path):
    state = PipelineState(tmp_path)
    assert state.review_status is None
    assert state.trusted_coverage is None


def test_gate_fields_persist(tmp_path):
    state = PipelineState(tmp_path)
    state.review_status = "review"
    state.trusted_coverage = 0.73
    state.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.review_status == "review"
    assert reloaded.trusted_coverage == 0.73

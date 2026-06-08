"""Tests for the run_local.py roster chooser (spec 2026-06-07-roster-chooser-design)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.checkpoint import PipelineState, PipelineStage


def test_roster_choice_defaults_to_none(tmp_path):
    state = PipelineState(tmp_path)
    assert state.roster_choice is None


def test_roster_choice_roundtrips_through_save(tmp_path):
    state = PipelineState(tmp_path)
    state.roster_choice = "__none__"
    state.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.roster_choice == "__none__"


def test_roster_choice_persisted_in_json(tmp_path):
    state = PipelineState(tmp_path)
    state.roster_choice = "bloomington-common-council"
    state.save()

    data = json.loads((tmp_path / "pipeline_state.json").read_text())
    assert data["roster_choice"] == "bloomington-common-council"


def test_legacy_state_file_without_roster_choice_loads_as_none(tmp_path):
    # State file written before this feature existed (no roster_choice key).
    (tmp_path / "pipeline_state.json").write_text(json.dumps({
        "completed_stage": 3,
        "transcription_progress": 0,
        "total_segments": 0,
        "body_slug": None,
    }))
    state = PipelineState(tmp_path)
    assert state.roster_choice is None
    assert state.completed_stage == PipelineStage.TRANSCRIBED

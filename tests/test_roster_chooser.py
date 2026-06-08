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


def _write_cache(rosters_dir: Path, slug: str, body_key: str, n_members: int) -> None:
    rosters_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "body_slug": slug,
        "body_key": body_key,
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "politicians": [{"full_name": f"Member {i}", "title": "Councilmember"} for i in range(n_members)],
    }
    (rosters_dir / f"{slug}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_list_cached_rosters_empty(tmp_config_dir):
    import run_local
    assert run_local._list_cached_rosters() == []


def test_list_cached_rosters_returns_sorted_slug_and_label(tmp_config_dir):
    import run_local
    rosters = tmp_config_dir / "rosters"
    _write_cache(rosters, "zzz-town-council", "ZZZ Town Council", 3)
    _write_cache(rosters, "aaa-city-council", "AAA City Council", 5)

    result = run_local._list_cached_rosters()

    # sorted by slug (filename)
    assert [slug for slug, _ in result] == ["aaa-city-council", "zzz-town-council"]
    assert result[0][1] == "AAA City Council (5 members) [aaa-city-council]"
    assert result[1][1] == "ZZZ Town Council (3 members) [zzz-town-council]"


def test_list_cached_rosters_bad_json_falls_back_to_slug(tmp_config_dir):
    import run_local
    rosters = tmp_config_dir / "rosters"
    rosters.mkdir(parents=True, exist_ok=True)
    (rosters / "broken-council.json").write_text("not valid json", encoding="utf-8")

    result = run_local._list_cached_rosters()

    assert result == [("broken-council", "broken-council")]

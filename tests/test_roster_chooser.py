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


@pytest.mark.parametrize("kwargs,expected", [
    # The only "prompt" case: interactive, no cli body, no persisted body,
    # no prior choice, not yet identified.
    (dict(cli_body=None, persisted_body=None, roster_choice=None, identified=False, isatty=True), True),
    # --body given → never prompt
    (dict(cli_body="x", persisted_body=None, roster_choice=None, identified=False, isatty=True), False),
    # already tagged → never prompt
    (dict(cli_body=None, persisted_body="x", roster_choice=None, identified=False, isatty=True), False),
    # prior choice recorded → never prompt
    (dict(cli_body=None, persisted_body=None, roster_choice="__none__", identified=False, isatty=True), False),
    # identification already complete → never prompt
    (dict(cli_body=None, persisted_body=None, roster_choice=None, identified=True, isatty=True), False),
    # not a terminal → never prompt
    (dict(cli_body=None, persisted_body=None, roster_choice=None, identified=False, isatty=False), False),
])
def test_should_prompt_roster(kwargs, expected):
    import run_local
    assert run_local._should_prompt_roster(**kwargs) is expected


def _setup_menu(tmp_config_dir, *, legacy=False):
    rosters = tmp_config_dir / "rosters"
    _write_cache(rosters, "bloomington-common-council", "Bloomington Common Council", 10)
    if legacy:
        (tmp_config_dir / "council_roster.json").write_text(json.dumps({
            "city": "Bloomington", "body": "City Council",
            "members": [{"name": f"Councilmember {i}"} for i in range(8)],
        }), encoding="utf-8")


def test_prompt_pick_cached_roster(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug == "bloomington-common-council"
    assert marker == "bloomington-common-council"


def test_prompt_pick_legacy(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    # Menu: 1=cached, 2=legacy, 3=no roster
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__legacy__"


def test_prompt_pick_no_roster(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    monkeypatch.setattr("builtins.input", lambda *a: "3")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"


def test_prompt_bare_enter_defaults_to_no_roster(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=True)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"


def test_prompt_reprompts_on_bad_input(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=False)  # menu: 1=cached, 2=no roster
    answers = iter(["banana", "9", "1"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug == "bloomington-common-council"
    assert marker == "bloomington-common-council"


def test_prompt_no_legacy_present(tmp_config_dir, monkeypatch):
    import run_local
    _setup_menu(tmp_config_dir, legacy=False)  # menu: 1=cached, 2=no roster
    monkeypatch.setattr("builtins.input", lambda *a: "2")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"


def test_prompt_empty_cache_only_shows_no_roster(tmp_config_dir, monkeypatch):
    # No cached rosters and no legacy file → menu has only "No roster" as item 1.
    import run_local
    monkeypatch.setattr("builtins.input", lambda *a: "1")
    body_slug, marker = run_local._prompt_roster_choice()
    assert body_slug is None
    assert marker == "__none__"

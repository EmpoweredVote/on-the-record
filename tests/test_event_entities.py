import json

import pytest

import run_local
from src.checkpoint import PipelineState
from src.event_entities import validate_event_entities
from src.models import Meeting

CHAMBER_ID = "11111111-1111-4111-8111-111111111111"
RACE_ID = "22222222-2222-4222-8222-222222222222"


def test_meeting_round_trip_preserves_race_id():
    restored = Meeting.from_dict(Meeting(
        meeting_id="debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        event_kind="debate",
        race_id=RACE_ID,
    ).to_dict())
    assert restored.race_id == RACE_ID


def test_legacy_meeting_defaults_race_id_to_none():
    restored = Meeting.from_dict({
        "meeting_id": "legacy",
        "city": "Bloomington",
        "date": "2026-02-18",
    })
    assert restored.race_id is None


def test_pipeline_state_persists_race_id(tmp_path):
    state = PipelineState(tmp_path)
    state.race_id = RACE_ID
    state.save()
    assert PipelineState(tmp_path).race_id == RACE_ID
    assert json.loads((tmp_path / "pipeline_state.json").read_text())["race_id"] == RACE_ID


def test_entity_validation_rules():
    assert validate_event_entities("council", CHAMBER_ID, None) is None
    assert validate_event_entities("debate", None, RACE_ID) is None
    assert "chamber_id is required" in validate_event_entities(
        "council", None, None
    )
    assert "race_id is required" in validate_event_entities(
        "debate", None, None
    )
    assert "cannot both be set" in validate_event_entities(
        "other", CHAMBER_ID, RACE_ID
    )


def test_entity_validation_rejects_bad_uuid():
    with pytest.raises(ValueError, match="race_id must be a UUID"):
        validate_event_entities("debate", None, "not-a-uuid")


def test_parser_accepts_race_id():
    parser = run_local.build_parser()
    args = parser.parse_args([
        "--input", "debate.mp4",
        "--event-kind", "debate",
        "--race-id", RACE_ID,
    ])
    assert args.race_id == RACE_ID


def test_resolve_race_id_persists_first_value(tmp_path):
    state = PipelineState(tmp_path)
    assert run_local._resolve_race_id(state, RACE_ID) == RACE_ID
    assert PipelineState(tmp_path).race_id == RACE_ID


def test_resolve_race_id_rejects_mismatch(tmp_path):
    state = PipelineState(tmp_path)
    state.race_id = RACE_ID
    state.save()

    with pytest.raises(RuntimeError, match="already linked"):
        run_local._resolve_race_id(
            state,
            "33333333-3333-4333-8333-333333333333",
        )

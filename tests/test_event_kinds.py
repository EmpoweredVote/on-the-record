from types import SimpleNamespace

import pytest

import run_local
from src.event_kinds import EVENT_KINDS, validate_event_kind
from src.models import Meeting


def test_meeting_round_trip_preserves_title_event_kind_and_null_city():
    meeting = Meeting(
        meeting_id="ca-governor-debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
    )

    restored = Meeting.from_dict(meeting.to_dict())

    assert restored.title == "California Governor Debate"
    assert restored.event_kind == "debate"
    assert restored.city is None


def test_legacy_meeting_has_no_event_kind():
    # legacy meetings without event_kind load as None, not a fabricated council
    restored = Meeting.from_dict({
        "meeting_id": "legacy",
        "city": "Bloomington",
        "date": "2026-02-18",
        "meeting_type": "Regular Session",
    })

    assert restored.title is None
    assert restored.event_kind is None


def test_press_conference_in_event_kinds():
    assert "press_conference" in EVENT_KINDS


def test_validate_press_conference():
    assert validate_event_kind("press_conference") == "press_conference"


def test_validate_event_kind_lists_allowed_values():
    with pytest.raises(ValueError, match="town_hall.*council.*school_board.*debate"):
        validate_event_kind("town_hall")


def test_resolve_metadata_defaults_event_kind_without_prompt(monkeypatch):
    monkeypatch.setattr(run_local.sys.stdin, "isatty", lambda: False)
    args = SimpleNamespace(
        city="Bloomington",
        date="2026-02-18",
        meeting_type="Regular Session",
        title=None,
        event_kind=None,
        default=True,  # council default is now opt-in via --default
    )

    run_local._resolve_metadata(args)

    assert args.event_kind == "council"
    assert args.title is None


def test_cityless_debate_does_not_inherit_council_city_default(monkeypatch):
    monkeypatch.setattr(run_local.sys.stdin, "isatty", lambda: False)
    args = SimpleNamespace(
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
        default=False,
    )

    run_local._resolve_metadata(args)

    assert args.city is None


def test_parser_accepts_title_and_event_kind():
    parser = run_local.build_parser()
    args = parser.parse_args([
        "--input", "meeting.mp4",
        "--title", "California Governor Debate",
        "--event-kind", "debate",
    ])

    assert args.title == "California Governor Debate"
    assert args.event_kind == "debate"


def test_parser_rejects_unknown_event_kind():
    parser = run_local.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--input", "meeting.mp4",
            "--event-kind", "town_hall",
        ])

"""Tests for the pure poller orchestration (change detection + work planning)."""
import json
from pathlib import Path

from src.agenda_pipeline import PollState, WorkItem, plan_work


class FakeMeeting:
    def __init__(self, start, marker, title="Common Council Regular Session"):
        self.start = start
        self.agenda_url = marker.split("|")[0] if marker else None
        self.agenda_updated_marker = marker or ""
        self.title = title


def test_poll_state_round_trip(tmp_path):
    state = PollState(tmp_path / "state.json")
    assert state.marker_for("bloomington-city-council-2026-07-29") is None
    state.record("bloomington-city-council-2026-07-29", "https://x/17202|2026-07-27T10:02")
    state2 = PollState(tmp_path / "state.json")
    assert state2.marker_for("bloomington-city-council-2026-07-29") == "https://x/17202|2026-07-27T10:02"


def test_poll_state_creates_parent_dirs_and_valid_json(tmp_path):
    path = tmp_path / "agendas" / "bloomington-city-council" / "poll_state.json"
    state = PollState(path)
    state.record("slug-a", "m1")
    state.record("slug-b", "m2")
    doc = json.loads(path.read_text())
    assert doc == {"slug-a": "m1", "slug-b": "m2"}


def test_plan_work_skips_unchanged_and_agendaless(tmp_path):
    meetings = [
        FakeMeeting("2026-07-29T18:30:00-04:00", "https://x/a.pdf|v1"),
        FakeMeeting("2026-08-05T18:30:00-04:00", None),
    ]
    state = PollState(tmp_path / "s.json")
    state.record("bloomington-city-council-2026-07-29", "https://x/a.pdf|v1")
    work, skipped = plan_work(meetings, state, body_slug="bloomington-city-council")
    assert work == []
    assert len(skipped) == 2
    reasons = dict(skipped)
    assert reasons["bloomington-city-council-2026-07-29"] == "agenda unchanged"
    assert reasons["bloomington-city-council-2026-08-05"] == "no agenda posted yet"

    fresh = PollState(tmp_path / "s2.json")
    work, skipped = plan_work(meetings, fresh, body_slug="bloomington-city-council")
    assert len(work) == 1
    assert work[0].slug == "bloomington-city-council-2026-07-29"
    assert work[0].date == "2026-07-29"
    assert work[0].meeting is meetings[0]


def test_plan_work_changed_marker_is_work(tmp_path):
    meetings = [FakeMeeting("2026-07-29T18:30:00-04:00", "https://x/a.pdf|v2")]
    state = PollState(tmp_path / "s.json")
    state.record("bloomington-city-council-2026-07-29", "https://x/a.pdf|v1")
    work, skipped = plan_work(meetings, state, body_slug="bloomington-city-council")
    assert skipped == []
    assert len(work) == 1
    assert work[0].slug == "bloomington-city-council-2026-07-29"


def test_slug_consistency_with_publish():
    from src.bodies import BLOOMINGTON_COMMON_COUNCIL
    from src.publish import scheduled_slug
    assert (
        f"{BLOOMINGTON_COMMON_COUNCIL.slug}-2026-07-29"
        == scheduled_slug(BLOOMINGTON_COMMON_COUNCIL, "2026-07-29")
    )

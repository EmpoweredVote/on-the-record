from __future__ import annotations

import pytest

from src.models import Meeting, SpeakerMapping
from src.publish import _reconcile_event_races


class _RecordingCursor:
    """Captures execute() calls and serves canned fetchall() rows in order."""

    def __init__(self, fetch_results):
        self._fetch = list(fetch_results)
        self.calls = []  # list of (sql, params)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._fetch.pop(0)


def _meeting(kind, *names):
    speakers = {
        f"S{i}": SpeakerMapping(speaker_label=f"S{i}", speaker_name=n,
                                politician_id=f"pol-{i}")
        for i, n in enumerate(names)
    }
    return Meeting(meeting_id="m1", city="X", date="2026-04-01",
                   event_kind=kind, speakers=speakers)


def test_reconcile_writes_union_for_multi_race_forum():
    cur = _RecordingCursor([[("race-clerk",), ("race-pros",)]])
    _reconcile_event_races(cur, _meeting("forum", "A", "B"), "muid-1")
    sqls = [c[0] for c in cur.calls]
    assert any("DELETE FROM meetings.event_races" in s for s in sqls)
    inserts = [c for c in cur.calls if "INSERT INTO meetings.event_races" in c[0]]
    inserted_races = {c[1][1] for c in inserts}
    assert inserted_races == {"race-clerk", "race-pros"}
    assert all(c[1][0] == "muid-1" for c in inserts)


def test_reconcile_single_race_debate():
    cur = _RecordingCursor([[("race-gov",)]])
    _reconcile_event_races(cur, _meeting("debate", "A"), "muid-1")
    inserts = [c for c in cur.calls if "INSERT INTO meetings.event_races" in c[0]]
    assert {c[1][1] for c in inserts} == {"race-gov"}


def test_reconcile_zero_races_debate_raises():
    cur = _RecordingCursor([[]])
    with pytest.raises(RuntimeError, match="no race"):
        _reconcile_event_races(cur, _meeting("debate", "A"), "muid-1")


def test_reconcile_zero_races_council_ok():
    cur = _RecordingCursor([[]])
    m = Meeting(meeting_id="m1", city="X", date="2026-04-01", event_kind="council",
                speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="Mayor",
                                               politician_id="pol-0")})
    _reconcile_event_races(cur, m, "muid-1")  # must not raise
    assert any("DELETE FROM meetings.event_races" in c[0] for c in cur.calls)

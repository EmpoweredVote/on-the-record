"""Tests for the meetings.* publisher: playback resolution helpers.

The row-building logic was refactored in commit dd9b26e into cursor-bound
upserts (``_upsert_meeting`` / ``_upsert_speakers`` / ``_replace_segments``)
that need a live Postgres connection, so the old pure ``build_*_row`` helpers
no longer exist. Their unit tests were removed with them. The pure URL
helpers below survived the refactor unchanged and remain worth covering.
"""

import pytest

from src.models import Meeting, SpeakerMapping
from src.publish import _resolve_chamber_id, _upsert_event_orgs, _upsert_meeting
from src.publish import _upsert_local_people, _upsert_speakers
from src.publish import extract_youtube_id, resolve_playback


# ---------------------------------------------------------------------------
# resolve_playback / extract_youtube_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=AbC12345xyz", "AbC12345xyz"),
        ("https://youtube.com/watch?v=AbC12345xyz&t=120", "AbC12345xyz"),
        ("https://youtu.be/AbC12345xyz", "AbC12345xyz"),
        ("https://youtu.be/AbC12345xyz?si=share", "AbC12345xyz"),
        ("https://www.youtube.com/embed/AbC12345xyz", "AbC12345xyz"),
        ("https://www.youtube.com/shorts/AbC12345xyz", "AbC12345xyz"),
        ("https://www.youtube.com/live/AbC12345xyz", "AbC12345xyz"),
        ("https://m.youtube.com/watch?v=AbC12345xyz", "AbC12345xyz"),
        ("https://vimeo.com/12345", None),
        ("https://example.com/watch?v=nope", None),
        ("not a url", None),
    ],
)
def test_extract_youtube_id(url, expected):
    assert extract_youtube_id(url) == expected


def test_resolve_playback_youtube():
    assert resolve_playback("https://www.youtube.com/watch?v=AbC12345xyz") == (
        "youtube",
        "AbC12345xyz",
    )


def test_resolve_playback_catstv_blob_is_direct_file():
    url = "https://catstv.blob.core.windows.net/videoarchive/B_CC_260218.m4v"
    assert resolve_playback(url) == ("file", url)


def test_resolve_playback_direct_mp4():
    url = "https://example.gov/meetings/2026-02-10.mp4"
    assert resolve_playback(url) == ("file", url)


def test_resolve_playback_hls():
    url = "https://stream.example.gov/live/playlist.m3u8"
    assert resolve_playback(url) == ("hls", url)


def test_resolve_playback_unknown_provider():
    assert resolve_playback("https://www.facebook.com/video/123") == (None, None)


def test_resolve_playback_local_path():
    assert resolve_playback("/Users/operator/meeting.mp4") == (None, None)
    assert resolve_playback("") == (None, None)


def test_resolve_playback_catstv_page_falls_back_on_error(monkeypatch):
    """A catstv.net page URL that can't be scraped degrades to (None, None)."""
    import src.download as download

    def boom(url):
        raise ValueError("no video found")

    monkeypatch.setattr(download, "_extract_blob_url_from_page", boom)
    assert resolve_playback("https://catstv.net/government.php?id=99") == (None, None)


RACE_ID = "22222222-2222-4222-8222-222222222222"
MEETING_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class RecordingCursor:
    def __init__(self, select_row=None, fetch_rows=None):
        self.select_row = select_row
        self.fetch_rows = list(fetch_rows or [])
        self.calls = []
        self._fetchone = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "FROM essentials.chambers" in sql:
            return
        if "SELECT id FROM meetings.meetings" in sql:
            self._fetchone = self.select_row
        elif "RETURNING id" in sql:
            self._fetchone = ("new-uuid",)

    def fetchall(self):
        return self.fetch_rows

    def fetchone(self):
        return self._fetchone


@pytest.mark.parametrize("existing_row", [("existing-uuid",), None])
def test_upsert_meeting_writes_title_and_event_kind(existing_row):
    cur = RecordingCursor(existing_row)
    meeting = Meeting(
        meeting_id="ca-governor-debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
        race_id=RACE_ID,
    )

    _upsert_meeting(cur, meeting, None)

    write_sql, write_params = cur.calls[1]
    assert "title" in write_sql
    assert "event_kind" in write_sql
    assert "California Governor Debate" in write_params
    assert "debate" in write_params


def test_resolve_chamber_id_returns_unique_match():
    cur = RecordingCursor(fetch_rows=[
        ("11111111-1111-4111-8111-111111111111",),
    ])
    assert _resolve_chamber_id(cur, "test-council") == (
        "11111111-1111-4111-8111-111111111111"
    )


def test_resolve_chamber_id_returns_none_for_missing_match():
    cur = RecordingCursor(fetch_rows=[])
    assert _resolve_chamber_id(cur, "missing") is None


def test_resolve_chamber_id_returns_none_for_duplicate_slug():
    cur = RecordingCursor(fetch_rows=[
        ("11111111-1111-4111-8111-111111111111",),
        ("22222222-2222-4222-8222-222222222222",),
    ])
    assert _resolve_chamber_id(cur, "duplicate") is None


# NOTE: there is no longer any entity-state rejection reachable through
# _upsert_meeting — chamber_id is optional for council/school_board (multi-seat
# bodies), race_id is no longer passed to the validator (races are derived into
# meetings.event_races), so the only remaining rule (chamber+race mutual
# exclusion) can't trigger here. The validator rules are unit-tested directly in
# tests/test_event_entities.py.


@pytest.mark.parametrize("existing_row", [("existing-uuid",), None])
def test_publish_writes_chamber_id_for_council(existing_row):
    cur = RecordingCursor(
        select_row=existing_row,
        fetch_rows=[("11111111-1111-4111-8111-111111111111",)],
    )
    meeting = Meeting(
        meeting_id="council-event",
        city="Bloomington",
        date="2026-02-18",
        meeting_type="Regular Session",
        event_kind="council",
    )

    _upsert_meeting(cur, meeting, "test-council")

    write_sql, write_params = cur.calls[-1]
    assert "chamber_id" in write_sql
    # publish no longer writes the meetings.meetings.race_id column
    assert "race_id" not in write_sql
    assert "11111111-1111-4111-8111-111111111111" in write_params


@pytest.mark.parametrize("existing_row", [("existing-uuid",), None])
def test_publish_does_not_write_race_id_for_debate(existing_row):
    """Publish stopped writing the meetings.meetings.race_id column; a debate's
    races now live in meetings.event_races (reconciled separately). The meeting
    row must carry neither the race_id column nor its value."""
    cur = RecordingCursor(select_row=existing_row)
    meeting = Meeting(
        meeting_id="debate-event",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        event_kind="debate",
        race_id=RACE_ID,
    )

    _upsert_meeting(cur, meeting, None)

    write_sql, write_params = cur.calls[-1]
    assert "chamber_id" in write_sql
    assert "race_id" not in write_sql
    assert RACE_ID not in write_params


def test_event_orgs_upserted():
    cur = RecordingCursor()
    _upsert_event_orgs(cur, MEETING_UUID, ["California Courier"])
    sqls = [sql for sql, _ in cur.calls]
    assert any("event_orgs" in sql for sql in sqls)
    params_list = [params for _, params in cur.calls]
    assert any("California Courier" in (params or ()) for params in params_list)


def test_event_orgs_upsert_empty_skips_insert():
    cur = RecordingCursor()
    _upsert_event_orgs(cur, MEETING_UUID, [])
    insert_calls = [sql for sql, _ in cur.calls if "INSERT" in sql and "event_orgs" in sql]
    assert len(insert_calls) == 0


# ---------------------------------------------------------------------------
# unidentified handles must not publish as local_people
# ---------------------------------------------------------------------------

def test_unidentified_speaker_publishes_no_local_person_and_null_local_slug():
    """An unidentified handle is a placeholder, not a public entity: no
    local_people row, and the speakers.local_slug column is written NULL so the
    FK to local_people.slug holds."""
    meeting = Meeting(
        meeting_id="event",
        city=None,
        date="2026-06-02",
        meeting_type="Event",
        event_kind="debate",
        race_id=RACE_ID,
        speakers={
            "S0": SpeakerMapping(
                speaker_label="S0",
                speaker_name="Unidentified Speaker",
                local_slug="unidentified-m-s0",
                speaker_status="unidentified",
            ),
        },
    )

    people_cur = RecordingCursor()
    _upsert_local_people(people_cur, meeting)
    local_people_inserts = [
        sql for sql, _ in people_cur.calls
        if "INSERT INTO meetings.local_people" in sql
    ]
    assert local_people_inserts == []   # no public placeholder entity

    speakers_cur = RecordingCursor(select_row=None)
    _upsert_speakers(speakers_cur, meeting, MEETING_UUID)
    insert_sql, insert_params = next(
        (sql, params) for sql, params in speakers_cur.calls
        if "INSERT INTO meetings.speakers" in sql
    )
    assert insert_params[-1] is None   # local_slug column written NULL


def test_publish_meeting_never_calls_deploy_hook(monkeypatch):
    """publish_meeting never fires _trigger_deploy_hook — the web app reads data
    live from the API so per-publish site rebuilds are no longer needed."""
    import src.publish as publish

    calls = {"deploy": 0}
    monkeypatch.setattr(publish, "_trigger_deploy_hook", lambda: calls.__setitem__("deploy", calls["deploy"] + 1))
    # Stub the whole DB transaction so we only exercise the deploy decision.
    monkeypatch.setattr(publish, "_require_db_url", lambda: "postgresql://x")

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchone(self): return ("muid",)
        def fetchall(self): return []
    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()
        def close(self): pass
    monkeypatch.setattr(publish.psycopg2, "connect", lambda *a, **k: _Conn())
    # Stub the per-step helpers so publish_meeting reaches the deploy decision.
    for fn in ("_upsert_meeting", "_upsert_event_orgs", "_upsert_local_people",
               "_reconcile_event_races", "_replace_topics"):
        monkeypatch.setattr(publish, fn, lambda *a, **k: "muid")
    monkeypatch.setattr(publish, "_upsert_speakers", lambda *a, **k: {})
    monkeypatch.setattr(publish, "_replace_segments", lambda *a, **k: 0)

    from src.models import Meeting
    m = Meeting(meeting_id="m1", city="X", date="2026-04-01")

    publish.publish_meeting(m, None, trigger_deploy=False)
    assert calls["deploy"] == 0
    publish.publish_meeting(m, None)  # trigger_deploy=True (default) — hook still not called
    assert calls["deploy"] == 0

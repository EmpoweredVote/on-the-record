import json
from pathlib import Path

from src.onboard import OnBoardMeeting, fetch_meetings_window

FIX = Path(__file__).parent / "fixtures" / "onboard"


def _fake_fetch(url: str) -> str:
    return (FIX / "meetings_window_2026.json").read_text()


def test_fetch_meetings_window_returns_council_regular_sessions():
    meetings = fetch_meetings_window(
        "2026-07-01", "2026-08-31", title_prefix="Common Council Regular Session", fetch=_fake_fetch
    )
    assert meetings, "expected at least one Regular Session in the window"
    m = meetings[0]
    assert isinstance(m, OnBoardMeeting)
    assert m.title == "Common Council Regular Session"
    assert m.start.startswith("2026-")
    assert m.onboard_id
    assert meetings == sorted(meetings, key=lambda x: x.start)


def test_agenda_file_properties():
    meetings = fetch_meetings_window(
        "2026-07-01", "2026-08-31", title_prefix="Common Council Regular Session", fetch=_fake_fetch
    )
    with_agenda = [m for m in meetings if m.agenda_url]
    assert with_agenda, "the 2026-07-29 session has an Agenda file"
    m = with_agenda[0]
    assert m.agenda_url.startswith("https://bloomington.in.gov/onboard/")
    assert m.agenda_created
    assert m.agenda_updated_marker.startswith(m.agenda_url)


def test_amended_agenda_picks_latest_created():
    # The 2026-07-22 Common Council Regular Session (onboard id 11958) carries
    # TWO Agenda entries in the fixture: file 17132 (created 2026-07-17
    # 19:27:29, original) and file 17185 (created 2026-07-22 13:25:54,
    # amended). The adapter must surface the later-created one.
    meetings = fetch_meetings_window(
        "2026-07-22", "2026-07-22", title_prefix="Common Council", fetch=_fake_fetch
    )
    session = [m for m in meetings if m.title == "Common Council Regular Session"]
    assert len(session) == 1
    m = session[0]
    assert m.onboard_id == "11958"
    assert m.agenda_url == "https://bloomington.in.gov/onboard/meetingFiles/17185/download"
    assert m.agenda_created == "2026-07-22 13:25:54"


def test_canceled_meetings_are_excluded():
    all_titles = []
    meetings = fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="", fetch=_fake_fetch)
    for m in meetings:
        all_titles.append(m.title)
    assert all_titles, "expected meetings in the window"
    assert not any(t.lower().startswith("cancel") for t in all_titles)


def test_empty_files_list_is_tolerated():
    meetings = fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="", fetch=_fake_fetch)
    no_files = [m for m in meetings if m.agenda_url is None]
    assert no_files, "fixture contains meetings with files: [] — they must parse with agenda_url None"


def test_window_filter_and_url_construction():
    seen = {}

    def capture(url):
        seen["url"] = url
        return (FIX / "meetings_window_2026.json").read_text()

    meetings = fetch_meetings_window("2026-07-20", "2026-07-30", title_prefix="", fetch=capture)
    assert "format=json" in seen["url"]
    assert "start=2026-07-20" in seen["url"]
    assert "end=2026-07-30" in seen["url"]
    # The fixture spans Jul-Aug; the client must ALSO filter locally to the
    # requested window (defense against the API ignoring params).
    assert meetings
    assert all("2026-07-20" <= m.start[:10] <= "2026-07-30" for m in meetings)


def test_malformed_json_returns_empty():
    assert fetch_meetings_window("2026-07-01", "2026-08-31", title_prefix="X", fetch=lambda u: "not json") == []

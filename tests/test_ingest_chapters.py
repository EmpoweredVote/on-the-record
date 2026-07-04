"""Chapter extraction from yt-dlp metadata (description fallback + normalization)."""

from src.ingest import parse_description_chapters, normalize_chapters


CBS_DESCRIPTION = """California has spent more than $14 billion on high-speed rail, but the project remains unfinished and controversial.

00:00 CBS News California Investigates
00:14 Steve Hilton on high-speed rail.
02:04 Chad Bianco on high-speed rail.
03:33 Tom Steyer on high-speed rail.
04:16 Katie Porter on high-speed rail.
05:59  Matt Mahan on high-speed rail.
07:08 Xavier Becerra on high-speed rail.
09:40 Antonio Villaraigosa on high-speed rail.
12:16 Betty Yee on high-speed rail.
14:05 Tony Thurmond on high-speed rail.
** Rep. Eric Swalwell appeared in an earlier version of this composite.

COMING SOON | CBS News California Interactive Governor Candidate Guide
"""


def test_parses_only_timestamp_led_lines():
    chapters = parse_description_chapters(CBS_DESCRIPTION)
    titles = [c["title"] for c in chapters]
    assert len(chapters) == 10
    assert titles[0] == "CBS News California Investigates"
    assert titles[1] == "Steve Hilton on high-speed rail."
    # Double-space after timestamp still parses, title is stripped:
    assert "Matt Mahan on high-speed rail." in titles
    # Non-timestamp lines excluded:
    assert all("Swalwell" not in t for t in titles)
    assert all("COMING SOON" not in t for t in titles)


def test_start_times_and_inferred_end_times():
    chapters = parse_description_chapters(CBS_DESCRIPTION)
    assert chapters[0]["start_time"] == 0.0
    assert chapters[1]["start_time"] == 14.0
    assert chapters[2]["start_time"] == 124.0  # 02:04
    # end_time is the next entry's start; last is None
    assert chapters[0]["end_time"] == 14.0
    assert chapters[-1]["end_time"] is None


def test_hms_timestamps_parse():
    desc = "Intro at start\n1:02:03 Deep segment\n1:05:10 Next segment"
    chapters = parse_description_chapters(desc)
    assert len(chapters) == 2
    assert chapters[0]["start_time"] == 3723.0  # 1:02:03
    assert chapters[0]["title"] == "Deep segment"


def test_fewer_than_two_matches_returns_empty():
    desc = "Some prose.\n00:30 The only timestamp line here.\nMore prose."
    assert parse_description_chapters(desc) == []


def test_none_or_empty_description():
    assert parse_description_chapters(None) == []
    assert parse_description_chapters("") == []


def test_normalize_uses_ytdlp_chapters_when_present():
    # start times are non-zero so the intro-drop doesn't interfere here;
    # this test isolates "prefer yt-dlp chapters over description".
    info = {
        "chapters": [
            {"start_time": 10.0, "end_time": 40.0, "title": "Zoning"},
            {"start_time": 40.0, "end_time": 90.0, "title": "Housing"},
        ],
        "description": "00:00 Ignored\n01:00 Also ignored",
    }
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["Zoning", "Housing"]
    assert chapters[0]["start_time"] == 10.0
    assert chapters[0]["end_time"] == 40.0


def test_normalize_falls_back_to_description():
    info = {
        "chapters": [],
        "description": "00:30 First topic\n01:30 Second topic",
    }
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["First topic", "Second topic"]
    assert chapters[1]["start_time"] == 90.0


def test_normalize_drops_intro_from_ytdlp():
    info = {
        "chapters": [
            {"start_time": 0.0, "end_time": 30.0, "title": "Intro"},
            {"start_time": 30.0, "end_time": 90.0, "title": "Housing"},
        ],
    }
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["Housing"]


def test_normalize_drops_intro_from_description():
    info = {"chapters": [], "description": "00:00 Branding\n01:30 Real topic\n02:30 Another"}
    chapters = normalize_chapters(info)
    assert [c["title"] for c in chapters] == ["Real topic", "Another"]


def test_normalize_no_chapters_no_timestamps_returns_empty():
    assert normalize_chapters({"chapters": [], "description": "just prose"}) == []
    assert normalize_chapters({}) == []


def test_normalize_coerces_partial_ytdlp_entries():
    # yt-dlp entries sometimes omit end_time; title may be missing.
    info = {"chapters": [{"start_time": 5.0, "title": "A"}, {"start_time": 12.0}]}
    chapters = normalize_chapters(info)
    assert chapters[0] == {"start_time": 5.0, "end_time": None, "title": "A"}
    assert chapters[1]["title"] == ""
    assert chapters[1]["start_time"] == 12.0

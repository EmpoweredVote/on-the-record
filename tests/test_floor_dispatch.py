from pathlib import Path

from src import govinfo

FIX = Path(__file__).parent / "fixtures" / "govinfo"


def _fake_paginating_fetch():
    """URL -> fixture text, following the real two-page topology.

    NOTE: adapted from the brief's proposed single-page `fake_fetch` (which
    returns the same fixture text regardless of URL). granules_page1.json
    (this repo's actual fixture) carries a real `nextPage`, so a fetch that
    ignores the URL loops forever feeding the same page back to
    `_list_matching_granule_ids`. This mirrors the two-page fake_fetch already
    used in tests/test_govinfo.py: page1 until `offsetMark=PAGE2` is
    requested, then page2 (whose nextPage is null, terminating pagination).
    """
    page1 = (FIX / "granules_page1.json").read_text()
    page2 = (FIX / "granules_page2.json").read_text()

    def fetch(url):
        if "offsetMark=PAGE2" in url:
            return page2
        return page1
    return fetch


def test_has_chamber_data_true_for_house_when_granules_present():
    calls = []
    inner = _fake_paginating_fetch()

    def fake_fetch(url):
        calls.append(url)
        return inner(url)
    assert govinfo.has_chamber_data("2026-07-16", "house",
                                    fetch=fake_fetch, api_key="k") is True
    assert len(calls) == 2  # page1 (has nextPage) then page2 (terminates)


def test_has_chamber_data_false_when_no_record():
    def fake_fetch(url):
        raise RuntimeError("404 no package")  # recess day
    assert govinfo.has_chamber_data("2026-12-25", "house",
                                    fetch=fake_fetch, api_key="k") is False


from src import floor_dispatch as fd


def test_parse_floor_date_extracts_iso_date():
    title = "US House Floor Proceedings (Thursday, September 4, 2026)"
    assert fd._parse_floor_date(title) == "2026-09-04"


def test_parse_floor_date_returns_none_for_unrelated_title():
    assert fd._parse_floor_date("Weekly leadership press conference") is None


def test_list_floor_videos_maps_titles_to_dated_videos():
    fake_info = {"entries": [
        {"title": "US House Floor Proceedings (Thursday, September 4, 2026)",
         "url": "https://youtu.be/aaa"},
        {"title": "Some hearing clip", "url": "https://youtu.be/bbb"},
    ]}
    def fake_extractor(url, *, cookies_file=None):
        return fake_info
    vids = fd.list_floor_videos("https://youtube.com/@USHouseClerk/streams",
                                extractor=fake_extractor)
    assert [(v.date, v.url) for v in vids] == [("2026-09-04", "https://youtu.be/aaa")]


def test_discover_sessions_filters_since_dedupe_and_house():
    videos = [
        fd.FloorVideo(date="2026-09-04", url="u4", title="t4"),
        fd.FloorVideo(date="2026-09-03", url="u3", title="t3"),  # already processed
        fd.FloorVideo(date="2026-08-01", url="u1", title="t1"),  # before since
        fd.FloorVideo(date="2026-09-02", url="u2", title="t2"),  # recess: no house data
    ]
    existing = {"2026-09-03-house-floor"}
    def has_house(date):
        return date != "2026-09-02"
    got = fd.discover_sessions(
        since="2026-09-01", existing_slugs=existing, has_house=has_house, videos=videos)
    assert [v.date for v in got] == ["2026-09-04"]


def test_already_processed_matches_any_floor_slug_on_that_date():
    assert fd._already_processed("2026-09-03", {"2026-09-03-house-floor"}) is True
    assert fd._already_processed("2026-09-03", {"2026-09-03-city-council"}) is False
    assert fd._already_processed("2026-09-04", {"2026-09-03-house-floor"}) is False


def test_dispatch_builds_expected_command_and_returns_code():
    captured = {}
    class Result:  # mimic subprocess.CompletedProcess
        returncode = 0
    def fake_runner(argv, **kwargs):
        captured["argv"] = argv
        return Result()
    code = fd.dispatch(fd.FloorVideo(date="2026-09-04", url="https://youtu.be/aaa", title="t"),
                       runner=fake_runner)
    assert code == 0
    argv = captured["argv"]
    assert "run_local.py" in argv[1]
    assert "--publish-as-draft" in argv
    assert "--congressional-record" in argv
    i = argv.index("--congressional-record")
    assert argv[i + 1] == "2026-09-04" and argv[i + 2] == "house"
    assert "--compute" in argv and argv[argv.index("--compute") + 1] == "modal"
    assert "--no-review" in argv

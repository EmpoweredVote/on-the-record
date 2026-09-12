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


def test_date_range_is_inclusive_and_newest_first():
    assert fd._date_range("2026-09-01", "2026-09-04") == [
        "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01",
    ]


def test_date_range_single_day():
    assert fd._date_range("2026-09-04", "2026-09-04") == ["2026-09-04"]


def test_already_processed_matches_any_floor_slug_on_that_date():
    assert fd._already_processed("2026-09-03", {"2026-09-03-house-floor"}) is True
    assert fd._already_processed("2026-09-03", {"2026-09-03-city-council"}) is False
    assert fd._already_processed("2026-09-04", {"2026-09-03-house-floor"}) is False


def test_discover_sessions_filters_since_dedupe_cdn_and_house():
    existing = {"2026-09-03-house-floor"}

    def has_cdn(date):
        return date != "2026-09-01"  # no CDN stream that day

    def has_house(date):
        return date != "2026-09-02"  # recess: no CREC data that day

    got = fd.discover_sessions(
        since="2026-09-01",
        until="2026-09-04",
        existing_slugs=existing,
        has_cdn=has_cdn,
        has_house=has_house,
    )
    # 09-04: survives. 09-03: already processed. 09-02: no house data.
    # 09-01: no cdn. Only 09-04 survives, newest-first order preserved.
    assert got == ["2026-09-04"]


def test_discover_sessions_returns_empty_when_all_filtered():
    got = fd.discover_sessions(
        since="2026-09-01",
        until="2026-09-01",
        existing_slugs=set(),
        has_cdn=lambda d: False,
        has_house=lambda d: True,
    )
    assert got == []


def test_dispatch_builds_expected_command_and_returns_code():
    captured = {}
    class Result:  # mimic subprocess.CompletedProcess
        returncode = 0
    def fake_runner(argv, **kwargs):
        captured["argv"] = argv
        return Result()
    code = fd.dispatch("2026-09-04", runner=fake_runner)
    assert code == 0
    argv = captured["argv"]
    assert "run_local.py" in argv[1]
    assert argv[2:] == [
        "--house-floor", "2026-09-04",
        "--compute", "modal",
        "--no-review",
        "--publish-as-draft",
    ]
    assert "--input" not in argv
    assert "--event-kind" not in argv
    assert "--meeting-type" not in argv
    assert "--congressional-record" not in argv
    assert "--cookies" not in argv


def test_dispatch_returns_nonzero_runner_code():
    class Result:
        returncode = 3
    def fake_runner(argv, **kwargs):
        return Result()
    assert fd.dispatch("2026-09-04", runner=fake_runner) == 3

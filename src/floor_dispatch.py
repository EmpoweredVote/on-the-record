"""Discover new US House floor sessions and dispatch the CREC oracle pipeline.

Run weekly, off-Mac (GitHub Actions). For each calendar date in the lookback
window that (a) has a House Clerk CDN HLS stream (`src.house_cdn.resolve_session`),
(b) has House Congressional Record data (`src.govinfo.has_chamber_data`), and
(c) is not already in the meetings database, run run_local.py once (via
`--house-floor DATE`) as a subprocess to process it and publish it as a
not-live draft.

The Clerk CDN serves public-domain HLS that ffmpeg fetches directly with no
bot-check and no cookies, so this module no longer touches YouTube/yt-dlp.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
from typing import Callable, Optional

from src import govinfo
from src import house_cdn
from src.publish import existing_meeting_slugs


def _date_range(since: str, until: str) -> list[str]:
    """ISO dates from `until` down to `since`, inclusive, newest first."""
    start = _dt.date.fromisoformat(since)
    end = _dt.date.fromisoformat(until)
    out: list[str] = []
    d = end
    while d >= start:
        out.append(d.isoformat())
        d -= _dt.timedelta(days=1)
    return out


def _already_processed(date: str, slugs: set[str]) -> bool:
    """True if any existing meeting slug is a floor meeting on this date."""
    return any(s.startswith(date) and "floor" in s for s in slugs)


def discover_sessions(
    *,
    since: str,
    until: str,
    existing_slugs: set[str],
    has_cdn: Callable[[str], bool],
    has_house: Callable[[str], bool],
) -> list[str]:
    """Dates in range with a CDN stream + House CREC data, not yet processed.

    Newest first (matches `_date_range` order).
    """
    out: list[str] = []
    for date in _date_range(since, until):
        if _already_processed(date, existing_slugs):
            continue
        if not has_cdn(date):
            continue
        if not has_house(date):
            continue
        out.append(date)
    return out


def dispatch(
    date: str,
    *,
    runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> int:
    """Run run_local.py --house-floor for one session, publishing it as a draft.

    Returns the subprocess exit code.
    """
    run_local = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run_local.py")
    argv = [
        sys.executable, run_local,
        "--house-floor", date,
        "--compute", "modal",
        "--no-review",
        "--publish-as-draft",
    ]
    result = runner(argv, check=False)
    return int(getattr(result, "returncode", 1))


def _default_since(lookback_days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly House-floor discovery + dispatch")
    parser.add_argument("--since", default=None,
                        help="Only sessions on/after this YYYY-MM-DD "
                             "(default: today minus --lookback-days).")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--max-sessions", type=int, default=6,
                        help="Cap the number of sessions dispatched per run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; do not dispatch.")
    args = parser.parse_args(argv)

    since = args.since or _default_since(args.lookback_days)
    until = _dt.date.today().isoformat()

    sessions = discover_sessions(
        since=since,
        until=until,
        existing_slugs=existing_meeting_slugs(),
        has_cdn=lambda d: house_cdn.resolve_session(d) is not None,
        has_house=lambda d: govinfo.has_chamber_data(d, "house"),
    )
    sessions = sessions[: args.max_sessions]

    print(f"Discovered {len(sessions)} new House-floor session(s) since {since}.")
    for date in sessions:
        print(f"  {date}")
    if args.dry_run or not sessions:
        return 0

    failures = 0
    for date in sessions:
        print(f"\n=== Dispatching {date} ===")
        code = dispatch(date)
        if code != 0:
            failures += 1
            print(f"  FAILED ({date}) exit={code}")
    print(f"\nDone: {len(sessions) - failures} ok, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

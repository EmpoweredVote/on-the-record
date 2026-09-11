"""Discover new US House floor sessions and dispatch the CREC oracle pipeline.

Run weekly, off-Mac (GitHub Actions). For each House Clerk floor video that
(a) is newer than the lookback window, (b) has House Congressional Record data,
and (c) is not already in the meetings database, run run_local.py once as a
subprocess to process it and publish it as a not-live draft.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from src import govinfo
from src.publish import existing_meeting_slugs

DEFAULT_CHANNEL_URL = "https://www.youtube.com/@USHouseClerk/streams"

# "US House Floor Proceedings (Thursday, September 4, 2026)" → the inner date.
_FLOOR_DATE_RE = re.compile(r"\(([A-Za-z]+,\s*[A-Za-z]+\s+\d{1,2},\s*\d{4})\)")


@dataclass
class FloorVideo:
    date: str   # YYYY-MM-DD
    url: str
    title: str


def _parse_floor_date(title: str) -> Optional[str]:
    """Extract the ISO session date from a House Clerk floor-video title, or None."""
    if "floor proceedings" not in title.lower():
        return None
    m = _FLOOR_DATE_RE.search(title)
    if not m:
        return None
    try:
        # "Thursday, September 4, 2026" → drop the weekday, parse the rest.
        _weekday, rest = m.group(1).split(",", 1)
        return _dt.datetime.strptime(rest.strip(), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _default_channel_extractor(url: str, *, cookies_file: Optional[str] = None) -> dict:
    """Flat-list a channel's videos via yt-dlp (metadata only, no download)."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
            "js_runtimes": {"node": {}}}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def list_floor_videos(
    channel_url: str,
    *,
    cookies_file: Optional[str] = None,
    extractor: Callable[..., dict] = _default_channel_extractor,
) -> list[FloorVideo]:
    """All dated House floor videos on the channel, newest-first order preserved."""
    info = extractor(channel_url, cookies_file=cookies_file)
    videos: list[FloorVideo] = []
    for entry in info.get("entries") or []:
        title = entry.get("title") or ""
        date = _parse_floor_date(title)
        url = entry.get("url") or entry.get("webpage_url")
        if date and url:
            videos.append(FloorVideo(date=date, url=url, title=title))
    return videos


def _already_processed(date: str, slugs: set[str]) -> bool:
    """True if any existing meeting slug is a floor meeting on this date."""
    return any(s.startswith(date) and "floor" in s for s in slugs)


def discover_sessions(
    *,
    since: str,
    existing_slugs: set[str],
    has_house: Callable[[str], bool],
    videos: list[FloorVideo],
) -> list[FloorVideo]:
    """Videos on/after `since`, not already processed, with House CREC data."""
    out: list[FloorVideo] = []
    for v in videos:
        if v.date < since:
            continue
        if _already_processed(v.date, existing_slugs):
            continue
        if not has_house(v.date):
            continue
        out.append(v)
    return out


def dispatch(
    video: FloorVideo,
    *,
    cookies_file: Optional[str] = None,
    runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> int:
    """Run run_local.py for one session, publishing it as a draft. Returns exit code."""
    run_local = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run_local.py")
    argv = [
        sys.executable, run_local,
        "--input", video.url,
        "--date", video.date,
        "--event-kind", "other",
        "--meeting-type", "House Floor",
        "--congressional-record", video.date, "house",
        "--compute", "modal",
        "--no-review",
        "--publish-as-draft",
    ]
    if cookies_file:
        argv += ["--cookies", cookies_file]
    result = runner(argv, check=False)
    return int(getattr(result, "returncode", 1) or 0)


def _default_since(lookback_days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly House-floor discovery + dispatch")
    parser.add_argument("--channel-url", default=DEFAULT_CHANNEL_URL)
    parser.add_argument("--since", default=None,
                        help="Only sessions on/after this YYYY-MM-DD "
                             "(default: today minus --lookback-days).")
    parser.add_argument("--lookback-days", type=int, default=10)
    parser.add_argument("--max-sessions", type=int, default=6,
                        help="Cap the number of sessions dispatched per run.")
    parser.add_argument("--cookies", default=os.environ.get("YT_DLP_COOKIES") or None,
                        help="Netscape cookies file for yt-dlp (datacenter-IP fallback).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan; do not dispatch.")
    args = parser.parse_args(argv)

    since = args.since or _default_since(args.lookback_days)

    videos = list_floor_videos(args.channel_url, cookies_file=args.cookies)
    existing = existing_meeting_slugs()
    sessions = discover_sessions(
        since=since,
        existing_slugs=existing,
        has_house=lambda d: govinfo.has_chamber_data(d, "house"),
        videos=videos,
    )
    sessions = sessions[: args.max_sessions]

    print(f"Discovered {len(sessions)} new House-floor session(s) since {since}.")
    for v in sessions:
        print(f"  {v.date}  {v.url}")
    if args.dry_run or not sessions:
        return 0

    failures = 0
    for v in sessions:
        print(f"\n=== Dispatching {v.date} ===")
        code = dispatch(v, cookies_file=args.cookies)
        if code != 0:
            failures += 1
            print(f"  FAILED ({v.date}) exit={code}")
    print(f"\nDone: {len(sessions) - failures} ok, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

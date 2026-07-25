#!/usr/bin/env python3
"""Backfill thumbnail.jpg for meetings that were published before the GUI
publish path extracted thumbnails.

Early GUI-published meetings never ran the thumbnail step (that lived only in
run_local's terminal publish path), so they have a source video on disk but no
thumbnail.jpg — hence no image in the library. This walks every meeting dir,
finds the ones with a source video but no thumbnail, and runs the shared
`attach_thumbnail` step (extracts thumbnail.jpg locally, and uploads it if
SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are set).

Usage:
    .venv/bin/python backfill_thumbnails.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src import config
from src.thumbnail import attach_thumbnail, find_video_file


def _has_streamable_hls(mdir: Path) -> bool:
    """True when the meeting's source is an HLS .m3u8 (House Clerk) — ffmpeg can
    extract a frame from it even though there's no local video file on disk."""
    named = mdir / "transcript_named.json"
    if not named.exists():
        return False
    try:
        d = json.loads(named.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    url = (d.get("processing_metadata") or {}).get("source_audio_url") or ""
    return url.split("?", 1)[0].lower().endswith(".m3u8")


def meetings_needing_thumbnail(meetings_dir: Path) -> list[Path]:
    """Meeting dirs that have a source video but no thumbnail.jpg, sorted."""
    out: list[Path] = []
    if not meetings_dir.exists():
        return out
    for mdir in sorted(p for p in meetings_dir.iterdir() if p.is_dir()):
        if (mdir / "thumbnail.jpg").exists():
            continue
        if find_video_file(mdir, "") or _has_streamable_hls(mdir):
            out.append(mdir)
    return out


class _ThumbShim:
    """Minimal meeting stand-in for `attach_thumbnail` when the full transcript
    can't be loaded (in-progress or malformed). A local thumbnail only needs the
    video + an id; clip offsets default to the start of the source."""
    def __init__(self, meeting_id: str):
        self.audio_source = ""
        self.clip_start_seconds = None
        self.duration_seconds = 0.0
        self.meeting_id = meeting_id
        self.thumbnail_url = None


def _meeting_for(meeting_id: str):
    """The real Meeting if it loads, else a thumbnail-only shim."""
    from gui.review_api import _load_meeting_ctx

    ctx = _load_meeting_ctx(meeting_id)
    if ctx is not None:
        return ctx[0]
    return _ThumbShim(meeting_id)


def backfill(*, dry_run: bool = False) -> int:
    """Extract a thumbnail for each meeting missing one. Returns the count made."""
    targets = meetings_needing_thumbnail(config.MEETINGS_DIR)
    if not targets:
        print("No meetings need a thumbnail — nothing to do.")
        return 0

    print(f"{len(targets)} meeting(s) missing a thumbnail:")
    made = 0
    for mdir in targets:
        meeting_id = mdir.name
        if dry_run:
            print(f"  [dry-run] would backfill {meeting_id}")
            continue
        meeting = _meeting_for(meeting_id)
        attach_thumbnail(meeting, mdir)
        if getattr(meeting, "thumbnail_url", None) and hasattr(meeting, "to_dict"):
            from gui.review_api import _atomic_write_text
            _atomic_write_text(
                mdir / "transcript_named.json",
                json.dumps(meeting.to_dict(), indent=2),
            )
        if (mdir / "thumbnail.jpg").exists():
            made += 1
            print(f"  OK   {meeting_id}")
        else:
            print(f"  FAIL {meeting_id} — extraction produced no thumbnail")
    print(f"Done — {made} thumbnail(s) created.")
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="List the meetings that would be backfilled, without writing.")
    args = ap.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

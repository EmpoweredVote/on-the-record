#!/usr/bin/env python3
"""Correct turn-boundary word bleed in already-processed meetings.

Word→turn assignment used to stop at the raw timestamp assignment, so the last
word of one turn (or the next speaker's opening) could land in the neighbouring
turn — e.g. a moderator's "...in this district." captured at the front of the
next candidate's segment as "district. >> Thank you...". The assignment stage
now runs snap_segment_boundaries to fix this, but meetings processed before that
carry the old, bled transcript_named.json (read by the review UI and by GUI /
CLI publish). This walks every meeting, re-snaps its boundaries, and rewrites
transcript_named.json + re-exports.

Segment count, ids, and start/end times are unchanged (only words move between
adjacent turns), so summary section indices stay valid — no reindex needed.

It does NOT re-publish. It prints which affected meetings are currently live so
you can re-publish them (the publish path pushes the corrected transcript):

    .venv/bin/python run_local.py --republish-all      # resync all live meetings

Usage:
    .venv/bin/python backfill_boundary_snap.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src import config
from src.models import Meeting
from src.word_assign import snap_segment_boundaries

try:  # best-effort live flagging; never a hard dependency
    from gui.publish_api import live_published_slugs
except Exception:  # pragma: no cover - import guard
    def live_published_slugs():
        return None


def snap_meeting(meeting) -> bool:
    """Re-snap boundary words and rebuild segment text in place.

    Returns True if any word changed segment, False if the meeting was already
    clean (so the caller can skip rewriting untouched files)."""
    before = [[w.word for w in s.words] for s in meeting.segments]
    snap_segment_boundaries(meeting.segments)
    changed = False
    for seg, prev_words in zip(meeting.segments, before):
        now_words = [w.word for w in seg.words]
        if now_words != prev_words:
            changed = True
        seg.text = " ".join(now_words)
    return changed


def _load(meeting_dir: Path):
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        return Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None


def backfill(*, dry_run: bool = False) -> int:
    """Re-snap every meeting whose transcript_named.json has boundary bleed.
    Returns the number of meetings changed."""
    meetings_dir = config.MEETINGS_DIR
    if not meetings_dir.exists():
        print("No meetings directory — nothing to do.")
        return 0

    changed = 0
    for mdir in sorted(p for p in meetings_dir.iterdir() if p.is_dir()):
        meeting = _load(mdir)
        if meeting is None or not meeting.segments:
            continue
        if not snap_meeting(meeting):
            continue  # already clean
        changed += 1
        if dry_run:
            print(f"  [dry-run] {mdir.name}: boundary words re-snapped")
            continue
        from gui.review_api import _atomic_write_text
        _atomic_write_text(mdir / "transcript_named.json",
                           json.dumps(meeting.to_dict(), indent=2))
        try:
            from src.export import export_all
            export_all(meeting, mdir / "exports")
        except Exception as exc:  # exports regenerate at publish; never block
            print(f"    (export refresh skipped for {mdir.name}: {exc})")
        print(f"  {mdir.name}: boundary words re-snapped")

    if not changed:
        print("No meetings needed re-snapping.")
        return 0

    # Flag which changed meetings are live so the user can re-publish them.
    if not dry_run:
        try:
            live = live_published_slugs()
        except Exception:
            live = None
        if live:
            live_touched = [p.name for p in sorted(meetings_dir.iterdir())
                            if p.is_dir() and p.name in live]
            if live_touched:
                print("\nRe-publish these (they are live and were re-snapped):")
                for name in live_touched:
                    print(f"    - {name}")
        elif live is None:
            print("\n(DB not configured — set DATABASE_URL to list which are live. "
                  "Re-publish with: python run_local.py --republish-all)")
    print(f"\nDone — {changed} meeting(s) re-snapped.")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing.")
    args = ap.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

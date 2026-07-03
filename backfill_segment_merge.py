#!/usr/bin/env python3
"""Re-merge adjacent same-speaker segments in already-processed meetings.

The pipeline's segment merge used to run only in memory at export time, so
transcript_named.json (read by the review UI and by GUI publish) kept the
un-merged, fragmented segments. This walks every meeting, merges its segments,
reindexes summary sections from their (stable) times, and rewrites
transcript_named.json + re-exports.

It does NOT re-publish. It prints which affected meetings are currently live so
you can re-publish them (the fixed publish path will push the merged transcript).

Usage:
    .venv/bin/python backfill_segment_merge.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src import config
from src.identify import merge_adjacent_segments
from src.models import Meeting


def reindex_summary_sections(meeting) -> None:
    """Recompute each summary section's start_segment/end_segment from its
    start_time/end_time against ``meeting.segments``.

    Segment start/end *times* are stable across a segment merge, but the stored
    segment *indices* are not — so after re-merging segments, the section indices
    must be recomputed or they point at the wrong rows. (In a fresh pipeline run
    this isn't needed: the merge happens before summary, so section indices are
    computed against the merged segments natively.) No-op when there's no summary
    or no segments. Times are the source of truth."""
    summary = getattr(meeting, "summary", None)
    sections = getattr(summary, "sections", None) if summary else None
    segs = getattr(meeting, "segments", None)
    if not sections or not segs:
        return
    eps = 1e-6
    for sec in sections:
        start_idx, end_idx = 0, 0
        for i, s in enumerate(segs):
            if s.start_time <= sec.start_time + eps:
                start_idx = i
            if s.start_time <= sec.end_time + eps:
                end_idx = i
        sec.start_segment = start_idx
        sec.end_segment = max(start_idx, end_idx)


def remerge_meeting(meeting) -> tuple[int, int]:
    """Merge segments + reindex sections. Returns (before_count, after_count)."""
    before = len(meeting.segments)
    meeting.segments = merge_adjacent_segments(meeting.segments)
    reindex_summary_sections(meeting)
    return before, len(meeting.segments)


def _load(meeting_dir: Path):
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        return Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None


def backfill(*, dry_run: bool = False) -> int:
    """Re-merge every meeting whose transcript_named.json has fragmented segments.
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
        before, after = remerge_meeting(meeting)
        if after == before:
            continue  # already merged
        changed += 1
        if dry_run:
            print(f"  [dry-run] {mdir.name}: {before} -> {after} segments")
            continue
        from gui.review_api import _atomic_write_text
        _atomic_write_text(mdir / "transcript_named.json",
                           json.dumps(meeting.to_dict(), indent=2))
        try:
            from src.export import export_all
            export_all(meeting, mdir / "exports")
        except Exception as exc:  # exports regenerate at publish; never block
            print(f"    (export refresh skipped for {mdir.name}: {exc})")
        print(f"  {mdir.name}: {before} -> {after} segments")

    if not changed:
        print("No meetings needed re-merging.")
        return 0

    # Flag which changed meetings are live so the user can re-publish them.
    if not dry_run:
        try:
            from gui.publish_api import live_published_slugs
            live = live_published_slugs()
        except Exception:
            live = None
        if live:
            print("\nRe-publish these (they are live and were re-merged):")
            for mdir in sorted(p for p in meetings_dir.iterdir() if p.is_dir()):
                if mdir.name in live:
                    print(f"    - {mdir.name}")
    print(f"\nDone — {changed} meeting(s) re-merged.")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing.")
    args = ap.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

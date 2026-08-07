#!/usr/bin/env python3
"""Repair section order and inverted ranges in already-generated summaries.

Both classifier paths in src/summarize.py returned the model's JSON as-is, and
neither prompt required sections to be ordered or well-formed. src/summarize.py
now normalizes classifier output before summarizing, so new runs are covered;
this repairs the summaries generated before that guard existed.

Two defects, one meeting each across the corpus:

*Out of chronological order* — `2025-10-06-interview` had its middle topic
emitted after the final one (segments 46-80, appended after 110-127). Everything
downstream treats list order as document order (web/lib/outline.ts says so
explicitly), so the live meeting page's topic outline ended by jumping back to
10.8 minutes.

*Inverted range* — `2026-06-24-cd1-republican-primary-debate` had a section with
`end_segment` 4 below `start_segment` 5. That yields an empty section transcript,
so it was published with a title and no content at all.

Only segment boundaries and list order change. Times, titles and content are left
exactly as generated, so this cannot alter what any summary says. It also cannot
backfill the content an inverted section never got — that needs a fresh summary
run. Overlap is deliberately left alone: sections legitimately overlap where a
merged segment straddles a topic boundary, and a compilation interview that puts
the same question to candidate after candidate genuinely interleaves topics that
no contiguous partition can express.

Both on-disk copies are rewritten: the summary embedded in transcript_named.json
(authoritative, and what publish reads) and the standalone summary.json checkpoint
that run_local's resume path loads back into meeting.summary.

It does NOT re-publish. It prints which repaired meetings are live.

Usage:
    .venv/bin/python backfill_summary_section_order.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src import config
from src.atomic_io import atomic_write_json
from src.models import Meeting
from src.summary_sections import normalize_raw_sections, normalize_sections


def repair_meeting(meeting) -> tuple[int, bool]:
    """Sort the meeting's summary sections and clamp inverted ranges, in place.
    Returns (ranges_clamped, order_changed)."""
    summary = getattr(meeting, "summary", None)
    sections = getattr(summary, "sections", None) if summary else None
    if not sections:
        return 0, False
    ordered, clamped, moved = normalize_sections(sections)
    summary.sections = ordered
    return clamped, moved


def repair_summary_json(meeting_dir: Path) -> tuple[int, bool] | None:
    """Apply the same repair to the standalone summary.json, preserving every
    other key on each section. None when there is no readable summary.json."""
    sfile = meeting_dir / "summary.json"
    if not sfile.exists():
        return None
    try:
        summary = json.loads(sfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"    (summary.json unreadable for {meeting_dir.name}: {exc})")
        return None
    raw = summary.get("sections") or []
    if not raw:
        return None
    ordered, clamped, moved = normalize_raw_sections(raw)
    if clamped or moved:
        summary["sections"] = ordered
        atomic_write_json(sfile, summary)
    return clamped, moved


def _load(meeting_dir: Path):
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        return Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None


def backfill(*, dry_run: bool = False) -> int:
    """Repair every meeting whose sections are out of order or inverted.
    Returns the number of meetings changed."""
    meetings_dir = config.MEETINGS_DIR
    if not meetings_dir.exists():
        print("No meetings directory — nothing to do.")
        return 0

    changed = 0
    touched: list[str] = []
    for mdir in sorted(p for p in meetings_dir.iterdir() if p.is_dir()):
        meeting = _load(mdir)
        if meeting is None:
            continue
        clamped, moved = repair_meeting(meeting)
        if not (clamped or moved):
            continue
        changed += 1
        touched.append(mdir.name)
        notes = []
        if moved:
            notes.append("reordered chronologically")
        if clamped:
            notes.append(f"{clamped} inverted range(s) clamped")
        detail = ", ".join(notes)
        if dry_run:
            print(f"  [dry-run] {mdir.name}: {detail}")
            continue
        atomic_write_json(mdir / "transcript_named.json", meeting.to_dict())
        if repair_summary_json(mdir):
            detail += ", summary.json repaired"
        try:
            from src.export import export_all
            export_all(meeting, mdir / "exports")
        except Exception as exc:  # exports regenerate at publish; never block
            print(f"    (export refresh skipped for {mdir.name}: {exc})")
        print(f"  {mdir.name}: {detail}")

    if not changed:
        print("No summaries needed section repair.")
        return 0

    if not dry_run:
        try:
            from gui.publish_api import live_published_slugs
            live = live_published_slugs()
        except Exception:
            live = None
        if live:
            to_republish = [s for s in touched if s in live]
            if to_republish:
                print("\nRe-publish these (they are live and were repaired):")
                for slug in to_republish:
                    print(f"    - {slug}")
            else:
                print("\nNone of the repaired meetings are live.")
    print(f"\nDone — {changed} meeting(s) repaired.")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing.")
    args = ap.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

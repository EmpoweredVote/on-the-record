#!/usr/bin/env python3
"""Re-derive stale summary.json section boundaries from transcript_named.json.

backfill_segment_merge.py renumbered every meeting's segments and reindexed the
summary sections *embedded* in transcript_named.json (from their stable times),
but never rewrote the standalone summary.json checkpoint — so a subset of
meetings have a summary.json whose start_segment/end_segment values still index
into the pre-merge segment numbering. That file is read back into
meeting.summary whenever run_local resumes past the SUMMARIZED stage, and it is
the gold data for the summary-classification eval, so stale boundaries there
are live hazards, not dead artifacts.

For each meeting where the standalone sections are stale but the embedded copy
still indexes into the current transcript, this copies start_segment/
end_segment from the embedded copy onto the matching standalone section
(sections are matched pairwise — same order, section_type, title, content, and
times — anything else is logged and skipped). Meetings where even the embedded
copy drifted are logged for summary regeneration and left untouched.

Validity here means: every section's start/end_segment is a segment_id present
in the meeting's current transcript_named.json (all segments, not just
text-bearing ones — reindex-by-time can legitimately land a boundary on an
empty-text segment).

Publish is NOT affected by this fix: both publish paths (gui.publish_api and
run_local --publish-meeting) load the Meeting from transcript_named.json, so
live rows already carry the corrected embedded copy. The exception is a full
pipeline resume (run_local stage 5 loads summary.json into meeting.summary
before the stage-7 publish), which would have pushed stale boundaries.

Usage:
    .venv/bin/python backfill_summary_sections.py [--dry-run] [--verify-only]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:  # detection logic shipped on feat/summary-model-evals
    from src.summary_classify_eval import gold_sections_valid
except ImportError:  # pre-merge fallback — same semantics; delete once merged
    def gold_sections_valid(gold_sections: list[dict], all_segment_ids: set) -> tuple:
        if not gold_sections:
            return False, "no gold sections"
        for sec in gold_sections:
            start, end = sec.get("start_segment"), sec.get("end_segment")
            if start is None or end is None:
                return False, "gold section missing start_segment/end_segment"
            if start not in all_segment_ids or end not in all_segment_ids:
                return False, (
                    f"gold segment range [{start},{end}] outside this "
                    "transcript's current segment ids"
                )
            # Membership alone passes an inverted range: both ids are real, but
            # end < start describes no segments at all.
            if end < start:
                return False, (
                    f"gold segment range [{start},{end}] is inverted "
                    "(end_segment below start_segment)"
                )
        return True, ""


def sections_stale(sections: list[dict], valid_ids: set) -> bool:
    """True when any section's boundaries fall outside the current transcript, or
    name an inverted range (end_segment below start_segment) — both mean the
    boundaries don't describe this transcript and need refitting."""
    ok, _ = gold_sections_valid(sections, valid_ids)
    return not ok


def _same_section(a: dict, b: dict) -> bool:
    """Same section apart from its (possibly renumbered) segment boundaries."""
    return all(a.get(k) == b.get(k)
               for k in ("section_type", "title", "content", "start_time", "end_time"))


def refit_sections(standalone: list[dict], embedded: list[dict]):
    """Copy start/end_segment from each embedded section onto its standalone
    twin. Returns the number of sections whose boundaries changed, or None
    (mutating nothing) when the two lists don't pair up — that summary was
    edited or regenerated independently and can't be safely refit."""
    if len(standalone) != len(embedded):
        return None
    if not all(_same_section(s, e) for s, e in zip(standalone, embedded)):
        return None
    changed = 0
    for s, e in zip(standalone, embedded):
        new = (e.get("start_segment"), e.get("end_segment"))
        if (s.get("start_segment"), s.get("end_segment")) != new:
            s["start_segment"], s["end_segment"] = new
            changed += 1
    return changed


def _iter_meeting_dirs(meetings_dir: Path):
    return sorted(p for p in meetings_dir.iterdir() if p.is_dir())


def _load_pair(mdir: Path):
    """(transcript_dict, summary_dict) or None when either file is absent/broken."""
    named, sfile = mdir / "transcript_named.json", mdir / "summary.json"
    if not named.exists() or not sfile.exists():
        return None
    try:
        return (json.loads(named.read_text(encoding="utf-8")),
                json.loads(sfile.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ! {mdir.name}: unreadable artifacts ({exc}) — skipping")
        return None


def verify(meetings_dir: Path) -> list[str]:
    """Meetings whose standalone summary.json still fails gold_sections_valid."""
    failing = []
    for mdir in _iter_meeting_dirs(meetings_dir):
        pair = _load_pair(mdir)
        if pair is None:
            continue
        transcript, summary = pair
        sections = summary.get("sections") or []
        if not sections:
            continue
        valid_ids = {s.get("segment_id") for s in transcript.get("segments") or []}
        ok, reason = gold_sections_valid(sections, valid_ids)
        if not ok:
            failing.append(mdir.name)
            print(f"  STALE {mdir.name}: {reason}")
    return failing


def backfill(*, dry_run: bool = False) -> dict:
    from src import config

    meetings_dir = config.MEETINGS_DIR
    stats = {"ok": 0, "fixed": [], "drifted": [], "mismatched": [], "still_stale": []}
    if not meetings_dir.exists():
        print("No meetings directory — nothing to do.")
        return stats

    for mdir in _iter_meeting_dirs(meetings_dir):
        pair = _load_pair(mdir)
        if pair is None:
            continue
        transcript, summary = pair
        standalone = summary.get("sections") or []
        if not standalone:
            continue
        valid_ids = {s.get("segment_id") for s in transcript.get("segments") or []}
        if not sections_stale(standalone, valid_ids):
            stats["ok"] += 1
            continue

        embedded = (transcript.get("summary") or {}).get("sections") or []
        if sections_stale(embedded, valid_ids):
            stats["drifted"].append(mdir.name)
            print(f"  DRIFTED {mdir.name}: embedded copy is stale too — "
                  "needs summary regeneration, not refit")
            continue

        changed = refit_sections(standalone, embedded)
        if changed is None:
            stats["mismatched"].append(mdir.name)
            print(f"  MISMATCH {mdir.name}: standalone and embedded sections "
                  "don't pair up — skipping")
            continue

        stats["fixed"].append(mdir.name)
        if dry_run:
            print(f"  [dry-run] {mdir.name}: would refit {changed} section boundarie(s)")
            continue
        from src.atomic_io import atomic_write_json
        atomic_write_json(mdir / "summary.json", summary)
        print(f"  {mdir.name}: refit {changed} section boundarie(s)")

    print(f"\nRe-verifying corpus{' (dry-run: fixes not applied)' if dry_run else ''}...")
    stats["still_stale"] = verify(meetings_dir)

    print(f"\nDone — {stats['ok']} already valid, {len(stats['fixed'])} "
          f"{'would be ' if dry_run else ''}refit, {len(stats['drifted'])} need "
          f"summary regeneration, {len(stats['mismatched'])} mismatched, "
          f"{len(stats['still_stale'])} still stale after this run.")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Refit stale summary.json section boundaries from the "
                    "embedded copy in transcript_named.json.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing.")
    ap.add_argument("--verify-only", action="store_true",
                    help="Only run the gold_sections_valid sweep; change nothing.")
    args = ap.parse_args()
    if args.verify_only:
        from src import config
        failing = verify(config.MEETINGS_DIR)
        print(f"\n{len(failing)} meeting(s) with stale summary.json.")
        return
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

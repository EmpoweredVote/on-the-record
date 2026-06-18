#!/usr/bin/env python3
"""Repair stale politician links left on speakers by pre-fix renames.

Before the rename-clears-stale-link fix, a human name correction left the prior
(wrong) politician_slug attached. This re-derives each LINKED speaker's
politician_slug/id from its (authoritative) speaker_name via the meeting's
roster, but ONLY for mappings whose name is inconsistent with the stored slug
(name tokens share nothing with slug tokens). Correct and manually-pasted links
are left untouched.

Dry-run by default. Pass --apply to write (each changed file is backed up first).

  .venv/bin/python bench/repair_stale_links.py            # dry-run, all meetings
  .venv/bin/python bench/repair_stale_links.py --apply
  .venv/bin/python bench/repair_stale_links.py 2026-02-25-council --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import config
from src.enroll import resolve_enrollment_key

_STOP = {
    "councilmember", "council", "president", "vice", "mayor", "clerk", "the",
    "of", "common", "city", "member", "district", "association", "office",
}
_SLUG_NOISE = {"h", "j", "s", "the", "of"}


def _name_tokens(s: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()) - _STOP


def _slug_tokens(slug: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", (slug or "").lower()).split()) - _SLUG_NOISE


def _inconsistent(name: str, slug: str) -> bool:
    nt, st = _name_tokens(name), _slug_tokens(slug)
    return bool(nt) and bool(st) and not (nt & st)


def _roster_for(meeting_dir: Path):
    body_slug = None
    state = meeting_dir / "pipeline_state.json"
    if state.exists():
        try:
            body_slug = json.loads(state.read_text()).get("body_slug")
        except Exception:
            body_slug = None
    try:
        from src.roster import load_roster
        return load_roster(body_slug=body_slug) if body_slug else load_roster()
    except Exception:
        return None


def repair_meeting(meeting_dir: Path, apply: bool) -> list[tuple]:
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return []
    data = json.loads(named.read_text())
    speakers = data.get("speakers", {})
    roster = _roster_for(meeting_dir)

    changes = []
    for label, m in speakers.items():
        slug = m.get("politician_slug")
        name = m.get("speaker_name") or ""
        if not slug or not name:
            continue
        if not _inconsistent(name, slug):
            continue
        # Re-derive from the authoritative name. None roster -> clear (unlink).
        new_slug, new_id = None, None
        if roster is not None:
            _key, new_slug, new_id = resolve_enrollment_key(name, roster)
        changes.append((label, name, slug, new_slug))
        if apply:
            if new_slug:
                m["politician_slug"], m["politician_id"] = new_slug, new_id
            else:
                m.pop("politician_slug", None)
                m.pop("politician_id", None)

    if apply and changes:
        backup = named.with_suffix(".json.stale-link.bak")
        if not backup.exists():
            backup.write_text(named.read_text(), encoding="utf-8")
        tmp = named.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(named)
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("meeting_ids", nargs="*", help="Specific meetings (default: all)")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = ap.parse_args()

    if args.meeting_ids:
        dirs = [config.MEETINGS_DIR / m for m in args.meeting_ids]
    else:
        dirs = sorted(d for d in config.MEETINGS_DIR.iterdir()
                      if d.is_dir() and not d.name.startswith("."))

    total = 0
    for d in dirs:
        changes = repair_meeting(d, args.apply)
        if changes:
            print(f"=== {d.name} ===")
            for label, name, old, new in changes:
                arrow = new or "(unlinked)"
                print(f"  {label:<11} {name:<28} {old}  ->  {arrow}")
            total += len(changes)

    verb = "Repaired" if args.apply else "Would repair"
    print(f"\n{verb} {total} stale link(s)" + ("" if args.apply else "  (dry-run; pass --apply to write)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

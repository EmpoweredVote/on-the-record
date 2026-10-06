#!/usr/bin/env python
"""Spelling accuracy of the web name lookup on gold witnesses (slice 2).

Web step only: the roster/politician/local_people steps are skipped because
published gold names are already in meetings.local_people (they would leak
the answer). Calls the real `claude` CLI — run on Chris's Mac, logged in.

Usage:
  .venv/bin/python scripts/eval_name_lookup.py --sample 50 --json /tmp/lookup_eval.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.name_candidates import build_candidates  # noqa: E402
from src.name_evidence import extract_evidence  # noqa: E402
from src.name_lookup import (  # noqa: E402
    ResearchCache, ResearchFailed, ResearcherUnavailable, research, should_research, verify_on_page,
)
from src.name_suggest import read_captions_text  # noqa: E402
from src.name_suggestion_eval import gold_labels, score_lookup_rows, strip_names  # noqa: E402


def select_witnesses(meetings_dir: Path, sample: int, seed: int) -> list[dict]:
    """Gold, non-politician speakers whose candidate would be web-researched,
    sampled round-robin across event kinds with a fixed seed."""
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(glob.glob(str(meetings_dir / "*" / "transcript_named.json"))):
        try:
            meeting = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(meeting, dict):
            continue
        gold = {k: v for k, v in gold_labels(meeting).items() if v and not v.startswith("__")}
        if not gold:
            continue
        speakers = meeting.get("speakers") or {}
        mdir = Path(path).parent
        cands = build_candidates(extract_evidence(strip_names(meeting), read_captions_text(mdir)),
                                 meeting.get("event_kind"))
        for label, gname in gold.items():
            sp = speakers.get(label, {}) if isinstance(speakers, dict) else {}
            c = cands.get(label)
            if sp.get("politician_id") or not c or not c.name or c.conflict:
                continue
            if not should_research(c.titled, c.partial, c.affiliation):
                continue
            by_kind[meeting.get("event_kind") or "unknown"].append({
                "meeting": mdir.name, "label": label, "event_kind": meeting.get("event_kind"),
                "gold": gname, "spoken": c.name, "affiliation": c.affiliation,
                "place": meeting.get("city") or None,
            })
    rng = random.Random(seed)
    for items in by_kind.values():
        rng.shuffle(items)
    picked, kinds = [], sorted(by_kind)
    while len(picked) < sample and any(by_kind[k] for k in kinds):
        for k in kinds:
            if by_kind[k] and len(picked) < sample:
                picked.append(by_kind[k].pop())
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings-dir", default=os.path.expanduser("~/CouncilScribe/meetings"))
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cache", default=os.path.expanduser("~/CouncilScribe/config/name_lookup_eval_cache.json"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    rows = select_witnesses(Path(args.meetings_dir), args.sample, args.seed)
    print(f"Selected {len(rows)} gold witnesses")
    cache = ResearchCache(Path(args.cache))
    unavailable = None
    for i, r in enumerate(rows, 1):
        r.update(looked_up=None, verified=False, status="not_found", url=None, reason=None)
        if unavailable:
            r["status"] = "unavailable"
            continue
        key = ResearchCache.key(r["spoken"], r["affiliation"], r["place"])
        cached = cache.get(key)
        try:
            found = (None if cached and cached.get("found") is False else cached) if cached is not None else \
                research(r["spoken"], None, r["affiliation"], r["place"])
        except ResearcherUnavailable as exc:
            unavailable = str(exc)
            r["status"] = "unavailable"
            print(f"  STOP: {unavailable}")
            continue
        except ResearchFailed as exc:
            r["status"] = "failed"
            r["reason"] = str(exc)
            print(f"  [{i}/{len(rows)}] failed       gold={r['gold']!r} spoken={r['spoken']!r} ({exc})")
            continue
        if cached is None:
            cache.put(key, found)
            cache.save()
        if found:
            ok, why = verify_on_page(found["name"], found["url"])
            r.update(looked_up=found["name"], verified=ok, status="verified" if ok else "not_verified",
                     url=found["url"] if ok else None, reason=why)
        print(f"  [{i}/{len(rows)}] {r['status']:<12} gold={r['gold']!r} spoken={r['spoken']!r} -> {r['looked_up']!r}")
    print("\nSummary:", json.dumps(score_lookup_rows(rows), indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Score speaker name suggestions against human_review gold labels.

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md

Usage:
  .venv/bin/python scripts/eval_name_suggestions.py
  .venv/bin/python scripts/eval_name_suggestions.py --kinds council forum --show-wrong 20
  .venv/bin/python scripts/eval_name_suggestions.py --json /tmp/name_eval.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.name_candidates import build_candidates  # noqa: E402
from src.name_evidence import extract_evidence  # noqa: E402
from src.name_suggestion_eval import gold_labels, score_meeting, strip_names, summarize  # noqa: E402

_VTT_TIME = re.compile(r"^\d\d:\d\d:\d\d\.\d+ --> .*$", re.M)
_VTT_TAG = re.compile(r"<[^>]+>")
_VTT_HEADER = re.compile(r"^(WEBVTT.*|NOTE.*|Kind:.*|Language:.*)$", re.M)


def _captions(meeting_dir: Path) -> Optional[str]:
    """Plain caption text: cue timings, inline <..> tags and header lines removed."""
    candidates = [meeting_dir / "source_captions.vtt", meeting_dir / "captions.vtt"]
    candidates += sorted(meeting_dir.glob("captions*.vtt"))
    for p in candidates:
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            text = _VTT_TIME.sub("", text)
            text = _VTT_TAG.sub("", text)
            return _VTT_HEADER.sub("", text)
    return None


def run(meetings_dir: Path, kinds: Optional[list[str]]) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(str(meetings_dir / "*" / "transcript_named.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                meeting = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not isinstance(meeting, dict):
            continue
        kind = meeting.get("event_kind")
        if kinds and kind not in kinds:
            continue
        gold = gold_labels(meeting)
        if not gold:
            continue
        mdir = Path(path).parent
        cands = build_candidates(extract_evidence(strip_names(meeting), _captions(mdir)), kind)
        for r in score_meeting(gold, cands, kind):
            r["meeting"] = mdir.name
            c = cands.get(r["label"])
            r["quotes"] = [f"{e.kind}: {e.quote}" for e in (c.evidence if c else [])][:3]
            rows.append(r)
    return rows


def _table(title: str, summary: dict[str, dict]) -> None:
    print(f"\n{title}")
    cols = ("n", "predicted", "correct", "misspelled", "wrong", "hallucination", "miss",
            "safe_null", "precision", "bad_rate", "exact_rate", "misspell_rate",
            "passes_prefill_bar")
    print("  " + f"{'group':<18}" + "".join(f"{c:>{len(c) + 2}}" for c in cols))
    for group, s in summary.items():
        print("  " + f"{str(group):<18}" + "".join(f"{str(s[c]):>{len(c) + 2}}" for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings-dir", default=os.path.expanduser("~/CouncilScribe/meetings"))
    ap.add_argument("--kinds", nargs="*", default=None, help="limit to these event kinds")
    ap.add_argument("--show-wrong", type=int, default=10, help="print N wrong/hallucination rows")
    ap.add_argument("--json", default=None, help="write all rows to this path")
    args = ap.parse_args()

    rows = run(Path(args.meetings_dir), args.kinds)
    print(f"Scored {len(rows)} gold speaker labels from "
          f"{len({r['meeting'] for r in rows})} meetings")
    _table("By tier (pre-fill bar applies to strong/medium/weak):", summarize(rows, "tier"))
    _table("By event kind:", summarize(rows, "event_kind"))

    bad = [r for r in rows if r["outcome"] in ("wrong", "hallucination")]
    print(f"\nWrong / hallucination examples ({len(bad)} total):")
    for r in bad[: args.show_wrong]:
        print(f"  [{r['tier']}] {r['meeting']} {r['label']}: gold={r['gold']!r} predicted={r['predicted']!r}")
        for q in r["quotes"]:
            print(f"      {q[:140]}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()

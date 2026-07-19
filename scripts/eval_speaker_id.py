#!/usr/bin/env python
"""Compare Layer-3 speaker-ID models against human-labeled interview meetings.

For each meeting and each model, run the REAL identification path with all
speakers unknown, then score predictions against the human_review gold labels.

Usage:
  .venv/bin/python scripts/eval_speaker_id.py --models haiku sonnet
  .venv/bin/python scripts/eval_speaker_id.py --meetings-dir ~/CouncilScribe/meetings
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.event_kinds import INTERVIEW_KINDS
from src.llm_providers import get_provider
from src.llm_utils import llm_identify_speakers
from src.models import Segment
from src.speaker_id_eval import classify, summarize


def _gold_labels(meeting_json: dict) -> dict[str, str]:
    """label -> gold name, for human_review labels only (deduped by first seen)."""
    gold: dict[str, str] = {}
    for s in meeting_json.get("segments", []):
        if s.get("id_method") == "human_review":
            gold.setdefault(s["speaker_label"], s.get("speaker_name"))
    return gold


def _segments(meeting_json: dict) -> list[Segment]:
    return [Segment.from_dict(s) for s in meeting_json.get("segments", [])]


def _load_meetings(meetings_dir: Path) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(glob.glob(str(meetings_dir / "*" / "transcript_named.json"))):
        try:
            data = json.load(open(path))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("event_kind") not in INTERVIEW_KINDS:
            continue
        if not _gold_labels(data):
            continue
        out.append((os.path.basename(os.path.dirname(path)), data))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["haiku", "sonnet"],
                    help="keys from config.SPEAKER_ID_MODELS")
    ap.add_argument("--meetings-dir", default=os.path.expanduser("~/CouncilScribe/meetings"))
    args = ap.parse_args()

    meetings = _load_meetings(Path(args.meetings_dir))
    print(f"Loaded {len(meetings)} labeled interview meetings")

    rows = []
    for model in args.models:
        try:
            provider = get_provider(model)
        except (RuntimeError, KeyError) as e:
            print(f"! skipping {model}: {e}")
            continue

        outcomes: list[str] = []
        t0 = time.time()
        for name, data in meetings:
            gold = _gold_labels(data)
            segments = _segments(data)
            preds = llm_identify_speakers(
                provider, segments, {}, event_kind=data.get("event_kind"),
            )
            for label, gold_name in gold.items():
                pred = preds.get(label)
                outcomes.append(classify(gold_name, pred.speaker_name if pred else None))
        row = summarize(model, outcomes)
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"  {model}: {row}")

    if not rows:
        print("No models ran (missing API keys?).")
        return

    cols = ["model", "n", "accuracy", "correct", "safe_null", "hallucination", "miss", "wrong", "seconds"]
    print("\n| " + " | ".join(cols) + " |")
    print("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")


if __name__ == "__main__":
    main()

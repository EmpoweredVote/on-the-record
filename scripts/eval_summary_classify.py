#!/usr/bin/env python
"""Replay the summary section-classification stage over stored meetings and
score candidate models against the accepted summary.json sections.

For each selected meeting, this re-runs ONLY the classify stage
(classify_sections() / _classify_sections_interview() from src.summarize) —
never the synthesis stage, and never touches the pipeline or its stored
artifacts — and compares the replayed section boundaries to the sections
already recorded in that meeting's accepted summary.json.

Usage:
  .venv/bin/python scripts/eval_summary_classify.py --models current
  .venv/bin/python scripts/eval_summary_classify.py --models current deepseek/deepseek-chat-v3.1 --limit 10
  .venv/bin/python scripts/eval_summary_classify.py --meetings-dir ~/CouncilScribe/meetings --limit 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()  # before src.config so API keys (incl. OPENROUTER_API_KEY) are visible

from src.event_kinds import INTERVIEW_KINDS  # noqa: E402
from src.eval_llm_client import build_eval_client  # noqa: E402
from src.eval_meeting_sampling import discover_meetings, select_diverse_sample  # noqa: E402
from src.models import Meeting  # noqa: E402
from src.summary_classify_eval import (  # noqa: E402
    DEFAULT_BOUNDARY_TOLERANCE,
    aggregate,
    gold_sections_valid,
    score_meeting,
)
from src.summarize import (  # noqa: E402
    _classify_sections_interview,
    _format_chapter_hint,
    _show_notes_hint,
    chapters_to_segment_hints,
    classify_sections,
)

DEFAULT_MEETINGS_DIR = os.path.expanduser("~/CouncilScribe/meetings")

# Why the report splits rather than reporting one number: interview-kind
# meetings are classified by _classify_sections_interview(), whose prompt
# offers exactly one section type ("topic"). Every segment therefore carries
# the same label, so label_agreement is a CONSTANT there — a model that
# collapses every topic into one section still scores ~1.00. Measured
# 2026-08-07 on the 149-meeting corpus, 99 meetings (66%) are interview-kind,
# so a single pooled agreement number is two-thirds noise-free filler.
# Read label_agreement on the multi-label row; read boundary_f1 everywhere.
REPORT_GROUPS = (
    ("all", lambda r: True),
    ("multi-label", lambda r: not r["is_interview"]),
    ("interview", lambda r: r["is_interview"]),
)


def replay_one(client, model_override, meeting: Meeting, gold_sections: list):
    """Returns (score_row, skip_reason). Exactly one is None."""
    segments = [s for s in meeting.segments if s.text]
    valid_ids = {s.segment_id for s in segments}
    # Staleness is "does this id exist in the current transcript", so the gate
    # sees ALL segment ids — backfill_segment_merge.py's reindex-by-time leaves
    # real ids carrying empty text, and a gold boundary landing on one of those
    # is valid, not stale. Scoring below stays on text-bearing ids only (the
    # population actually shown to the classifier).
    all_ids = {s.segment_id for s in meeting.segments}

    ok, reason = gold_sections_valid(gold_sections, all_ids)
    if not ok:
        return None, reason

    is_interview = meeting.event_kind in INTERVIEW_KINDS
    chapter_hint = _format_chapter_hint(
        chapters_to_segment_hints(meeting.processing_metadata.source_chapters or [], segments)
    )
    debug: list = []
    if is_interview:
        raw = _classify_sections_interview(
            client, segments, chapter_hint=chapter_hint + _show_notes_hint(meeting),
            model=model_override, debug=debug,
        )
    else:
        raw = classify_sections(
            client, segments, chapter_hint=chapter_hint, model=model_override, debug=debug,
        )

    parse_failures = sum(1 for d in debug if not d.get("parsed"))
    row = score_meeting(gold_sections, raw, valid_ids, parse_failures=parse_failures)
    # Which vocabulary this meeting was classified under decides whether
    # label_agreement carries any signal — see REPORT_GROUPS.
    row["is_interview"] = is_interview
    row["event_kind"] = meeting.event_kind
    return row, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=["current"],
                     help="'current' (config's Claude classify model) or OpenRouter model ids")
    ap.add_argument("--limit", type=int, default=10,
                     help="number of meetings to sample (deterministic, diverse across event kinds)")
    ap.add_argument("--meetings-dir", default=DEFAULT_MEETINGS_DIR)
    args = ap.parse_args()

    meetings_dir = Path(os.path.expanduser(args.meetings_dir))
    all_meetings = discover_meetings(meetings_dir)
    sample = select_diverse_sample(all_meetings, args.limit)
    print(f"Selected {len(sample)} of {len(all_meetings)} candidate meetings: {sample}\n")

    rows = []
    for model_key in args.models:
        try:
            client, model_override = build_eval_client(model_key)
        except RuntimeError as e:
            print(f"! skipping {model_key}: {e}")
            continue

        meeting_rows = []
        for meeting_id in sample:
            mdir = meetings_dir / meeting_id
            try:
                transcript_data = json.loads((mdir / "transcript_named.json").read_text())
                gold_summary = json.loads((mdir / "summary.json").read_text())
            except (json.JSONDecodeError, OSError) as e:
                print(f"    ! {model_key}/{meeting_id}: could not read artifacts ({e}) — skipping")
                continue
            meeting = Meeting.from_dict(transcript_data)
            gold_sections = gold_summary.get("sections", [])

            try:
                row, skip_reason = replay_one(client, model_override, meeting, gold_sections)
            except Exception as e:  # keep the run alive over flaky API calls
                print(f"    ! {model_key}/{meeting_id}: {e} — skipping meeting")
                continue
            if row is None:
                print(f"    - {model_key}/{meeting_id}: SKIPPED ({skip_reason})")
                continue
            meeting_rows.append(row)
            agree_str = f"{row['agreement']:.2f}" if row["agreement"] is not None else "—"
            print(
                f"    {model_key}/{meeting_id}: agreement={agree_str} "
                f"boundary_f1={row['boundary_f1']:.2f} "
                f"(P{row['boundary_precision']:.2f}/R{row['boundary_recall']:.2f} "
                f"{row['boundary_matched']}/{row['n_gold_boundaries']} gold) "
                f"gold_sections={row['gold_sections']} candidate_sections={row['candidate_sections']} "
                f"delta={row['section_count_delta']} parse_failures={row['parse_failures']}"
            )

        for group_name, keep in REPORT_GROUPS:
            group_rows = [r for r in meeting_rows if keep(r)]
            if not group_rows:
                continue
            agg = aggregate(model_key, group_rows)
            agg["group"] = group_name
            rows.append(agg)

    if not rows:
        print("No models ran (missing API keys?).")
        return 1

    def fmt(value, places=3):
        return f"{value:.{places}f}" if value is not None else "—"

    cols = ["model", "group", "meetings", "segments", "label_agreement",
            "boundary_f1", "boundary_prec", "boundary_recall",
            "avg_section_count_delta", "parse_failures"]
    print("\n| " + " | ".join(cols) + " |")
    print("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        # label_agreement is struck through on the interview row: it cannot vary
        # there, so printing it as a comparable score invites false confidence.
        agree = "n/a (1-label)" if r["group"] == "interview" else fmt(r["label_agreement"])
        print(
            f"| {r['model']} | {r['group']} | {r['meetings']} | {r['segments']} | {agree} | "
            f"{fmt(r['boundary_f1'])} | {fmt(r['boundary_precision'])} | "
            f"{fmt(r['boundary_recall'])} | {fmt(r['avg_section_count_delta'], 2)} | "
            f"{r['parse_failures']} |"
        )

    print("\nlabel_agreement: per-segment section-type match, gold-covered segments only.")
    print("  Not meaningful on interview-kind meetings (single-label vocabulary) — "
          "read boundary_f1 there.")
    print("boundary_f1: section-start placement within "
          f"{DEFAULT_BOUNDARY_TOLERANCE} scored segment(s), one-to-one matched, "
          "micro-averaged.")
    print("  Low recall = missed boundaries (under-segmentation); "
          "low precision = invented ones.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

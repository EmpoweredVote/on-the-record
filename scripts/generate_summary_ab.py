#!/usr/bin/env python
"""Generate a BLIND A/B comparison of the summary SYNTHESIS stage (discussion/
topic prose + executive summary) between candidate models and the reference
(the meeting's already-accepted summary.json).

For each selected meeting and each --models entry, this re-runs ONLY the
synthesis stage over the meeting's ACCEPTED section boundaries (from
summary.json) — never the classify stage, and never touches the pipeline or
its stored artifacts. Non-synthesis section types (roll_call/vote/opening/
closing/procedural) are carried through from the accepted summary unchanged,
so a reviewer comparing two discussion-section rewrites (or two executive
summaries) is judging prose quality, not different section boundaries.

Writes, into --out (default ~/CouncilScribe/eval/summary-ab/<date>/):
  ab_pairs.md      — reviewer-facing: judging instructions + "Option 1" /
                      "Option 2" text pairs in RANDOMIZED order. Group headers
                      read "<meeting_id> — Comparison N" and each pair carries
                      a visible, model-blind id ("pair-7") — no model names or
                      candidate/reference labels anywhere in this file.
  answer_key.json  — withheld from ab_pairs.md; maps each visible pair id (and
                      the internal meeting/pair_key) back to candidate_model
                      and which option was which.

The Option 1/2 order is deterministic per (meeting id, model, pair key) — see
src.summary_ab_pairing.pair_rng — NOT per wall-clock run, so re-running this
script reproduces the same file byte-for-byte given the same inputs. Seeding
includes the model (not just the meeting id) so two candidate models compared
against the same meeting get independent draw sequences — otherwise the
reference text would land in the same option slot for every model, a pattern
a reviewer could learn across a multi-model run.

Usage:
  .venv/bin/python scripts/generate_summary_ab.py --meetings 2025-09-22-press-conference-hans-truelson --models deepseek/deepseek-chat-v3.1
  .venv/bin/python scripts/generate_summary_ab.py --limit 5 --models deepseek/deepseek-chat-v3.1 gemini-2.5-flash
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()  # before src.config so API keys (incl. OPENROUTER_API_KEY) are visible

from src.event_kinds import INTERVIEW_KINDS  # noqa: E402
from src.eval_llm_client import build_eval_client  # noqa: E402
from src.eval_meeting_sampling import discover_meetings, select_diverse_sample  # noqa: E402
from src.models import Meeting, SummarySection  # noqa: E402
from src.summary_ab_pairing import (  # noqa: E402
    assign_visible_ids,
    build_answer_key,
    build_pair,
    build_visible_id_index,
    pair_rng,
    render_pair_markdown,
)
from src.summary_classify_eval import gold_sections_valid  # noqa: E402
from src.summarize import (  # noqa: E402
    _full_section_transcript,
    _generate_executive_summary,
    _generate_interview_executive_summary,
    _summarize_discussion,
    _summarize_interview_topic,
)

DEFAULT_MEETINGS_DIR = os.path.expanduser("~/CouncilScribe/meetings")
CIVIC_SYNTHESIS_TYPES = ("discussion", "public_comment", "consent_agenda")

JUDGING_INSTRUCTIONS = """\
# Summary Synthesis A/B Pairs

For each pair below, read Option 1 and Option 2 and decide which is
better — WITHOUT knowing which model produced which option. Record your pick
using the pair id shown next to each comparison (e.g. "pair-7: Option 1");
answer_key.json is intentionally not shown here so the review stays blind.

Judging suggestions:
- Prefer faithful attribution of quotes/positions to the correct speaker.
- Prefer concrete outcomes (votes, motions, specific claims) over vague summary.
- Penalize hallucinated detail not supported by the transcript.
- For executive summaries, prefer ones a citizen could act on in 60 seconds.
"""


def gold_gate(meeting: Meeting, gold_sections: list) -> tuple:
    """Stale-gold guard (mirrors scripts/eval_summary_classify.py's replay_one):
    (ok, reason) for whether this meeting's accepted summary.json boundaries
    still index into its current transcript. A meeting that fails can't be fed
    to _full_section_transcript at all — skip it before any model call.

    Passes ALL segment ids, not just text-bearing ones: backfill_segment_merge.py
    reindexes by time and leaves real ids carrying empty text, so a boundary
    landing on one of those is valid. Narrowing the set here would skip a third
    of the corpus as "stale" when it is nothing of the sort.
    """
    return gold_sections_valid(gold_sections, {s.segment_id for s in meeting.segments})


def _is_synthesis_eligible(section_type: str, is_interview: bool) -> bool:
    if is_interview:
        return True  # every interview "topic" section is prose synthesis output
    return section_type in CIVIC_SYNTHESIS_TYPES


def _reference_view(gold_summary: dict, is_interview: bool) -> dict:
    """The accepted summary.json's synthesis-eligible content, unchanged."""
    sections = [
        {"title": s.get("title", ""), "section_type": s.get("section_type", ""),
         "content": s.get("content", "")}
        for s in gold_summary.get("sections", [])
        if _is_synthesis_eligible(s.get("section_type", ""), is_interview)
    ]
    return {
        "executive_summary": gold_summary.get("executive_summary", ""),
        "sections": sections,
    }


def synthesize_candidate(client, model_override, meeting: Meeting, gold_sections: list) -> dict:
    """Re-run the synthesis stage over meeting's ACCEPTED section boundaries
    with model_override. Non-synthesis-eligible sections are copied through
    from gold_sections verbatim (see module docstring)."""
    segments = [s for s in meeting.segments if s.text]
    is_interview = meeting.event_kind in INTERVIEW_KINDS

    candidate_sections: list[SummarySection] = []
    regenerated: list[dict] = []  # synthesis-eligible only, in gold order
    for sec in gold_sections:
        sec_type = sec.get("section_type", "procedural")
        title = sec.get("title", "")
        start_seg = sec.get("start_segment", 0)
        end_seg = sec.get("end_segment", start_seg)

        if _is_synthesis_eligible(sec_type, is_interview):
            section_transcript = _full_section_transcript(segments, start_seg, end_seg)
            if is_interview:
                content = _summarize_interview_topic(client, section_transcript, title, model=model_override)
            else:
                content = _summarize_discussion(client, section_transcript, title, model=model_override)
            regenerated.append({"title": title, "section_type": sec_type, "content": content})
        else:
            content = sec.get("content", "")  # reused verbatim from the accepted summary

        candidate_sections.append(SummarySection(
            section_type=sec_type, title=title, content=content,
            start_time=sec.get("start_time", 0.0), end_time=sec.get("end_time", 0.0),
            start_segment=start_seg, end_segment=end_seg,
        ))

    # _generate_executive_summary/_generate_interview_executive_summary return
    # (executive_summary, highlights) from ONE call — highlights can't be
    # skipped at the API level without a different (production-diverging)
    # prompt. But build_meeting_pairs() never pairs highlights (only
    # executive_summary + the regenerated sections), so there's no reason to
    # carry the unused value any further than this local discard.
    if is_interview:
        executive, _highlights = _generate_interview_executive_summary(
            client, candidate_sections, meeting, model=model_override)
    else:
        executive, _highlights = _generate_executive_summary(
            client, candidate_sections, meeting, model=model_override)

    return {"executive_summary": executive, "sections": regenerated}


def build_meeting_pairs(meeting_id: str, model_key: str, candidate_view: dict, reference_view: dict) -> list:
    """[build_pair(...), ...] for one (meeting, model): the executive summary
    first, then each synthesis-eligible section in gold order (candidate and
    reference sections are built from the SAME gold section list in
    synthesize_candidate/_reference_view, so index i is title-matched).

    A fresh rng, seeded from (meeting_id, model_key), is used per (meeting,
    model) call — so adding/removing a candidate model from a run never
    reshuffles another model's already-assigned pairs for this meeting, and
    two models compared against the same meeting get independent orderings."""
    rng = pair_rng(meeting_id, model_key)
    pairs = [
        build_pair(meeting_id, f"{model_key}::executive_summary", model_key,
                   candidate_view["executive_summary"], reference_view["executive_summary"], rng)
    ]
    for i, (cand_sec, ref_sec) in enumerate(zip(candidate_view["sections"], reference_view["sections"])):
        pair_key = f"{model_key}::section_{i}:{ref_sec['title']}"
        pairs.append(build_pair(meeting_id, pair_key, model_key, cand_sec["content"], ref_sec["content"], rng))
    return pairs


def _pair_title(pair_key: str) -> str:
    # pair_key looks like "<model>::executive_summary" or "<model>::section_0:Title"
    # — used only to derive a reviewer-facing title; the model prefix is
    # discarded and never rendered.
    _, _, rest = pair_key.partition("::")
    if rest == "executive_summary":
        return "Executive Summary"
    _, _, title = rest.partition(":")
    return title or rest


def assemble_markdown(comparisons: list) -> str:
    """comparisons: [{"meeting_id": str, "comparison_index": int, "pairs": [...]}],
    each pair already carrying a "visible_id" (see assign_visible_ids).
    Produces the FULL ab_pairs.md text: judging instructions, then one
    "## <meeting_id> — Comparison N" section per comparison with each pair
    rendered under its visible id. Anonymous by construction — no model name
    or candidate/reference role is ever written here (see
    tests/test_generate_summary_ab_script.py for the leak-proof check)."""
    parts = [JUDGING_INSTRUCTIONS]
    for comp in comparisons:
        parts.append(f"## {comp['meeting_id']} — Comparison {comp['comparison_index']}\n")
        for pair in comp["pairs"]:
            parts.append(render_pair_markdown(pair, _pair_title(pair["pair_key"]), pair.get("visible_id")))
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--meetings", nargs="+", default=None, help="explicit meeting ids")
    ap.add_argument("--limit", type=int, default=5,
                     help="sample size when --meetings isn't given (deterministic, diverse kinds)")
    ap.add_argument("--models", nargs="+", required=True,
                     help="'current' or OpenRouter model ids to compare against the accepted summary")
    ap.add_argument("--meetings-dir", default=DEFAULT_MEETINGS_DIR)
    ap.add_argument("--out", default=None,
                     help="output dir (default ~/CouncilScribe/eval/summary-ab/<today>/)")
    args = ap.parse_args()

    meetings_dir = Path(os.path.expanduser(args.meetings_dir))
    out_dir = Path(os.path.expanduser(args.out)) if args.out else (
        Path(os.path.expanduser("~/CouncilScribe/eval/summary-ab")) / date.today().isoformat()
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.meetings:
        sample = []
        for meeting_id in args.meetings:
            mdir = meetings_dir / meeting_id
            if not (mdir / "transcript_named.json").exists() or not (mdir / "summary.json").exists():
                print(f"! skipping {meeting_id}: missing transcript_named.json or summary.json")
                continue
            sample.append(meeting_id)
    else:
        all_meetings = discover_meetings(meetings_dir)
        sample = select_diverse_sample(all_meetings, args.limit)
    print(f"Selected {len(sample)} meeting(s): {sample}\n")

    # Build each model's client ONCE, not once per meeting.
    clients = {}
    for model_key in args.models:
        try:
            clients[model_key] = build_eval_client(model_key)
        except RuntimeError as e:
            print(f"! skipping {model_key}: {e}")
    if not clients:
        print("No models ran (missing API keys?).")

    all_pairs = []
    comparisons = []
    next_visible_id = 1

    for meeting_id in sample:
        mdir = meetings_dir / meeting_id
        try:
            transcript_data = json.loads((mdir / "transcript_named.json").read_text())
            gold_summary = json.loads((mdir / "summary.json").read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"! {meeting_id}: could not read artifacts ({e}) — skipping")
            continue
        meeting = Meeting.from_dict(transcript_data)
        gold_sections = gold_summary.get("sections", [])
        is_interview = meeting.event_kind in INTERVIEW_KINDS

        ok, reason = gold_gate(meeting, gold_sections)
        if not ok:
            print(f"! {meeting_id}: SKIPPED ({reason})")
            continue

        reference_view = _reference_view(gold_summary, is_interview)

        comparison_index = 0
        for model_key, (client, model_override) in clients.items():
            try:
                candidate_view = synthesize_candidate(client, model_override, meeting, gold_sections)
            except Exception as e:
                print(f"! {model_key}/{meeting_id}: {e} — skipping")
                continue

            pairs = build_meeting_pairs(meeting_id, model_key, candidate_view, reference_view)
            pairs = assign_visible_ids(pairs, start=next_visible_id)
            next_visible_id += len(pairs)
            all_pairs.extend(pairs)

            comparison_index += 1
            comparisons.append({
                "meeting_id": meeting_id,
                "comparison_index": comparison_index,
                "pairs": pairs,
            })
            print(f"  {model_key}/{meeting_id}: {len(pairs)} pairs "
                  f"({len(candidate_view['sections'])} synthesis sections + 1 executive summary)")

    ab_pairs_path = out_dir / "ab_pairs.md"
    ab_pairs_path.write_text(assemble_markdown(comparisons), encoding="utf-8")

    answer_key = {
        "note": "Withheld from ab_pairs.md — for scoring after the blind review only.",
        "by_pair_id": build_visible_id_index(all_pairs),
        "pairs": build_answer_key(all_pairs),
    }
    answer_key_path = out_dir / "answer_key.json"
    answer_key_path.write_text(json.dumps(answer_key, indent=2), encoding="utf-8")

    print(f"\nWrote {len(all_pairs)} pairs across {len(sample)} meeting(s) to:")
    print(f"  {ab_pairs_path}")
    print(f"  {answer_key_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

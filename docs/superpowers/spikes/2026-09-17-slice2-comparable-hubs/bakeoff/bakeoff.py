#!/usr/bin/env python3
"""bakeoff.py -- SPIKE harness comparing three source-discovery engines against a
hand-labeled eval set. NOT production code.

Usage:
    .venv/bin/python bakeoff.py [--engine a|b|c|both|all] [--race SLUG] [--dry-run]

    'both' = engine A + engine B (legacy default). 'all' = A + B + C.

--dry-run mocks Tavily + all OpenRouter calls with canned fixtures (fixtures.py)
-- NO network, NO cost -- and runs the full pipeline (engines -> judge ->
report) so the harness is proven end-to-end without any API keys. Without
--dry-run, a real run needs OPENROUTER_API_KEY (already in .env.local) for
all three engines + the judge, and TAVILY_API_KEY for engines A and C
(missing key -> that engine reports itself unavailable for the run rather
than failing it).

See README.md in this directory for more.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO_ROOT = Path("/Users/chrisandrews/Documents/GitHub/on-the-record")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import BAKEOFF_DIR, load_ground_truth, round_num  # noqa: E402

import engine_a  # noqa: E402
import engine_b  # noqa: E402
import engine_c  # noqa: E402
import judge  # noqa: E402

from gui.env import load_env_local  # noqa: E402

# Populate OPENROUTER_API_KEY / TAVILY_API_KEY from the repo's .env.local before
# any engine runs — the engines check these at runtime. Harmless for --dry-run.
load_env_local()

ENGINES = {"a": engine_a, "b": engine_b, "c": engine_c}
ENGINE_NAMES = {
    "a": "Engine A (Tavily search-agent loop)",
    "b": "Engine B (OpenRouter web plugin)",
    "c": "Engine C (Tavily hub-directed loop)",
}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--engine",
        choices=["a", "b", "c", "both", "all"],
        default="both",
        help="Which engine(s) to run. 'both' = a+b (legacy default); 'all' = a+b+c.",
    )
    p.add_argument("--race", metavar="SLUG", default=None, help="Only run this one race (by its `race` slug).")
    p.add_argument("--dry-run", action="store_true", help="Mock all network/LLM calls with canned fixtures.")
    return p.parse_args(argv)


def select_races(slug: str | None) -> list[dict]:
    races = load_ground_truth()
    if slug is None:
        return races
    matches = [r for r in races if r["race"] == slug]
    if not matches:
        known = ", ".join(r["race"] for r in races)
        raise SystemExit(f"No race with slug {slug!r}. Known races: {known}")
    return matches


def run_engine_on_race(engine_key: str, race: dict, dry_run: bool) -> dict:
    module = ENGINES[engine_key]
    result = module.run(race, dry_run=dry_run)
    result["race"] = race["race"]
    result["engine"] = engine_key
    return result


def score_result(race: dict, engine_result: dict, dry_run: bool) -> dict | None:
    if engine_result["status"] != "ok":
        return None
    return judge.judge_engine(race, engine_result["found"], dry_run=dry_run)


def aggregate(engine_key: str, per_race: list[dict]) -> dict:
    """Pooled (micro-averaged) totals across every scored race for one engine."""
    scored = [r for r in per_race if r["score"] is not None]
    unavailable = [r for r in per_race if r["result"]["status"] == "unavailable"]

    total_known = sum(r["score"]["known"] for r in scored)
    total_matched = sum(r["score"]["matched_count"] for r in scored)
    total_found = sum(r["score"]["found_count"] for r in scored)
    total_correct = sum(r["score"]["correct_count"] for r in scored)
    trap_totals = {reason: 0 for reason in judge.TRAP_REASONS}
    for r in scored:
        for reason, n in r["score"]["trap_counts"].items():
            trap_totals[reason] += n

    recall = (total_matched / total_known) if total_known else None
    precision = (total_correct / total_found) if total_found else None

    return {
        "engine": engine_key,
        "races_scored": len(scored),
        "races_unavailable": len(unavailable),
        "total_known": total_known,
        "total_found": total_found,
        "total_matched": total_matched,
        "total_correct": total_correct,
        "recall": round_num(recall, 3),
        "precision": round_num(precision, 3),
        "trap_counts": trap_totals,
    }


def render_report(dry_run: bool, engine_keys: list[str], per_race_rows: list[dict], aggregates: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append("# Source-Discovery Bakeoff Report")
    lines.append("")
    lines.append(f"Generated: {now}")
    mode = "**DRY-RUN (mocked, no network, no cost)**" if dry_run else "**LIVE RUN (real API calls were made)**"
    lines.append(f"Mode: {mode}")
    lines.append("")
    lines.append(
        f"This is a SPIKE benchmark, not production. It compares {len(engine_keys)} engine(s) for "
        "the source-discovery hunt (finding comparable common-question sources for a "
        "race) against an 8-race hand-labeled ground truth."
    )
    lines.append("")

    lines.append("## Aggregate summary")
    lines.append("")
    lines.append("| Engine | Races scored | Races unavailable | Recall | Precision | Stale | Advocacy | Hallucinated |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for agg in aggregates:
        name = ENGINE_NAMES[agg["engine"]]
        recall = "n/a" if agg["recall"] is None else f"{agg['recall']:.2f}"
        precision = "n/a" if agg["precision"] is None else f"{agg['precision']:.2f}"
        t = agg["trap_counts"]
        lines.append(
            f"| {name} | {agg['races_scored']} | {agg['races_unavailable']} | {recall} | {precision} "
            f"| {t['stale']} | {t['advocacy']} | {t['hallucinated']} |"
        )
    lines.append("")

    lines.append("## Per-race detail")
    lines.append("")
    lines.append("| Race | Engine | Status | Known | Found | Matched | Recall | Precision | Stale | Advocacy | Hallucinated |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in per_race_rows:
        result = row["result"]
        score = row["score"]
        if result["status"] != "ok":
            lines.append(
                f"| {row['race']} | {ENGINE_NAMES[row['engine']]} | unavailable ({result.get('reason')}) "
                f"| - | - | - | - | - | - | - | - |"
            )
            continue
        recall = "n/a" if score["recall"] is None else f"{score['recall']:.2f}"
        precision = "n/a" if score["precision"] is None else f"{score['precision']:.2f}"
        t = score["trap_counts"]
        lines.append(
            f"| {row['race']} | {ENGINE_NAMES[row['engine']]} | ok | {score['known']} | {score['found_count']} "
            f"| {score['matched_count']} | {recall} | {precision} | {t['stale']} | {t['advocacy']} | {t['hallucinated']} |"
        )
    lines.append("")

    if dry_run:
        lines.append("## Note on this run")
        lines.append("")
        lines.append(
            "This report was produced with `--dry-run`: every Tavily search/fetch and "
            f"every OpenRouter chat completion ({len(engine_keys)} engine(s) + the judge) was replaced "
            "by a deterministic canned fixture from `fixtures.py`. No network calls "
            "were made and no API cost was incurred. The recall/precision numbers "
            "above only prove the scoring pipeline works end-to-end -- they say "
            "nothing about which real engine is actually better at the hunt. A real "
            "run (see README.md) is the human's deliberate next step."
        )
        lines.append("")

    return "\n".join(lines)


def print_summary_table(aggregates: list[dict]) -> None:
    print()
    print(f"{'Engine':<38} {'Scored':>6} {'Unavail':>7} {'Recall':>7} {'Precision':>9} {'Stale':>6} {'Advoc':>6} {'Halluc':>7}")
    for agg in aggregates:
        name = ENGINE_NAMES[agg["engine"]]
        recall = "n/a" if agg["recall"] is None else f"{agg['recall']:.2f}"
        precision = "n/a" if agg["precision"] is None else f"{agg['precision']:.2f}"
        t = agg["trap_counts"]
        print(
            f"{name:<38} {agg['races_scored']:>6} {agg['races_unavailable']:>7} {recall:>7} {precision:>9} "
            f"{t['stale']:>6} {t['advocacy']:>6} {t['hallucinated']:>7}"
        )
    print()


def main(argv=None) -> int:
    args = parse_args(argv)
    races = select_races(args.race)
    if args.engine == "both":
        engine_keys = ["a", "b"]
    elif args.engine == "all":
        engine_keys = ["a", "b", "c"]
    else:
        engine_keys = [args.engine]

    per_race_rows = []
    for race in races:
        for engine_key in engine_keys:
            result = run_engine_on_race(engine_key, race, args.dry_run)
            score = score_result(race, result, args.dry_run)
            per_race_rows.append({"race": race["race"], "engine": engine_key, "result": result, "score": score})

    aggregates = [aggregate(k, [r for r in per_race_rows if r["engine"] == k]) for k in engine_keys]

    report_text = render_report(args.dry_run, engine_keys, per_race_rows, aggregates)
    report_path = BAKEOFF_DIR / "report.md"
    report_path.write_text(report_text, encoding="utf-8")

    print(f"Ran {len(races)} race(s) x {len(engine_keys)} engine(s). Report written to {report_path}")
    print_summary_table(aggregates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

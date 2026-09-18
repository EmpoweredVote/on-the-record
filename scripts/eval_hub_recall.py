#!/usr/bin/env python3
"""Hub-lane comparable-source recall eval (Slice 2 Phase 4).

Manual ONLINE harness (live Tavily + OpenRouter) — NOT a pytest test. Runs the
shipped hub lane for each ground-truth race and reports recall of the comparable
common-question sources the ground truth says exist:

  hubs.hubs_for_race -> hub_search.raw_items_for_race (Tavily)
      -> prefilter_item -> classify.classify_item (OpenRouter + page peek)

Reported per race and pooled (micro-averaged) over --runs (majority vote per
source):
  * addressable recall (headline) = found+verified / GT a scoped_search hub domain reaches
  * overall recall (context)      = found+verified / all GT
  * retrieval-only recall (debug) = retrieved by Tavily / GT   (pre-classify)

Usage (repo root; keys from .env.local):
  .venv/bin/python scripts/eval_hub_recall.py [--races SLUG ...] [--runs N]
      [--budget N] [--no-classify] [--hubs snapshot|db] [--print-hubs] [--env-file PATH]

The metric is noisy — use --runs N and treat a change as real only when it beats
the per-run spread this harness prints (use --runs 5 for a tuning decision).

Baseline: NOT YET MEASURED — filled by Task 5 of the plan.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gui.env import load_env_local  # noqa: E402
from src import config  # noqa: E402
from src.discovery import hub_recall_eval as hre  # noqa: E402
from src.discovery import hub_search, hubs  # noqa: E402
from src.discovery.classify import classify_item  # noqa: E402
from src.discovery.feeds import fetch_page_text  # noqa: E402
from src.discovery.prefilter import prefilter_item  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402

GROUND_TRUTH = REPO_ROOT / "tests/fixtures/hub_recall_ground_truth.json"
HUBS_SNAPSHOT = REPO_ROOT / "tests/fixtures/hub_recall_hubs_snapshot.json"
_HUB_FIELDS = {"id", "name", "scope", "state", "kind", "poll_method", "domain",
               "query_template", "tos_bucket", "active", "added_via", "notes"}


def load_ground_truth(path: Path = GROUND_TRUTH) -> list:
    return json.loads(path.read_text(encoding="utf-8"))["races"]


def load_hubs_from_snapshot(path: Path = HUBS_SNAPSHOT) -> list:
    rows = json.loads(path.read_text(encoding="utf-8"))["hubs"]
    return [hubs.Hub(**{k: v for k, v in r.items() if k in _HUB_FIELDS}) for r in rows]


def load_hubs_from_db() -> list:
    from src.discovery import db
    conn = db.connect()
    try:
        return hubs.load_hubs(conn.cursor())
    finally:
        conn.close()


def _peek(url: str):
    try:
        return fetch_page_text(url) or None
    except Exception:  # noqa: BLE001 — the peek is optional; classify proceeds without
        return None


def _accepted(verdict) -> bool:
    return (verdict.rejected_reason is None and verdict.relevant
            and verdict.confidence >= config.DISCOVERY_CONFIDENCE_FLOOR)


def run_race(race: dict, all_hubs: list, provider, *, budget: int, do_classify: bool):
    """One race, one pass. Returns (hub_domains, found_urls, accepted_urls)."""
    applicable = hubs.hubs_for_race(all_hubs, state=race.get("state"))
    hub_domains = [h.domain for h in applicable
                   if h.domain and not hub_search._is_pointer_only(h)]
    items = hub_search.raw_items_for_race(
        applicable, candidates=race["candidates"],
        locality=race["race_label"], year=race.get("year"), budget=budget)
    found_urls = [it.url for it in items]
    accepted_urls = []
    if do_classify:
        for it in items:
            pf = prefilter_item(it.title, it.description, it.duration_seconds, race["candidates"])
            if not pf.passed:
                continue
            v = classify_item(provider, it, race_label=race["race_label"],
                              roster_names=race["candidates"], peek_fetcher=_peek)
            if _accepted(v):
                accepted_urls.append(it.url)
    return hub_domains, found_urls, accepted_urls


def _fmt(x):
    return "  n/a" if x is None else f"{x:.2f}"


def _headline(rec: dict, do_classify: bool):
    return rec["addressable_recall"] if do_classify else rec["retrieval_recall_addressable"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--races", nargs="+", default=None, help="limit to these race slugs")
    p.add_argument("--runs", type=int, default=3, help="passes per race; majority vote per source")
    p.add_argument("--budget", type=int, default=config.DISCOVERY_HUB_BUDGET,
                   help="scoped searches per race (default config.DISCOVERY_HUB_BUDGET)")
    p.add_argument("--no-classify", action="store_true", help="retrieval only (skips OpenRouter)")
    p.add_argument("--hubs", choices=["snapshot", "db"], default="snapshot",
                   help="registry source (default: committed snapshot)")
    p.add_argument("--print-hubs", action="store_true", help="print the loaded hubs as JSON and exit")
    p.add_argument("--env-file", default=None, help="path to .env.local (worktree runs: point at the main checkout)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_env_local(Path(args.env_file) if args.env_file else None)

    all_hubs = load_hubs_from_db() if args.hubs == "db" else load_hubs_from_snapshot()
    if args.print_hubs:
        print(json.dumps({"hubs": [h.__dict__ for h in all_hubs]}, indent=2))
        return 0

    if "TAVILY_API_KEY" not in os.environ:
        print("FATAL: TAVILY_API_KEY not set — the retrieval stage needs it. "
              "Export it or pass --env-file <main>/.env.local.", file=sys.stderr)
        return 2
    do_classify = not args.no_classify
    if do_classify and "OPENROUTER_API_KEY" not in os.environ:
        print("FATAL: OPENROUTER_API_KEY not set — needed to classify. "
              "Export it, pass --env-file, or use --no-classify.", file=sys.stderr)
        return 2

    races = load_ground_truth()
    if args.races:
        want = set(args.races)
        races = [r for r in races if r["race"] in want]
        if not races:
            print(f"FATAL: no ground-truth race matched {sorted(want)}", file=sys.stderr)
            return 2
    provider = get_provider(config.DISCOVERY_MODEL_ACTIVE) if do_classify else None
    if args.runs < 1:
        print("FATAL: --runs must be >= 1", file=sys.stderr)
        return 2

    print(f"model={config.DISCOVERY_MODEL_ACTIVE if do_classify else '(none)'} "
          f"runs={args.runs} budget={args.budget} races={len(races)} "
          f"hubs={args.hubs} classify={do_classify}\n")

    # acc[race_slug][source_id] = {"addressable","retrieved_count","accepted_count"}
    acc: dict = {}
    per_run_headline = []
    precision_matched = precision_total = 0  # pooled over runs (accepted-item quality)
    for run_i in range(args.runs):
        run_rows = []
        for race in races:
            hub_domains, found, accepted = run_race(
                race, all_hubs, provider, budget=args.budget, do_classify=do_classify)
            rows = hre.score_run(race["sources"], hub_domains, found, accepted)
            run_rows.extend(rows)
            prec = hre.precision(race["sources"], accepted)
            precision_matched += prec["n_matched"]
            precision_total += prec["n_accepted"]
            slot = acc.setdefault(race["race"], {})
            for r in rows:
                cell = slot.setdefault(r["id"], {"addressable": r["addressable"],
                                                 "retrieved_count": 0, "accepted_count": 0})
                cell["addressable"] = r["addressable"]
                cell["retrieved_count"] += int(r["retrieved"])
                cell["accepted_count"] += int(r["accepted"])
        hl = _headline(hre.recalls_from_per_source(run_rows), do_classify)
        per_run_headline.append(hl)
        print(f"run {run_i + 1}/{args.runs}: headline recall = {_fmt(hl)}")

    print("\n| race | addr | overall | retr | n(addr/gt) |")
    print("|---|---|---|---|---|")
    all_majority = []
    for race in races:
        recs = [{"id": sid, **cell} for sid, cell in acc[race["race"]].items()]
        maj = hre.majority_per_source(recs, args.runs)
        all_majority.extend(maj)
        rr = hre.recalls_from_per_source(maj)
        print(f"| {race['race']} | {_fmt(rr['addressable_recall'])} | {_fmt(rr['overall_recall'])} "
              f"| {_fmt(rr['retrieval_recall_overall'])} | {rr['n_addressable']}/{rr['n_gt']} |")

    pooled = hre.recalls_from_per_source(all_majority)
    n_addr_verified = sum(1 for r in all_majority if r["addressable"] and r["accepted"])
    hv = [h for h in per_run_headline if h is not None]
    spread = (f"{min(hv):.2f}..{max(hv):.2f} (median {statistics.median(hv):.2f})"
              if hv else "n/a")
    print("\n== POOLED (majority vote over runs, micro-averaged over races) ==")
    print(f"addressable recall (headline): {_fmt(pooled['addressable_recall'])}  "
          f"[{n_addr_verified}∩A / {pooled['n_addressable']}]")
    print(f"overall recall:                {_fmt(pooled['overall_recall'])}  "
          f"[{pooled['n_verified']} / {pooled['n_gt']}]")
    print(f"retrieval-only recall:         {_fmt(pooled['retrieval_recall_overall'])}  "
          f"[{pooled['n_retrieved']} / {pooled['n_gt']}]")
    pooled_prec = (precision_matched / precision_total) if precision_total else None
    print(f"precision (pooled over runs):  {_fmt(pooled_prec)}  "
          f"[{precision_matched} / {precision_total}]")
    print(f"per-run headline spread:       {spread}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

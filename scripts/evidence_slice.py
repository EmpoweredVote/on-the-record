#!/usr/bin/env python3
# scripts/evidence_slice.py
"""Manual ONLINE runner for the evidence trust-core slice (LA Mayor).

Reads the candidates' already-cited compass-research sources, runs the
extract -> verbatim gate -> independent cross-check -> judge pipeline, and
writes artifacts (no DB writes). Keys from the main-checkout .env.local or
--env-file.

  ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
      [--race ID] [--limit N] [--gold path.json]
"""
from __future__ import annotations
import argparse
import functools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.evidence import data, pipeline, report
from src.evidence.eval import score
from src.evidence.pipeline import Providers
from src.llm_providers import get_provider
from src.discovery.feeds import fetch_page_text

LA_MAYOR = "9e888818-c50b-4c61-a106-a0839ff2479d"
SPIKE_DIR = (pathlib.Path(__file__).resolve().parents[1]
             / "docs/superpowers/spikes/2026-09-19-evidence-trust-core")


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default=LA_MAYOR)
    ap.add_argument("--candidate", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--extractor", default="sonnet")
    ap.add_argument("--crosschecker", default="gemini-flash")
    ap.add_argument("--judge", default="gpt5-mini")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--out", default=str(SPIKE_DIR))
    ap.add_argument("--gold", default=None)
    ap.add_argument("--max-chars", type=int, default=200000)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    providers = Providers(extractor=get_provider(args.extractor),
                          crosschecker=get_provider(args.crosschecker),
                          judge=get_provider(args.judge))
    conn = data.connect(args.env_file)
    roster = data.fetch_roster(conn, args.race)
    if args.candidate:
        roster = [r for r in roster if r["politician_id"] == args.candidate]

    fetcher = functools.partial(fetch_page_text, max_chars=args.max_chars)

    all_items, all_leads = [], []
    for cand in roster:
        sources = data.fetch_cited_sources(conn, cand["politician_id"])
        if args.limit:
            sources = sources[:args.limit]
        items, leads = pipeline.run_candidate(
            politician_id=cand["politician_id"], candidate_name=cand["name"],
            sources=sources, providers=providers, fetcher=fetcher,
            batch_id="evidence-slice-la-mayor")
        print(f"{cand['name']}: {len(items)} items, {len(leads)} leads "
              f"from {len(sources)} sources")
        all_items += items
        all_leads += leads

    gold = json.loads(pathlib.Path(args.gold).read_text()) if args.gold else {}
    metrics = score(all_items, all_leads, gold)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence_items.json").write_text(
        json.dumps([it.to_json() for it in all_items], indent=2))
    (out / "leads.json").write_text(
        json.dumps([ld.to_json() for ld in all_leads], indent=2))
    (out / "eval_report.md").write_text(report.render_report(metrics, "LA Mayor"))
    (out / "review.html").write_text(
        report.render_review_html(all_items, all_leads, "LA Mayor"))
    print(f"\nWrote artifacts to {out}")
    print(report.render_report(metrics, "LA Mayor"))


if __name__ == "__main__":
    main()

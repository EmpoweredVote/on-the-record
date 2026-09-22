#!/usr/bin/env python3
"""Flag-vs-guard eval — does the classifier flag a candidate's own prior-cycle
answers (not reject them), keep current-cycle answers unflagged, and still guard
a wrong-contest page? Manual harness (live network + LLM), NOT a pytest test.

Runs each case in cases.json through the production peek (feeds.fetch_page_text,
which carries the cycle year) + the production discovery model, and scores the
`prior_cycle` flag and the guard (relevant) against the labels.

Usage (from the repo root, so `src` imports resolve; OPENROUTER_API_KEY needed):
    OPENROUTER_API_KEY=... .venv/bin/python \
        docs/superpowers/spikes/2026-09-17-slice2-comparable-hubs/eval/prior_cycle/run.py [--runs N]

The metric is noisy — pass --runs N (default 1) to average N passes per case.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]  # repo/docs/superpowers/spikes/<spike>/eval/prior_cycle/run.py
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Best-effort: load OPENROUTER_API_KEY from .env.local if not already exported.
_ENV = REPO_ROOT / ".env.local"
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        s = line.strip()
        if s.startswith("OPENROUTER_API_KEY=") and not os.environ.get("OPENROUTER_API_KEY"):
            os.environ["OPENROUTER_API_KEY"] = s.split("=", 1)[1].strip()

from src import config  # noqa: E402
from src.discovery.classify import build_prompt, parse_verdict, _SYSTEM  # noqa: E402
from src.discovery.feeds import fetch_page_text  # noqa: E402
from src.discovery.models import RawItem  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402

CASES = json.loads((Path(__file__).parent / "cases.json").read_text())["cases"]


def classify_case(provider, case) -> dict:
    excerpt = fetch_page_text(case["url"]) or ""
    item = RawItem(url=case["url"], title=(case["roster"][0] + " - Ballotpedia"), via="hub")
    prompt = build_prompt(item, race_label=case["race_label"],
                          roster_names=case["roster"], captions_excerpt=excerpt)
    reply = provider.complete(prompt, max_tokens=config.DISCOVERY_CLASSIFY_MAX_TOKENS,
                              temperature=0.0, system=_SYSTEM)
    v = parse_verdict(reply)
    return {"relevant": v.relevant, "prior_cycle": v.prior_cycle,
            "source_cycle_year": v.source_cycle_year, "why": (v.why or v.rejected_reason or "")[:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    provider = get_provider(config.DISCOVERY_MODEL_ACTIVE)
    print(f"model={config.DISCOVERY_MODEL_ACTIVE} runs={args.runs} cases={len(CASES)}\n")

    passed = 0
    for case in CASES:
        # majority vote across runs on the two graded fields
        rel_votes, pc_votes, last = Counter(), Counter(), None
        for _ in range(args.runs):
            r = classify_case(provider, case)
            rel_votes[r["relevant"]] += 1
            pc_votes[r["prior_cycle"]] += 1
            last = r
        rel = rel_votes.most_common(1)[0][0]
        pc = pc_votes.most_common(1)[0][0]
        ok_rel = (rel == case["expect_relevant"])
        # prior_cycle only graded when relevant + a label is given
        ok_pc = (case["expect_prior_cycle"] is None) or (not rel) or (pc == case["expect_prior_cycle"])
        ok = ok_rel and ok_pc
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']} ({case['category']})")
        print(f"   expect: relevant={case['expect_relevant']} prior_cycle={case['expect_prior_cycle']}")
        print(f"   got:    relevant={rel} prior_cycle={pc} year={last['source_cycle_year']}")
        print(f"   why:    {last['why']}\n")
    print(f"== {passed}/{len(CASES)} cases pass ==")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())

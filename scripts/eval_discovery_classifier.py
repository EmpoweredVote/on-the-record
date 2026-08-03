"""Eval the discovery classifier against labeled fixtures.

Usage:
  .venv/bin/python scripts/eval_discovery_classifier.py --models haiku sonnet
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src import config  # noqa: E402
from src.discovery.classify import classify_item  # noqa: E402
from src.discovery.eval import classify_outcome, summarize  # noqa: E402
from src.discovery.models import RawItem  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=[config.DISCOVERY_MODEL_ACTIVE])
    args = ap.parse_args()
    examples = [json.loads(line) for line in FIXTURES.read_text().splitlines() if line]
    rows = []
    for model in args.models:
        provider = get_provider(model)
        outcomes = []
        for ex in examples:
            item = RawItem(url="https://example.test/eval", title=ex["title"],
                           description=ex["description"], channel_name=ex["channel"],
                           duration_seconds=ex["duration_seconds"], via="search")
            verdict = classify_item(provider, item, race_label=ex["race_label"],
                                    roster_names=ex["roster"], captions_fetcher=None)
            outcome = classify_outcome(ex["gold_relevant"], verdict)
            outcomes.append(outcome)
            print(f"{model} {outcome:15s} conf={verdict.confidence:.2f} {ex['title']!r}")
        rows.append(summarize(model, outcomes))
    print("\n| model | n | recall | precision | parse_failure |")
    print("|---|---|---|---|---|")
    for r in rows:
        rec = f"{r['recall']:.2f}" if r["recall"] is not None else "—"
        prec = f"{r['precision']:.2f}" if r["precision"] is not None else "—"
        print(f"| {r['model']} | {r['n']} | {rec} | {prec} | {r['parse_failure']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

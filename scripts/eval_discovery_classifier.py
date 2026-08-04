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
from src.discovery.eval import calibration, classify_outcome, summarize  # noqa: E402
from src.discovery.models import RawItem  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402

FIXTURES = [
    Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval.jsonl",
    Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval_real.jsonl",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=[config.DISCOVERY_MODEL_ACTIVE])
    args = ap.parse_args()
    examples = []
    for path in FIXTURES:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line:
                    ex = json.loads(line)
                    ex["_fixture"] = path.stem
                    examples.append(ex)
    fixture_stems = [p.stem for p in FIXTURES if p.exists()]
    rows = []
    cal_by_model = []
    route_stats_by_model = []
    # human-labeled route ground truth, real fixture only: 'ingested' means a
    # human routed it to the real pipeline (route=ingest), 'approved' means a
    # human routed it to quote-sourcing (route=quote_source). The 8 synthetic
    # discovery_eval.jsonl examples carry no status/source_key -- they fall
    # back to the https://example.test/eval URL below and read as "web page"
    # in the prompt even though most are YouTube-shaped; acceptable since
    # they're not part of this route-accuracy readout.
    _EXPECTED_ROUTE = {"ingested": "ingest", "approved": "quote_source"}
    for model in args.models:
        provider = get_provider(model)
        outcomes = []
        pairs = []
        by_fixture_outcomes = {stem: [] for stem in fixture_stems}
        by_fixture_pairs = {stem: [] for stem in fixture_stems}
        route_correct = {stem: 0 for stem in fixture_stems}
        route_total = {stem: 0 for stem in fixture_stems}
        for ex in examples:
            source_key_val = ex.get("source_key", "")
            url = (f"https://www.youtube.com/watch?v={source_key_val.split(':', 1)[1]}"
                  if source_key_val.startswith("youtube:")
                  else ex.get("url", "https://example.test/eval"))
            item = RawItem(url=url, title=ex["title"],
                           description=ex["description"], channel_name=ex["channel"],
                           duration_seconds=ex["duration_seconds"],
                           published_at=ex.get("published_at"), via="search")
            verdict = classify_item(provider, item, race_label=ex["race_label"],
                                    roster_names=ex["roster"], peek_fetcher=None)
            outcome = classify_outcome(ex["gold_relevant"], verdict)
            outcomes.append(outcome)
            pairs.append((ex["gold_relevant"], verdict))
            by_fixture_outcomes[ex["_fixture"]].append(outcome)
            by_fixture_pairs[ex["_fixture"]].append((ex["gold_relevant"], verdict))
            expected_route = _EXPECTED_ROUTE.get(ex.get("status"))
            if expected_route is not None:
                route_total[ex["_fixture"]] += 1
                if verdict.route == expected_route:
                    route_correct[ex["_fixture"]] += 1
            print(f"{model} {outcome:15s} conf={verdict.confidence:.2f} {ex['title']!r}")
        for stem in fixture_stems:
            rows.append(summarize(f"{model} · {stem}", by_fixture_outcomes[stem]))
        rows.append(summarize(f"{model} · combined", outcomes))
        cal_by_model.append((model, calibration(pairs),
                            [(stem, calibration(by_fixture_pairs[stem]))
                             for stem in fixture_stems]))
        route_stats_by_model.append((model, route_correct, route_total))
    print("\n| model | n | recall | precision | parse_failure |")
    print("|---|---|---|---|---|")
    for r in rows:
        rec = f"{r['recall']:.2f}" if r["recall"] is not None else "—"
        prec = f"{r['precision']:.2f}" if r["precision"] is not None else "—"
        print(f"| {r['model']} | {r['n']} | {rec} | {prec} | {r['parse_failure']} |")
    for model, cal, cal_by_fixture in cal_by_model:
        if cal["brier"] is None:
            print(f"\ncalibration ({model} · combined): n=0")
        else:
            print(f"\ncalibration ({model} · combined): n={cal['n']} brier={cal['brier']:.3f}")
        for b in cal["buckets"]:
            print(f"  {b['range']}: n={b['n']} predicted={b['predicted']:.2f} "
                  f"actual={b['actual']:.2f}")
        for stem, fcal in cal_by_fixture:
            if fcal["brier"] is None:
                print(f"calibration ({model} · {stem}): n=0")
            else:
                print(f"calibration ({model} · {stem}): n={fcal['n']} brier={fcal['brier']:.3f}")
            for b in fcal["buckets"]:
                print(f"  {b['range']}: n={b['n']} predicted={b['predicted']:.2f} "
                      f"actual={b['actual']:.2f}")
    print()
    for model, route_correct, route_total in route_stats_by_model:
        for stem in fixture_stems:
            if route_total[stem] > 0:
                print(f"route_accuracy ({model} · {stem}): "
                      f"{route_correct[stem]}/{route_total[stem]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

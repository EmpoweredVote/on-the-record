#!/usr/bin/env python3
# scripts/judge_ab.py
"""Evidence judge A/B: deepseek vs gemini-flash vs Jev.

Two layers:

1. Pure metric functions (no I/O, no LLM calls, no DB access) — plain
   dict/list math over rows produced by ``run_ab``. A ``row`` is a dict:

       {id, human_status, review_reason, arm, mechanism, disposition,
        parse_ok, latency, notes}

   Convention for empty-group means: when a group has zero rows, its mean
   (and any gap derived from it) is ``None`` rather than ``0.0``. A count
   of zero items is not evidence of "zero mechanism score" — treating it
   as 0.0 would silently manufacture a (likely large, misleading) gap
   against a populated group. Callers that need a numeric fallback for
   display should coerce ``None`` themselves.

2. The harness itself — ``load_gold`` (read-only DB pull), the three arm
   builders (``make_provider_arm``/``make_jev_arm``), ``run_ab`` (times
   each arm over each gold item and computes disposition), and
   ``render_report``. Provider/Jev construction (anything needing an API
   key) happens lazily, inside the arm builders — never at import time —
   so ``import scripts.judge_ab`` and the pure-function tests need no
   keys. Only ``main`` touches the network or the DB.

Manual run (spends LLM — see docs/superpowers/plans/2026-09-21-evidence-judge-ab.md
Task 4):

    ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/judge_ab.py \\
        --env-file ~/Documents/GitHub/ev-accounts/backend/.env --sample 40 \\
        --out docs/superpowers/spikes/2026-09-19-evidence-trust-core/judge-ab
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from itertools import combinations
from statistics import mean, median

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.evidence import data  # noqa: E402
from src.evidence.disposition import decide  # noqa: E402
from src.evidence.models import GateResults, QuoteCandidate, SourceType  # noqa: E402


def _is_judge_relevant(row):
    """Rows the judge A/B cares about: accepted, or goal-only rejected."""
    if row["human_status"] == "accepted":
        return True
    return row["human_status"] == "rejected" and row["review_reason"] == "goal-only"


def mechanism_separation(rows):
    """Mean `mechanism` on accepted rows vs goal-only-rejected rows.

    Returns {"accept_mean", "goalonly_mean", "gap"}. An empty group's
    mean is None; if either mean is None, gap is also None (see module
    docstring for the empty-group convention).
    """
    accepted = [r["mechanism"] for r in rows if r["human_status"] == "accepted"]
    goalonly = [
        r["mechanism"]
        for r in rows
        if r["human_status"] == "rejected" and r["review_reason"] == "goal-only"
    ]

    accept_mean = mean(accepted) if accepted else None
    goalonly_mean = mean(goalonly) if goalonly else None
    gap = (
        accept_mean - goalonly_mean
        if accept_mean is not None and goalonly_mean is not None
        else None
    )

    return {"accept_mean": accept_mean, "goalonly_mean": goalonly_mean, "gap": gap}


def human_agreement(rows):
    """Agreement between judge disposition and human verdict.

    Restricted to the judge-relevant subset (accepted, or goal-only
    rejected). A row agrees when green<->accepted or flagged<->rejected.
    Returns {"n", "agree", "rate"}; rate is 0.0 when n is 0.
    """
    relevant = [r for r in rows if _is_judge_relevant(r)]
    n = len(relevant)
    agree = 0
    for r in relevant:
        if r["disposition"] == "green" and r["human_status"] == "accepted":
            agree += 1
        elif r["disposition"] == "flagged" and r["human_status"] == "rejected":
            agree += 1
    rate = agree / n if n else 0.0
    return {"n": n, "agree": agree, "rate": rate}


def parse_error_rate(rows):
    """Fraction of rows with parse_ok == False. Empty input -> 0.0."""
    if not rows:
        return 0.0
    failed = sum(1 for r in rows if r["parse_ok"] is False)
    return failed / len(rows)


def _dispositions_by_id(rows_by_arm):
    """{id: {arm: disposition}} across all arms."""
    by_id = {}
    for arm, rows in rows_by_arm.items():
        for r in rows:
            by_id.setdefault(r["id"], {})[arm] = r["disposition"]
    return by_id


def inter_judge_agreement(rows_by_arm):
    """Pairwise fraction of ids where two arms agree on `disposition`.

    Returns {"armA|armB": rate} for every unordered pair of arms present
    in rows_by_arm, computed over ids both arms scored.
    """
    by_id = _dispositions_by_id(rows_by_arm)
    arms = sorted(rows_by_arm.keys())
    result = {}
    for arm_a, arm_b in combinations(arms, 2):
        shared_ids = [
            i for i, dispositions in by_id.items()
            if arm_a in dispositions and arm_b in dispositions
        ]
        if shared_ids:
            agree = sum(
                1 for i in shared_ids
                if by_id[i][arm_a] == by_id[i][arm_b]
            )
            rate = agree / len(shared_ids)
        else:
            rate = 0.0
        result[f"{arm_a}|{arm_b}"] = rate
    return result


def divergent_ids(rows_by_arm):
    """ids where not all arms that scored it agree on `disposition`."""
    by_id = _dispositions_by_id(rows_by_arm)
    return sorted(
        i for i, dispositions in by_id.items()
        if len(set(dispositions.values())) > 1
    )


# --- Harness: gold, arms, run_ab, report --------------------------------


def load_gold(conn) -> list:
    """Read-only load of judge-relevant gold from ``inform.evidence_items``.

    A single SELECT, no writes. Returns one dict per row — keyed by
    column name — for every row whose human review_status is 'accepted'
    or 'rejected' (the judge A/B ignores anything still pending review).
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT id, verbatim_text, context, issue, review_status, review_reason "
        "FROM inform.evidence_items WHERE review_status IN ('accepted', 'rejected')"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _parse_ok(scores) -> bool:
    """False exactly when judge()'s parser fell back to its all-worst
    default payload (tag_ok==0, mechanism==0, dispute_risk==1) — i.e. the
    LLM's reply didn't parse as the expected JSON (see judge.parse_judge).
    Jev arms are typed Score answers, not free-text JSON, so they can't
    hit this path and always report True."""
    return not (scores.tag_ok == 0.0 and scores.mechanism == 0.0 and scores.dispute_risk == 1.0)


def make_provider_arm(provider_name: str):
    """Build an arm callable that scores a QuoteCandidate via judge() using
    the named LLM provider (e.g. "deepseek", "gemini-flash").

    Lazy: get_provider() — and therefore any API-key lookup — runs only
    when this is CALLED, not at import time, so importing this module (or
    building a fake-arm test) never needs a key.
    """
    from src.llm_providers import get_provider
    from src.evidence.judge import judge as judge_llm

    provider = get_provider(provider_name)

    def arm(cand):
        scores = judge_llm(cand, provider=provider)
        return scores, _parse_ok(scores)

    return arm


def make_jev_arm():
    """Build the jev arm callable.

    Constructs the TypeSafeClient eagerly (right here, not on the arm's
    first call) so a missing `typesafe_sdk` install or a missing
    TYPESAFE_API_KEY raises immediately at arm-construction time — callers
    (main/_build_arms) should catch broadly and skip the jev arm with a
    clear message rather than let its setup crash the deepseek/gemini-flash
    run too.
    """
    from src.evidence.judge_jev import judge_jev
    from typesafe_sdk import TypeSafeClient  # optional dependency

    client = TypeSafeClient()  # raises if the SDK can't find TYPESAFE_API_KEY

    def arm(cand):
        return judge_jev(cand, client=client), True

    return arm


def run_ab(gold, arms, *, decide_fn) -> dict:
    """Run every arm over every gold item.

    For each gold item, builds a QuoteCandidate from
    (verbatim_text, context, issue) and scores it with each arm. The
    OTHER disposition gates (own_words/in_context/primary/tag_agree) are
    held definitional-true and source_type is pinned to PRIMARY, so only
    the arm's JudgeScores can push the result to "flagged" — isolating
    each arm's judge quality from the rest of the pipeline (which this
    harness does not exercise).

    `decide_fn` is injected — `main` passes the real
    `src.evidence.disposition.decide`; tests can pass a fake — so this
    stays testable without importing the disposition thresholds' current
    values into the test itself.

    Returns {arm_name: [row, ...]}, one row per gold item per arm, in
    gold order.
    """
    rows_by_arm = {arm_name: [] for arm_name in arms}
    for item in gold:
        cand = QuoteCandidate(text=item["verbatim_text"], context=item["context"],
                              issue=item["issue"])
        for arm_name, arm_fn in arms.items():
            t0 = time.perf_counter()
            scores, parse_ok = arm_fn(cand)
            latency = time.perf_counter() - t0
            gates = GateResults(
                verbatim=True, own_words=True, in_context=True, primary=True,
                tag_agree=True, judge_tag_ok=scores.tag_ok,
                judge_context_sufficient=scores.context_sufficient,
                judge_dispute_risk=scores.dispute_risk, judge_mechanism=scores.mechanism)
            disposition, _reasons = decide_fn(gates, SourceType.PRIMARY)
            rows_by_arm[arm_name].append({
                "id": item["id"],
                "human_status": item["review_status"],
                "review_reason": item.get("review_reason"),
                "arm": arm_name,
                "mechanism": scores.mechanism,
                "disposition": disposition,
                "parse_ok": parse_ok,
                "latency": latency,
                "notes": getattr(scores, "notes", ""),
            })
    return rows_by_arm


def _latency_stats(rows):
    lat = [r["latency"] for r in rows]
    if not lat:
        return None, None
    return mean(lat), median(lat)


def _jev_confidence_summary(rows, low_threshold=0.5):
    """Mean per-dimension confidence + a low-confidence item count, parsed
    from each row's `notes` JSON blob (`{"confidence": {dim: value|None}}`
    — the shape `judge_jev` packs). Returns None when no row in `rows`
    carries a parseable confidence blob (i.e. this arm isn't jev), so the
    report can skip the section for deepseek/gemini-flash."""
    per_dim: dict = {}
    low_conf_items = 0
    seen = False
    for r in rows:
        raw = r.get("notes")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        conf = payload.get("confidence") if isinstance(payload, dict) else None
        if not isinstance(conf, dict):
            continue
        seen = True
        vals = [v for v in conf.values() if v is not None]
        if vals and min(vals) < low_threshold:
            low_conf_items += 1
        for k, v in conf.items():
            if v is not None:
                per_dim.setdefault(k, []).append(v)
    if not seen:
        return None
    return {
        "mean_by_dim": {k: mean(vs) for k, vs in per_dim.items() if vs},
        "low_confidence_items": low_conf_items,
        "low_confidence_threshold": low_threshold,
    }


def render_report(rows_by_arm, gold) -> str:
    """Markdown A/B report built on the pure metrics above: per-arm
    mechanism separation, human agreement, parse-error rate, mean/median
    latency, and (for jev) a confidence summary; plus inter-judge
    agreement and the ids where arms disagree."""
    def fmt(x, digits=3):
        return "n/a" if x is None else f"{x:.{digits}f}"

    lines = ["# Evidence judge A/B — deepseek vs gemini-flash vs jev", "",
             f"- gold items: {len(gold)}",
             f"- arms: {', '.join(sorted(rows_by_arm))}", ""]

    for arm in sorted(rows_by_arm):
        rows = rows_by_arm[arm]
        sep = mechanism_separation(rows)
        agree = human_agreement(rows)
        perr = parse_error_rate(rows)
        lat_mean, lat_median = _latency_stats(rows)
        lines += [
            f"## {arm}",
            f"- n: {len(rows)}",
            f"- mechanism separation: accept_mean={fmt(sep['accept_mean'])}"
            f"  goalonly_mean={fmt(sep['goalonly_mean'])}  gap={fmt(sep['gap'])}",
            f"- human agreement (judge-relevant subset): {agree['agree']}/{agree['n']}"
            f" ({fmt(agree['rate'])})",
            f"- parse error rate: {fmt(perr)}",
            f"- latency: mean={fmt(lat_mean)}s  median={fmt(lat_median)}s",
        ]
        jev = _jev_confidence_summary(rows)
        if jev is not None:
            dims = ", ".join(f"{k}={v:.2f}" for k, v in sorted(jev["mean_by_dim"].items()))
            lines.append(
                f"- jev confidence: mean by dimension [{dims}]; "
                f"low-confidence items (<{jev['low_confidence_threshold']}): "
                f"{jev['low_confidence_items']}/{len(rows)}")
        lines.append("")

    inter = inter_judge_agreement(rows_by_arm)
    lines.append("## Inter-judge agreement")
    if inter:
        for pair in sorted(inter):
            lines.append(f"- {pair}: {fmt(inter[pair])}")
    else:
        lines.append("- n/a (need at least two arms)")
    lines.append("")

    div = divergent_ids(rows_by_arm)
    lines.append(f"## Divergent ids ({len(div)})")
    lines += [f"- {i}" for i in div] if div else ["- none"]

    return "\n".join(lines) + "\n"


# --- main -----------------------------------------------------------------

_DEFAULT_OUT = (pathlib.Path(__file__).resolve().parents[1]
                / "docs/superpowers/spikes/2026-09-19-evidence-trust-core/judge-ab")


def build_parser():
    ap = argparse.ArgumentParser(
        description="Offline-testable A/B harness: deepseek vs gemini-flash vs jev "
                    "judge, over gold pulled read-only from inform.evidence_items.")
    ap.add_argument("--env-file", default=None,
                    help="Path to a .env with DATABASE_URL "
                         "(default: DATABASE_URL env var, else ev-accounts/backend/.env)")
    ap.add_argument("--sample", type=int, default=None,
                    help="Cap the number of gold items run (bounds LLM spend; also "
                         "the inter-judge-agreement sample size)")
    ap.add_argument("--out", default=str(_DEFAULT_OUT))
    return ap


def _build_arms() -> dict:
    """Build the three real arms. jev is optional: if `typesafe_sdk` isn't
    installed or TYPESAFE_API_KEY is missing, make_jev_arm() raises here
    and this skips jev with a clear printed note instead of crashing the
    deepseek/gemini-flash run too."""
    arms = {
        "deepseek": make_provider_arm("deepseek"),
        "gemini-flash": make_provider_arm("gemini-flash"),
    }
    try:
        arms["jev"] = make_jev_arm()
    except Exception as exc:  # ImportError (no typesafe-sdk) or the SDK's own error
        print(f"[judge_ab] skipping jev arm ({type(exc).__name__}: {exc})")
    return arms


def main(argv=None):
    args = build_parser().parse_args(argv)
    conn = data.connect(args.env_file)
    gold = load_gold(conn)
    if args.sample:
        gold = gold[:args.sample]
    if not gold:
        print("[judge_ab] no judge-relevant gold in inform.evidence_items "
              "(review_status IN ('accepted','rejected')) — nothing to run")
        return

    arms = _build_arms()
    rows_by_arm = run_ab(gold, arms, decide_fn=decide)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report_md = render_report(rows_by_arm, gold)
    (out / "judge_ab_report.md").write_text(report_md)
    (out / "judge_ab_items.json").write_text(json.dumps(rows_by_arm, indent=2))

    print(report_md)
    print(f"Wrote artifacts to {out}")


if __name__ == "__main__":
    main()

"""Pure scoring for the hub-lane comparable-source recall eval (Slice 2 Phase 4).

No network, no filesystem, no DB — importable and unit-tested offline. The online
runner (scripts/eval_hub_recall.py) supplies the found/accepted URLs and the
per-race hub domains; this module turns them into recall/precision numbers
against the hand-labeled ground truth.

Per race (GT = comparable ground-truth sources):
  A (addressable) = GT whose registrable domain is a scoped_search hub domain
                    applicable to the race (pointer-only hubs are dropped by the
                    runner before their domains reach here).
  R (retrieved)   = GT matched by any raw (pre-classify) found URL.
  V (verified)    = GT matched by any classifier-accepted URL.
Headline = addressable recall = |V ∩ A| / |A|.
"""
from __future__ import annotations

import re

# Multi-label public suffixes; kept tiny on purpose (the ground truth is all
# plain .gov/.org/.com). Extend only with evidence.
_MULTI_SUFFIXES = {"co.uk", "org.uk", "gov.uk", "com.au"}
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")


def normalize_url(url: str) -> str:
    """Canonical match form: lowercased, scheme/query/fragment/leading-www./
    trailing-slash removed. Path case is lowercased too — applied to both sides,
    so consistent for matching."""
    u = (url or "").strip().lower()
    u = u.split("#", 1)[0].split("?", 1)[0]
    u = _SCHEME.sub("", u)
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def registrable_domain(url_or_host: str) -> str:
    """Best-effort eTLD+1 from a URL or bare host."""
    u = normalize_url(url_or_host)
    host = u.split("/", 1)[0].split(":", 1)[0]
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    last2 = ".".join(labels[-2:])
    if last2 in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return last2


def filled_targets(gt_sources: list) -> list:
    """The recall TARGET set: only sources that actually carry the candidates'
    own words, i.e. `exists == "yes"`. `partial` sources (unfilled questionnaire,
    scheduled-not-yet-aired event, access-blocked, or unconfirmed) can never be
    verified, so counting them in a recall denominator would punish the lane for
    correctly rejecting an empty page. Preserves order; returns the same dicts."""
    return [s for s in gt_sources if s.get("exists") == "yes"]


def source_urls(source: dict) -> list:
    return [source["url"], *(source.get("accept_urls") or [])]


def source_in_urls(source: dict, urls: list) -> bool:
    targets = {normalize_url(u) for u in source_urls(source)}
    return any(normalize_url(u) in targets for u in urls)


def is_addressable(source: dict, hub_domains) -> bool:
    hub_regs = {registrable_domain(d) for d in hub_domains if d}
    return any(registrable_domain(u) in hub_regs for u in source_urls(source))


def score_run(gt_sources: list, hub_domains, found_urls: list, accepted_urls: list) -> list:
    return [{
        "id": s["id"],
        "addressable": is_addressable(s, hub_domains),
        "retrieved": source_in_urls(s, found_urls),
        "accepted": source_in_urls(s, accepted_urls),
    } for s in gt_sources]


def _ratio(num: int, den: int):
    return (num / den) if den else None


def recalls_from_per_source(rows: list) -> dict:
    addr = [r for r in rows if r["addressable"]]
    return {
        "n_gt": len(rows),
        "n_addressable": len(addr),
        "n_retrieved": sum(1 for r in rows if r["retrieved"]),
        "n_verified": sum(1 for r in rows if r["accepted"]),
        "addressable_recall": _ratio(sum(1 for r in addr if r["accepted"]), len(addr)),
        "overall_recall": _ratio(sum(1 for r in rows if r["accepted"]), len(rows)),
        "retrieval_recall_overall": _ratio(sum(1 for r in rows if r["retrieved"]), len(rows)),
        "retrieval_recall_addressable": _ratio(sum(1 for r in addr if r["retrieved"]), len(addr)),
    }


def precision(gt_sources: list, accepted_urls: list) -> dict:
    n = len(accepted_urls)
    if not n:
        return {"n_accepted": 0, "n_matched": 0, "precision": None}
    matched = sum(1 for u in accepted_urls if any(source_in_urls(s, [u]) for s in gt_sources))
    return {"n_accepted": n, "n_matched": matched, "precision": matched / n}


def majority(count: int, n_runs: int) -> bool:
    return 2 * count > n_runs


def majority_per_source(records: list, n_runs: int) -> list:
    return [{
        "id": r["id"], "addressable": r["addressable"],
        "retrieved": majority(r["retrieved_count"], n_runs),
        "accepted": majority(r["accepted_count"], n_runs),
    } for r in records]

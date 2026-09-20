from __future__ import annotations
from dataclasses import dataclass, field
from .models import Status
from .triage import registrable_domain


def item_key(item) -> str:
    return f"{item.politician_id}|{item.source_url}|{item.verbatim_text[:60]}"


@dataclass
class Metrics:
    total: int = 0
    green: int = 0
    flagged: int = 0
    dropped: int = 0
    leads: int = 0
    verbatim_pass_rate: float = 0.0
    primary_source_rate: float = 0.0
    per_domain_yield: dict = field(default_factory=dict)
    precision: "float | None" = None
    recall: "float | None" = None


def score(items, leads, gold) -> Metrics:
    m = Metrics(total=len(items), leads=len(leads))
    verbatim_pass = 0
    green_items = []
    for it in items:
        m.green += it.status == Status.GREEN.value
        m.flagged += it.status == Status.FLAGGED.value
        m.dropped += it.status == Status.DROPPED.value
        verbatim_pass += bool(it.gates.verbatim)
        if it.status == Status.GREEN.value:
            green_items.append(it)
            d = registrable_domain(it.source_url)
            m.per_domain_yield[d] = m.per_domain_yield.get(d, 0) + 1
    m.verbatim_pass_rate = verbatim_pass / m.total if m.total else 0.0
    if green_items:
        prim = sum(1 for it in green_items if it.source_type == "primary")
        m.primary_source_rate = prim / len(green_items)

    labels = (gold or {}).get("labels", {})
    if labels:
        agree = sum(1 for it in green_items if labels.get(item_key(it)) == "green")
        labeled = sum(1 for it in items if item_key(it) in labels)
        m.precision = agree / labeled if labeled else None

    sample = (gold or {}).get("recall_sample", {})
    if sample:
        found = expected = 0
        for url, n in sample.items():
            expected += int(n)
            found += min(int(n), sum(1 for it in green_items if it.source_url == url))
        m.recall = found / expected if expected else None
    return m

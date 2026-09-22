"""Pure row-builder: a run's evidence_items.json -> inform.evidence_items rows.

No DB or I/O. scripts/commit_evidence.py resolves the compass-topic map and
performs the dry-run/--commit write.
"""
from __future__ import annotations

WRITE_STATUSES = ("green", "flagged")


def _resolve_topic(issue: str, topic_key_to_id: dict):
    return topic_key_to_id.get((issue or "").strip().lower())


def build_rows(items: list, topic_key_to_id: dict) -> list:
    rows = []
    for it in items:
        if it.get("status") not in WRITE_STATUSES:
            continue
        issue = (it.get("issue") or "").strip()
        prov = it.get("provenance") or {}
        rows.append({
            "politician_id": it["politician_id"],
            "topic_id": _resolve_topic(issue, topic_key_to_id),
            "issue": issue,
            "evidence_type": it.get("evidence_type") or "quote",
            "verbatim_text": it["verbatim_text"],
            "source_url": it["source_url"],
            "deep_link": it.get("deep_link"),
            "context": it.get("context"),
            "source_type": it.get("source_type"),
            "source_cycle_year": it.get("source_cycle_year"),
            "machine_status": it["status"],
            "gate_flags": {"reasons": it.get("status_reasons") or [],
                           "gates": it.get("gates") or {}},
            "provenance": prov,
            "batch_id": prov.get("batch"),
            "review_status": "pending",
        })
    return rows

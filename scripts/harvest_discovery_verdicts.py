"""Export human triage verdicts into a real-labeled eval fixture.

Every approved/ingested row is a gold-relevant example; rejects are gold-
irrelevant ONLY when the reason is a relevance verdict (clip-not-original,
wrong-person, tier-5). stale/duplicate/other say nothing about relevance and
are skipped. Existing fixture lines win on source_key so hand corrections
survive re-harvests.

Usage:
  .venv/bin/python scripts/harvest_discovery_verdicts.py            # write
  .venv/bin/python scripts/harvest_discovery_verdicts.py --dry-run  # counts only
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src.discovery import db  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval_real.jsonl"

GOLD_FALSE_REASONS = {"clip-not-original", "wrong-person", "tier-5"}

QUERY = """
    select d.source_key, d.title, d.description_snippet, d.channel_name,
           d.duration_seconds, d.status, d.status_reason,
           coalesce(p.race_label, '(unknown race)'),
           coalesce((select array_agg(rc.full_name order by rc.full_name)
                     from essentials.race_candidates rc
                     where rc.race_id = d.race_id and rc.full_name is not null),
                    '{}')
    from essentials.discovered_sources d
    left join essentials.readrank_race_pipeline p on p.race_id = d.race_id
    where d.status in ('approved', 'ingested', 'rejected')
    order by d.created_at
"""


def to_example(row) -> "dict | None":
    (source_key, title, snippet, channel, duration, status, reason,
     race_label, roster) = row
    if status == "rejected" and reason not in GOLD_FALSE_REASONS:
        return None
    return {
        "title": title or "", "description": snippet or "",
        "channel": channel or "", "duration_seconds": duration,
        "race_label": race_label, "roster": list(roster or []),
        "gold_relevant": status in ("approved", "ingested"),
        "source_key": source_key,
    }


def merge_examples(existing: list, harvested: list) -> list:
    """source_key-keyed merge; existing fixture lines win (hand corrections)."""
    by_key = {}
    for ex in [e for e in harvested if e] + existing:
        by_key[ex["source_key"]] = ex   # later wins -> existing overrides
    return sorted(by_key.values(), key=lambda e: e["source_key"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(QUERY)
        rows = cur.fetchall()
    finally:
        conn.close()
    harvested = [to_example(r) for r in rows]
    kept = [e for e in harvested if e]
    existing = []
    if FIXTURE.exists():
        existing = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line]
    merged = merge_examples(existing, kept)
    gold_true = sum(1 for e in merged if e["gold_relevant"])
    print(f"triaged rows={len(rows)} harvestable={len(kept)} "
          f"merged={len(merged)} (gold_relevant={gold_true}, "
          f"gold_irrelevant={len(merged) - gold_true})")
    if args.dry_run:
        return 0
    FIXTURE.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in merged))
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

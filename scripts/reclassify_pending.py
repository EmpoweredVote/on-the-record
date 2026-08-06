"""One-shot: re-run the tier classifier over pending discovered_sources rows.

Run once after the 2026-08-05 tier recalibration deploys, so the triage
queue sorts coherently under the new ladder. Idempotent; harmless to re-run.

Usage:
  .venv/bin/python scripts/reclassify_pending.py [--dry-run] [--limit N]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src import config  # noqa: E402
from src.discovery import db, reclassify  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="classify and report, but roll back all updates")
    ap.add_argument("--limit", type=int, default=None,
                    help="only process the first N pending rows")
    args = ap.parse_args()
    provider = get_provider(config.DISCOVERY_MODEL_ACTIVE)
    conn = db.connect()
    moves = Counter()
    try:
        with conn.cursor() as cur:
            rows = reclassify.fetch_pending(cur)
            if args.limit is not None:
                rows = rows[: args.limit]
            print(f"pending rows: {len(rows)}")
            for row in rows:
                old_tier, new_tier = reclassify.reclassify_row(cur, provider, row)
                title = (row[2] or "")[:60]
                if new_tier is None:
                    moves["skipped_no_verdict"] += 1
                    print(f"  SKIPPED (no usable verdict) tier={old_tier} {title!r}")
                else:
                    moves[f"{old_tier}->{new_tier}"] += 1
                    marker = " " if old_tier == new_tier else "*"
                    print(f"  {marker} {old_tier} -> {new_tier} {title!r}")
        if args.dry_run:
            conn.rollback()
            print("DRY RUN — rolled back")
        else:
            conn.commit()
    finally:
        conn.close()
    print("moves:", dict(sorted(moves.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

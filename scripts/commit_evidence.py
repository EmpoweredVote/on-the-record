#!/usr/bin/env python3
# scripts/commit_evidence.py
"""Write a trust-core run's green+flagged evidence items to inform.evidence_items.

Dry-run by default; --commit writes (ON CONFLICT DO NOTHING). The migration
<slot>_evidence_items.sql must be applied first.

  ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/commit_evidence.py \
      docs/superpowers/spikes/2026-09-19-evidence-trust-core/evidence_items.json \
      [--commit] [--env-file PATH]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg2
import psycopg2.extras

from src.evidence.commit import build_rows
from src.evidence.data import database_url

_INSERT = """
INSERT INTO inform.evidence_items
  (politician_id, topic_id, issue, evidence_type, verbatim_text, source_url, deep_link,
   context, source_type, source_cycle_year, machine_status, gate_flags, provenance,
   batch_id, review_status)
VALUES
  (%(politician_id)s, %(topic_id)s, %(issue)s, %(evidence_type)s, %(verbatim_text)s,
   %(source_url)s, %(deep_link)s, %(context)s, %(source_type)s, %(source_cycle_year)s,
   %(machine_status)s, %(gate_flags)s, %(provenance)s, %(batch_id)s, %(review_status)s)
ON CONFLICT (politician_id, source_url, md5(lower(verbatim_text))) DO NOTHING
"""


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact", help="Path to a run's evidence_items.json")
    ap.add_argument("--commit", action="store_true", help="Write (default: dry run)")
    ap.add_argument("--env-file", default=None)
    return ap


def load_topic_map(conn) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT lower(topic_key), id::text FROM inform.compass_topics")
    return {k: v for k, v in cur.fetchall()}


def main(argv=None):
    args = build_parser().parse_args(argv)
    items = json.loads(pathlib.Path(args.artifact).read_text())
    conn = psycopg2.connect(database_url(args.env_file))
    try:
        rows = build_rows(items, load_topic_map(conn))
        mapped = sum(1 for r in rows if r["topic_id"])
        print(f"{len(rows)} rows to write "
              f"({sum(r['machine_status']=='green' for r in rows)} green / "
              f"{sum(r['machine_status']=='flagged' for r in rows)} flagged; "
              f"{mapped} compass-mapped, {len(rows)-mapped} free-issue)")
        for r in rows:
            print(f"  [{r['machine_status']}] {r['issue']}  {r['verbatim_text'][:70]}")
        if not args.commit:
            print("\nDRY RUN — nothing written. Re-run with --commit.")
            return
        cur = conn.cursor()
        for r in rows:
            p = dict(r, gate_flags=psycopg2.extras.Json(r["gate_flags"]),
                     provenance=psycopg2.extras.Json(r["provenance"]))
            cur.execute(_INSERT, p)
        conn.commit()
        print(f"\nCommitted {len(rows)} row(s) (existing rows skipped by the dedup key).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

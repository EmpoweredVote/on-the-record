#!/usr/bin/env python3
"""Seed essentials.readrank_race_pipeline from pass-B research artifacts.

Usage:
  .venv/bin/python seed_from_research.py <artifact.json> [--commit] [--env-file PATH]

Lane files with a "races" array INSERT queue rows (race_id null -> status needs_race;
unverified races land as blocked). The house-classification lane instead UPDATES
priority_tier (5 contested / 6 not) on existing us_house rows, matching districts out
of position_name formats like 'U.S. Representative District 9', 'U.S. House MA-01',
'U.S. Representative At-Large'.
Always dry-runs unless --commit.
"""
import argparse, json, os, re, sys
import psycopg2, psycopg2.extras

DEFAULT_ENV = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")

def load_database_url(env_file):
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    with open(env_file) as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    sys.exit(f"No DATABASE_URL in env or {env_file}")

def district_of(position_name):
    m = re.search(r"District\s+(\d+)", position_name, re.I)
    if m: return int(m.group(1))
    m = re.search(r"-(\d{2})\b", position_name)
    if m: return int(m.group(1))
    if re.search(r"At-Large", position_name, re.I): return 0
    return None

def possible_dups(cur, r, run_start):
    """Pass A seeded existing races under DB-derived labels; research lanes use their own.
    Anything already queued for the same slot needs a human eye before we add a second row.
    Rows inserted during this run are excluded (created_at >= run_start) — distinct races in
    one lane legitimately share (state, category, date, kind), e.g. D and R primaries."""
    cur.execute("""
      select race_label from essentials.readrank_race_pipeline
      where state = %s and office_category = %s and election_date = %s and election_kind = %s
        and created_at < %s
    """, (r["state"], r["office_category"], r["election_date"], r["election_kind"], run_start))
    labels = [row["race_label"] for row in cur.fetchall()]
    if r["office_category"] == "us_house":
        # same-state house races only collide when the district matches
        d = district_of(r["race_label"])
        labels = [l for l in labels if district_of(l) == d]
    elif r["office_category"].startswith("local_"):
        # many distinct local offices share one (state, date, kind) slot; only an
        # exact office label is a duplicate
        labels = [l for l in labels if l.casefold() == r["race_label"].casefold()]
    return labels

def seed_races(cur, data):
    cur.execute("select now() as t")
    run_start = cur.fetchone()["t"]
    inserted = skipped = blocked = dup_flagged = 0
    for r in data["races"]:
        dups = possible_dups(cur, r, run_start)
        if dups:
            dup_flagged += 1
            print(f"  POSSIBLE DUP (skipping insert): {r['race_label']!r} vs existing {dups}")
            continue
        status = "needs_race"
        reason = None
        if not r.get("verified", False):
            status, reason = "blocked", f"unverified research: {r.get('verify_notes','')[:200]}"
            blocked += 1
        if r.get("contested") is False and status != "blocked":
            status, reason = "skipped", r.get("notes") or "uncontested"
        cur.execute("""
          insert into essentials.readrank_race_pipeline
            (race_label, state, office_category, election_date, election_kind,
             priority_tier, status, status_reason, notes, source_urls)
          values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          on conflict (race_label, election_date) where race_id is null do nothing
          returning id
        """, (r["race_label"], r["state"], r["office_category"], r["election_date"],
              r["election_kind"], r["priority_tier"], status, reason,
              json.dumps(r["candidates"]),
              r.get("sources", [])))
        if cur.fetchone():
            inserted += 1
        else:
            skipped += 1
    print(f"insert: {inserted} new, {skipped} already present, {blocked} blocked-unverified, "
          f"{dup_flagged} possible-dup (NOT inserted - resolve by hand)")

def classify_house(cur, data):
    cur.execute("""
      select p.id, p.state, r.position_name
      from essentials.readrank_race_pipeline p
      join essentials.races r on r.id = p.race_id
      where p.office_category = 'us_house' and p.election_kind = 'general'
    """)
    rows = cur.fetchall()
    lookup = {(d["state"], d["district"]): d["contested"] for d in data["districts"]}
    hits = misses = 0
    for row in rows:
        dist = district_of(row["position_name"])
        key = (row["state"], dist)
        if dist is None or key not in lookup:
            misses += 1
            print(f"  no match: {row['state']} {row['position_name']}")
            continue
        tier = 5 if lookup[key] else 6
        cur.execute("update essentials.readrank_race_pipeline set priority_tier=%s, updated_at=now() where id=%s",
                    (tier, row["id"]))
        hits += 1
    print(f"classified {hits} house rows, {misses} unmatched (fix by hand)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact"); ap.add_argument("--commit", action="store_true")
    ap.add_argument("--env-file", default=DEFAULT_ENV)
    args = ap.parse_args()
    data = json.load(open(args.artifact))
    conn = psycopg2.connect(load_database_url(args.env_file))
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if "districts" in data:
        classify_house(cur, data)
    else:
        seed_races(cur, data)
    if args.commit:
        conn.commit(); print("COMMITTED")
    else:
        conn.rollback(); print("DRY RUN — rolled back (use --commit)")

if __name__ == "__main__":
    main()

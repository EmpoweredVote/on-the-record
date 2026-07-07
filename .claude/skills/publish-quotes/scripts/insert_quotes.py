#!/usr/bin/env python3
"""Insert curated quotes into ev-accounts essentials.quotes as NOT-live drafts.

Dry-run by default. Pass --commit to actually write.

Reads a JSON batch file:
{
  "politician_id": "9a60d603-...",          # OR "politician_name": "Steve Hilton"
  "topic_key": "abortion",                    # batch default; must exist in inform.compass_topics
  "source_url": "https://www.youtube.com/watch?v=VIZ1h4OaImU",  # batch default
  "quotes": [
    {"text": "...", "editor_note": "Clearest statement of the stance.", "timestamp_seconds": 919},
    {"text": "...", "editor_note": "Edited for clarity, meaning preserved.",
     "deidentified": "custom obscured text", "timestamp_seconds": 974},
    {"text": "...", "editor_note": "From a different clip.",
     "topic_key": "housing", "source_url": "https://www.youtube.com/watch?v=OTHER"}
  ]
}

Per quote:
  - editor_note   -> REQUIRED, non-empty. Why this quote was selected and/or what was
                     edited and why. The script refuses the whole batch if any quote
                     is missing one.
  - topic_key, source_url -> optional per-quote overrides of the batch-level defaults,
                     so one batch can span multiple topics/sources (e.g. a curation
                     page export).
  - deidentified  -> defaults to a VERBATIM copy of text (house norm). Required non-null
                     so the row is selectable in the admin tool.
  - timestamp_seconds (optional) -> appended to a YouTube source_url as &t=<n>s to deep-link
                     the exact moment.

Every row is inserted with readrank_selected = FALSE (not live). Choosing the single
live quote per (politician, topic) is a human step in /admin/readrank-quotes.
The script also warns (does not block) when a topic exceeds the house cap of 2 drafts.

Run with the on-the-record venv:
  .venv/bin/python <skill>/scripts/insert_quotes.py batch.json            # dry run
  .venv/bin/python <skill>/scripts/insert_quotes.py batch.json --commit   # write
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse, urlsplit

import psycopg2
import psycopg2.extras

# ev-accounts is where essentials.quotes lives — DIFFERENT DB from the on-the-record pipeline.
DEFAULT_ENV = "/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/.env"


def load_database_url(env_file):
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if env_file and os.path.exists(env_file):
        with open(env_file) as fh:
            for line in fh:
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    sys.exit(f"No DATABASE_URL in env or {env_file}")


def with_timestamp(source_url, seconds):
    """Append &t=<n>s to a YouTube watch URL. Non-YouTube URLs are returned unchanged."""
    if seconds is None:
        return source_url
    host = urlsplit(source_url).netloc.lower()
    if "youtube.com" not in host and "youtu.be" not in host:
        return source_url
    sep = "&" if "?" in source_url else "?"
    return f"{source_url}{sep}t={int(seconds)}s"


def build_insert_rows(batch, politician_id):
    """Assemble (politician_id, topic_key, quote_text, deidentified_text, source_name,
    source_url, editor_note) tuples for insertion. Pure — no DB access.

    Each quote requires a non-empty editor_note (why selected / what edited & why).
    Per-quote topic_key and source_url override the batch-level defaults. Raises
    ValueError on a missing/blank editor_note, or a quote with no topic_key or
    source_url from either level.
    """
    default_topic = (batch.get("topic_key") or "").strip().lower()
    default_source = (batch.get("source_url") or "").strip()
    rows = []
    for i, q in enumerate(batch["quotes"], 1):
        note = (q.get("editor_note") or "").strip()
        if not note:
            raise ValueError(f"quote #{i} is missing a non-empty editor_note")
        topic_key = (q.get("topic_key") or default_topic).strip().lower()
        if not topic_key:
            raise ValueError(f"quote #{i} has no topic_key (per-quote or batch-level)")
        base_url = (q.get("source_url") or default_source).strip()
        if not base_url:
            raise ValueError(f"quote #{i} has no source_url (per-quote or batch-level)")
        text = q["text"].strip()
        deid = (q.get("deidentified") or text).strip()  # verbatim by default
        source_name = urlparse(base_url).netloc
        url = with_timestamp(base_url, q.get("timestamp_seconds"))
        rows.append((politician_id, topic_key, text, deid, source_name, url, note))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", help="Path to JSON batch file")
    ap.add_argument("--commit", action="store_true", help="Actually write (default: dry run)")
    ap.add_argument("--env-file", default=DEFAULT_ENV, help="Path to .env with DATABASE_URL")
    args = ap.parse_args()

    with open(args.batch) as fh:
        batch = json.load(fh)

    if not batch.get("quotes"):
        sys.exit("No quotes in batch.")

    conn = psycopg2.connect(load_database_url(args.env_file))
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 1. Resolve / verify politician.
    pid = batch.get("politician_id")
    if pid:
        cur.execute(
            "SELECT id, COALESCE(full_name, TRIM(COALESCE(preferred_name, first_name)||' '||last_name)) AS name "
            "FROM essentials.politicians WHERE id = %s", (pid,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"No politician with id {pid}")
    else:
        name = batch["politician_name"].strip()
        cur.execute(
            "SELECT id, COALESCE(full_name, TRIM(COALESCE(preferred_name, first_name)||' '||last_name)) AS name "
            "FROM essentials.politicians "
            "WHERE COALESCE(full_name, TRIM(COALESCE(preferred_name, first_name)||' '||last_name)) ILIKE %s",
            (f"%{name}%",))
        rows = cur.fetchall()
        if len(rows) != 1:
            sys.exit(f"Name '{name}' matched {len(rows)} politicians; use politician_id instead.")
        row = rows[0]
        pid = row["id"]
    print(f"Politician: {row['name']}  ({pid})")

    # 2. Build rows (enforces the editor_note gate + per-quote overrides).
    rows_to_insert = build_insert_rows(batch, pid)

    # 3. Validate every distinct topic_key against the canonical compass spine.
    for tk in sorted({r[1] for r in rows_to_insert}):
        cur.execute("SELECT 1 FROM inform.compass_topics WHERE lower(topic_key) = %s", (tk,))
        if not cur.fetchone():
            sys.exit(f"topic_key '{tk}' is not in inform.compass_topics. Pick a canonical key.")
    print(f"Topics: {', '.join(sorted({r[1] for r in rows_to_insert}))}")

    # House cap: at most 2 drafts per (politician, topic). Warn, don't block.
    from collections import Counter
    per_topic = Counter(r[1] for r in rows_to_insert)
    for tk, n in per_topic.items():
        if n > 2:
            print(f"  WARNING: {n} quotes for topic '{tk}' — house cap is 2 drafts per topic.")

    print(f"\n{'DRY RUN — nothing written' if not args.commit else 'COMMITTING'}: "
          f"{len(rows_to_insert)} quote(s), readrank_selected = FALSE\n")
    for i, (_, tk, text, deid, _, url, note) in enumerate(rows_to_insert, 1):
        print(f"  #{i} [{tk}] {url}")
        print(f"     text:  {text[:90]}{'…' if len(text) > 90 else ''}")
        print(f"     deid:  {'(verbatim)' if deid == text else deid[:90]}")
        print(f"     note:  {note[:90]}{'…' if len(note) > 90 else ''}")

    if not args.commit:
        print("\nRe-run with --commit to write.")
        return

    try:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO essentials.quotes "
            "(politician_id, topic_key, quote_text, deidentified_text, source_name, source_url, "
            " editor_note, readrank_selected, created_at, updated_at) VALUES %s",
            rows_to_insert,
            template="(%s, %s, %s, %s, %s, %s, %s, false, now(), now())",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"\nInserted {len(rows_to_insert)} row(s).")

    # 4. Verify: show all quotes for this politician across the inserted topics.
    topics = sorted({r[1] for r in rows_to_insert})
    cur.execute(
        "SELECT lower(topic_key) AS topic_key, readrank_selected, "
        "       (deidentified_text = quote_text) AS verbatim, source_url, "
        "       left(quote_text,55) AS preview, (editor_note IS NOT NULL) AS has_note "
        "FROM essentials.quotes "
        "WHERE politician_id = %s AND lower(topic_key) = ANY(%s) "
        "ORDER BY lower(topic_key), created_at NULLS FIRST, id",
        (pid, topics))
    print(f"\nInserted quotes for this politician ({', '.join(topics)}):")
    for r in cur.fetchall():
        live = "LIVE" if r["readrank_selected"] else "draft"
        print(f"  [{live}] {r['topic_key']}  verbatim={r['verbatim']} note={r['has_note']}\n        {r['preview']}")
    print("\nNext: pick the single live quote per topic in /admin/readrank-quotes.")


if __name__ == "__main__":
    main()

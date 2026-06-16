#!/usr/bin/env python3
"""Consistency check: compare transcript_named.json files on disk against DB.

Flags:
  - Meeting on disk but missing from DB ("not published")
  - Meeting in DB but segment count differs from disk ("segment mismatch")
  - Meeting in DB but summary JSONB is null while summary.json exists on disk

Exit code: 0 if all clean, 1 if any drift detected.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_DIR))

_env_file = _REPO_DIR / ".env.local"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

from src import config  # noqa: E402 — must follow .env.local load


def _count_segments(named_path: Path) -> int:
    """Return segment count from transcript_named.json."""
    with open(named_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return sum(1 for s in data.get("segments", []) if s.get("text", "").strip())


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL not set. Add it to .env.local.")
        return 1

    import psycopg2

    meetings_dir = config.MEETINGS_DIR
    if not meetings_dir.exists():
        print(f"No meetings directory at {meetings_dir}")
        return 0

    disk_meetings: dict[str, dict] = {}
    for mdir in sorted(meetings_dir.iterdir()):
        if not mdir.is_dir() or mdir.name.startswith("."):
            continue
        named = mdir / "transcript_named.json"
        if not named.exists():
            continue
        seg_count = _count_segments(named)
        has_summary_file = (mdir / "summary.json").exists()
        disk_meetings[mdir.name] = {
            "dir": mdir,
            "segments": seg_count,
            "has_summary_file": has_summary_file,
        }

    if not disk_meetings:
        print("No processed meetings found on disk.")
        return 0

    print(f"Checking {len(disk_meetings)} meeting(s) on disk against DB...\n")

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(disk_meetings))
            cur.execute(
                f"""
                SELECT slug, segment_count, summary IS NOT NULL AS has_summary
                FROM meetings.meetings
                WHERE slug IN ({placeholders})
                """,
                list(disk_meetings.keys()),
            )
            db_rows = {row[0]: {"segments": row[1], "has_summary": row[2]} for row in cur.fetchall()}
    finally:
        conn.close()

    issues: list[str] = []
    ok_count = 0

    for slug, disk in sorted(disk_meetings.items()):
        if slug not in db_rows:
            issues.append(f"  NOT PUBLISHED  {slug}  ({disk['segments']} segs on disk)")
            continue

        db = db_rows[slug]
        row_issues = []

        if db["segments"] != disk["segments"]:
            row_issues.append(
                f"segment count: disk={disk['segments']} db={db['segments']}"
            )

        if disk["has_summary_file"] and not db["has_summary"]:
            row_issues.append("summary.json exists on disk but DB summary is null")

        if row_issues:
            issues.append(f"  MISMATCH       {slug}  — {'; '.join(row_issues)}")
        else:
            ok_count += 1

    if issues:
        print(f"{'='*60}")
        print(f"DRIFT DETECTED ({len(issues)} issue(s)):")
        print(f"{'='*60}")
        for line in issues:
            print(line)
        print(f"\n{ok_count} meeting(s) clean, {len(issues)} with issues.")
        print("\nTo fix: re-publish affected meetings with:")
        print("  python run_local.py --publish-meeting <meeting-id>")
        return 1

    print(f"All {ok_count} meeting(s) are consistent with the DB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

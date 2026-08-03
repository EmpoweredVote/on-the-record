"""Seed source_outlets from channels proven by already-ingested meetings.

Usage:
  .venv/bin/python scripts/harvest_outlets.py            # dry-run: print what would be inserted
  .venv/bin/python scripts/harvest_outlets.py --apply    # insert (idempotent on feed_url)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src import config  # noqa: E402


def collect_channel_candidates(meetings_dir: Path) -> list:
    """(channel_name, sample_video_url) per distinct channel, from
    transcript_named.json of every local meeting. Pure; unit-tested."""
    seen = {}
    for child in sorted(p for p in meetings_dir.iterdir() if p.is_dir()):
        tn = child / "transcript_named.json"
        if not tn.exists():
            continue
        try:
            data = json.loads(tn.read_text())
        except (ValueError, OSError):
            continue
        url = data.get("audio_source") or ""
        channel = (data.get("processing_metadata") or {}).get("source_channel")
        if not channel or "youtube.com" not in url and "youtu.be" not in url:
            continue
        seen.setdefault(channel, url)
    return sorted(seen.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="insert rows (default: dry-run)")
    args = ap.parse_args()

    from src.discovery import db  # noqa: PLC0415 — after load_env_local
    from src.ingest import fetch_source_metadata

    candidates = collect_channel_candidates(config.MEETINGS_DIR)
    print(f"{len(candidates)} distinct channels found locally")
    rows = []
    for channel_name, sample_url in candidates:
        meta = fetch_source_metadata(sample_url)
        channel_id = meta.get("channel_id")
        if not channel_id:
            print(f"SKIP {channel_name}: could not resolve channel_id", file=sys.stderr)
            continue
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        rows.append((meta.get("channel") or channel_name, channel_id, feed_url))
        print(f"OUTLET {channel_name} -> {feed_url}")
    if not args.apply:
        print(f"DRY-RUN: {len(rows)} outlets would be inserted (pass --apply)")
        return 0
    conn = db.connect()
    try:
        inserted = 0
        with conn:
            with conn.cursor() as cur:
                for name, channel_id, feed_url in rows:
                    cur.execute("""
                        insert into essentials.source_outlets
                          (name, kind, feed_url, external_channel_id, added_via, notes)
                        values (%s, 'youtube_channel', %s, %s, 'seed',
                                'harvested from ingested meetings')
                        on conflict (feed_url) do nothing
                        returning id
                    """, (name, feed_url, channel_id))
                    if cur.fetchone() is not None:
                        inserted += 1
        print(f"INSERTED {inserted} outlets ({len(rows) - inserted} already present)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

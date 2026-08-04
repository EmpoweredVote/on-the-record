"""Poll discovery sources: watchlist feeds + due per-race YouTube sweeps.

Usage:
  .venv/bin/python scripts/poll_discovery.py                # full daily run
  .venv/bin/python scripts/poll_discovery.py --dry-run      # prefilter only, no LLM, no writes
  .venv/bin/python scripts/poll_discovery.py --race RACE_ID # force-sweep one race
  .venv/bin/python scripts/poll_discovery.py --skip-sweeps  # watchlist only
  .venv/bin/python scripts/poll_discovery.py --skip-watchlist
  .venv/bin/python scripts/poll_discovery.py --classify-cap N
  .venv/bin/python scripts/poll_discovery.py --print-alarms # zero-source races, then exit
"""
import argparse
import datetime as dt
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()  # before src.config so CS_DATA_DIR / API keys are visible

from src import config  # noqa: E402
from src.discovery import db, engine, feeds, search  # noqa: E402
from src.llm_providers import get_provider  # noqa: E402
from src.source_key import source_key  # noqa: E402


def _meeting_source_keys() -> set:
    """Every already-processed source, computed once per run (the per-item
    find_meeting_by_source scan would be O(items x meetings))."""
    from gui.runner import _meeting_source_key
    if not config.MEETINGS_DIR.exists():
        return set()
    keys = set()
    for child in sorted(config.MEETINGS_DIR.iterdir()):
        if child.is_dir():
            key = _meeting_source_key(child)
            if key:
                keys.add(key)
    return keys


def _captions_fetcher(url: str):
    from src.download import download_captions_via_ytdlp
    cache = config.DISCOVERY_DIR / "captions"
    cache.mkdir(parents=True, exist_ok=True)
    safe = hashlib.sha256(source_key(url).encode("utf-8")).hexdigest()[:24]
    dest = cache / f"{safe}.vtt"
    if dest.exists():
        return dest.read_text(encoding="utf-8", errors="replace")
    path = download_captions_via_ytdlp(url, dest)
    if path is None:
        return None
    return Path(path).read_text(encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--race", help="race_id: sweep this race now regardless of cadence")
    ap.add_argument("--skip-watchlist", action="store_true")
    ap.add_argument("--skip-sweeps", action="store_true")
    ap.add_argument("--classify-cap", type=int, default=None)
    ap.add_argument("--print-alarms", action="store_true")
    ap.add_argument("--trigger", choices=("scheduled", "manual"), default="manual",
                    help="how this run started (the launchd plist passes scheduled)")
    args = ap.parse_args()

    conn = db.connect()
    try:
        if args.print_alarms:
            rows = db.alarm_races(conn.cursor())
            if not rows:
                print("No zero-source alarms.")
            for race_id, label, date in rows:
                print(f"ALARM {date} {label} ({race_id}) — no approved sources; "
                      "run an agent gap-filler (see docs/runbooks/source-discovery.md)")
            return 0

        provider = get_provider(config.DISCOVERY_MODEL_ACTIVE)
        run_id = None
        if not args.dry_run:
            cur = conn.cursor()
            run_id = db.insert_run(cur, "race" if args.race else args.trigger)
            conn.commit()   # crash after this point leaves a visible started row
        stats = engine.run_discovery(
            conn,
            provider=provider,
            fetch_feed_items=feeds.fetch_outlet_items,
            ytsearch_fn=search.ytsearch,
            hydrate_fn=search.hydrate_item,
            captions_fetcher=_captions_fetcher,
            sleep_fn=time.sleep,
            meeting_keys=_meeting_source_keys(),
            today=dt.date.today(),
            dry_run=args.dry_run,
            race_filter=args.race,
            classify_cap=args.classify_cap,
            skip_watchlist=args.skip_watchlist,
            skip_sweeps=args.skip_sweeps,
        )
        print(f"DONE examined={stats.examined} queued={stats.inserted_pending} "
              f"auto_filtered={stats.inserted_auto_filtered} "
              f"prefiltered_out={stats.prefiltered_out} seen={stats.skipped_seen} "
              f"classified={stats.classified} capped={stats.spend_capped}")
        alarms = db.alarm_races(conn.cursor())
        if run_id is not None:
            cur = conn.cursor()
            db.finish_run(cur, run_id, stats)
            conn.commit()   # run record is the payload — commit before alarms so an
            db.record_alarms(cur, [a[0] for a in alarms])
            conn.commit()   # alarm-write hiccup can't roll the run record back with it
        for alarm in alarms:
            print(f"ALARM {alarm[2]} {alarm[1]} — no approved sources")
        if stats.failures:
            print(f"{len(stats.failures)} failure(s)", file=sys.stderr)
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the tier-3 defer sweep once and report how many items moved.

Use while the discovery poll is paused to clear the existing backlog, or any
time to re-file low-value items. Idempotent (only touches status='pending').

  .venv/bin/python scripts/defer_low_value.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src.discovery import db  # noqa: E402


def main() -> int:
    conn = db.connect()
    try:
        cur = conn.cursor()
        n = db.apply_tier3_defer(cur)
        conn.commit()
        print(f"DEFERRED {n} low-value items")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

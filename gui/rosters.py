"""List locally cached body rosters for the new-meeting Body picker. Mirrors
run_local._list_cached_rosters without importing the heavy CLI module."""
from __future__ import annotations

import json

from src import config


def list_cached_rosters() -> list[tuple[str, str]]:
    """[(slug, label), ...] for each cached roster in CONFIG_DIR/rosters/*.json,
    sorted by filename. label is 'Body Name (N members)', falling back to the slug."""
    rosters_dir = config.CONFIG_DIR / "rosters"
    out: list[tuple[str, str]] = []
    if not rosters_dir.exists():
        return out
    for path in sorted(rosters_dir.glob("*.json")):
        slug = path.stem
        label = slug
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            body_key = data.get("body_key") or slug
            count = len(data.get("politicians", []))
            label = f"{body_key} ({count} members)"
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        out.append((slug, label))
    return out

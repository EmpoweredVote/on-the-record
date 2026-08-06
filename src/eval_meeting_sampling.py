"""Deterministic meeting selection shared by the summary-model eval scripts
(scripts/eval_summary_classify.py, scripts/generate_summary_ab.py).

discover_meetings() is the only piece that touches the filesystem (a glob +
one small JSON read per candidate meeting, just enough to read event_kind).
select_diverse_sample() is pure and unit-tested on its own.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path


def discover_meetings(meetings_dir: Path) -> list[tuple[str, str]]:
    """[(meeting_id, event_kind), ...] for every dir under meetings_dir that has
    BOTH transcript_named.json and summary.json, sorted by meeting_id.

    event_kind is "unknown" when the transcript is missing/unparseable or the
    field is absent — callers still get a stable, includable entry rather than
    a silent drop, since diversity-bucketing treats "unknown" as just another
    bucket.
    """
    out = []
    for tpath_str in sorted(glob.glob(str(Path(meetings_dir) / "*" / "transcript_named.json"))):
        tpath = Path(tpath_str)
        mdir = tpath.parent
        meeting_id = mdir.name
        if not (mdir / "summary.json").exists():
            continue
        event_kind = "unknown"
        try:
            data = json.loads(tpath.read_text(encoding="utf-8"))
            event_kind = data.get("event_kind") or "unknown"
        except (json.JSONDecodeError, OSError):
            pass
        out.append((meeting_id, event_kind))
    return out


def select_diverse_sample(meetings: list[tuple[str, str]], limit: int) -> list[str]:
    """Deterministic sample of up to `limit` meeting ids.

    Round-robins across event-kind buckets (each bucket already sorted by
    meeting_id, kinds visited in sorted order) so a small --limit still spans
    multiple kinds instead of exhausting the alphabetically-first kind. Fully
    deterministic given the same `meetings` input — no randomness.
    """
    if limit <= 0:
        return []
    buckets: dict[str, list[str]] = {}
    for meeting_id, kind in meetings:
        buckets.setdefault(kind, []).append(meeting_id)
    kinds = sorted(buckets)
    selected: list[str] = []
    if not kinds:
        return selected
    i = 0
    remaining = sum(len(v) for v in buckets.values())
    while len(selected) < limit and remaining > 0:
        kind = kinds[i % len(kinds)]
        if buckets[kind]:
            selected.append(buckets[kind].pop(0))
            remaining -= 1
        i += 1
    return selected

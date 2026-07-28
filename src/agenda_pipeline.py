"""Orchestration for the agenda poller (pure parts; I/O stays in the script).

Change detection: OnBoard file `created`/`updated` timestamps form a marker
per meeting (OnBoardMeeting.agenda_updated_marker); unchanged marker → skip.
State lives in a JSON file under the CouncilScribe drive (atomic
tempfile+replace, like src/checkpoint.py). Failed fetch/parse/interpret must
be LOGGED WORK, never a silent skip — the coverage metric depends on it
(spec: quality gates).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorkItem:
    slug: str
    meeting: object          # OnBoardMeeting
    date: str                # YYYY-MM-DD (from meeting.start, body-local)


class PollState:
    """Persistent slug -> agenda marker map (atomic write, like checkpoint.py)."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._seen: dict[str, str] = {}
        if self._path.exists():
            self._seen = json.loads(self._path.read_text())

    def marker_for(self, slug: str) -> Optional[str]:
        return self._seen.get(slug)

    def record(self, slug: str, marker: str) -> None:
        self._seen[slug] = marker
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent)
        with os.fdopen(fd, "w") as fh:
            json.dump(self._seen, fh, indent=2)
        os.replace(tmp, self._path)


def plan_work(meetings, state: "PollState", *, body_slug: str):
    """Split fetched meetings into (work, skipped-with-reasons).

    Looks markers up directly on PollState so callers can't pass a stale dict.
    Slug construction must stay identical to publish.scheduled_slug (the
    video pass reuses it as the join key) — see test_slug_consistency_with_publish.
    """
    work: list[WorkItem] = []
    skipped: list[tuple[str, str]] = []
    for m in meetings:
        date = m.start[:10]
        slug = f"{body_slug}-{date}"
        if not m.agenda_url:
            skipped.append((slug, "no agenda posted yet"))
            continue
        if state.marker_for(slug) == m.agenda_updated_marker:
            skipped.append((slug, "agenda unchanged"))
            continue
        work.append(WorkItem(slug=slug, meeting=m, date=date))
    return work, skipped


def download_file(url: str, dest: Path) -> Path:
    """Download an OnBoard document (agenda/memo PDF) to dest. Shared by the
    poller and publish.reconcile_memo so UA/timeout live in one place."""
    import requests

    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest

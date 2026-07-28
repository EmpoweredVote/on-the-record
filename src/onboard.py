"""Client for Bloomington's OnBoard meetings JSON API.

Endpoint: https://bloomington.in.gov/onboard/meetings?format=json&start=&end=
Real shape (captured 2026-07-27, tests/fixtures/onboard/meetings_window_2026.json):
  {date: {time: [meeting, ...]}} — meeting has id/title/start/end/location/files.
  files is a dict keyed by type ("Agenda", "Packet", ...) -> LIST of file
  entries when populated, but an EMPTY JSON LIST when empty (PHP array
  quirk); a type key can hold multiple entries (amended agendas) — we
  surface the latest-created one. Canceled meetings keep their slot with a
  "CANCELED -" style title prefix (several spelling variants); they are
  excluded here.

House style: pure parsing + injected fetch (see src/house_cdn.py).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

BASE_URL = "https://bloomington.in.gov/onboard/meetings"


def _default_fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed gov host)
        return resp.read().decode("utf-8")


@dataclass
class OnBoardFile:
    file_type: str
    url: str
    filename: str
    created: Optional[str] = None
    updated: Optional[str] = None


@dataclass
class OnBoardMeeting:
    onboard_id: str
    title: str
    start: str
    end: Optional[str] = None
    location: Optional[str] = None
    files: list = field(default_factory=list)

    def _latest_file(self, file_type: str) -> Optional[OnBoardFile]:
        candidates = [f for f in self.files if f.file_type == file_type]
        if not candidates:
            return None
        return max(candidates, key=lambda f: f.created or "")

    @property
    def agenda_url(self) -> Optional[str]:
        f = self._latest_file("Agenda")
        return f.url if f else None

    @property
    def agenda_created(self) -> Optional[str]:
        f = self._latest_file("Agenda")
        return f.created if f else None

    @property
    def packet_url(self) -> Optional[str]:
        f = self._latest_file("Packet")
        return f.url if f else None

    @property
    def agenda_updated_marker(self) -> str:
        """Change-detection key: agenda url + latest created/updated stamp."""
        f = self._latest_file("Agenda")
        if f is None:
            return ""
        return f"{f.url}|{f.updated or f.created or ''}"


def _is_canceled(title: str) -> bool:
    return title.strip().lower().startswith("cancel")


def _parse_files(raw: object) -> list:
    """Guard the files polymorphism: dict-of-type->list when populated,
    empty JSON list when empty."""
    files: list = []
    if not isinstance(raw, dict):
        return files
    for file_type, entries in raw.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            files.append(
                OnBoardFile(
                    file_type=str(entry.get("type") or file_type),
                    url=url,
                    filename=str(entry.get("filename") or ""),
                    created=entry.get("created"),
                    updated=entry.get("updated"),
                )
            )
    return files


def _parse_meeting(raw: object) -> Optional[OnBoardMeeting]:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    start = raw.get("start")
    onboard_id = raw.get("id")
    if not title or not start or onboard_id is None:
        return None
    return OnBoardMeeting(
        onboard_id=str(onboard_id),
        title=str(title),
        start=str(start),
        end=raw.get("end"),
        location=raw.get("location"),
        files=_parse_files(raw.get("files")),
    )


def fetch_meetings_window(
    start: str,
    end: str,
    *,
    title_prefix: str,
    fetch: Callable[[str], str] = _default_fetch,
) -> list[OnBoardMeeting]:
    """Fetch meetings in ["YYYY-MM-DD" start, end] whose title starts with
    `title_prefix`, excluding canceled ones, sorted by start time.

    Returns [] on fetch/parse failure or malformed payloads.
    """
    url = f"{BASE_URL}?format=json&start={start}&end={end}"
    try:
        doc = json.loads(fetch(url))
    except Exception:
        return []
    if not isinstance(doc, dict):
        return []
    meetings: list[OnBoardMeeting] = []
    for by_time in doc.values():
        if not isinstance(by_time, dict):
            continue
        for entries in by_time.values():
            if not isinstance(entries, list):
                continue
            for raw in entries:
                m = _parse_meeting(raw)
                if m is None:
                    continue
                if _is_canceled(m.title):
                    continue
                if title_prefix and not m.title.startswith(title_prefix):
                    continue
                # Defense against the API ignoring the window params:
                # filter locally on the meeting's local date.
                if not (start <= m.start[:10] <= end):
                    continue
                meetings.append(m)
    meetings.sort(key=lambda m: m.start)
    return meetings

"""Launch run_local.py as a background subprocess and report its progress.

This module owns the *mechanics* of launching + monitoring; the pure helpers
(derive_meeting_id, build_run_command) are unit-tested without spawning."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from gui.paths import is_safe_meeting_id


@dataclass
class RunParams:
    input: str
    date: str
    meeting_type: str
    event_kind: str
    city: Optional[str] = None
    title: Optional[str] = None
    compute: str = "local"
    diarizer: str = "oss"
    meeting_id: Optional[str] = None
    clip_start: Optional[str] = None
    clip_end: Optional[str] = None
    num_speakers: int = 0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def derive_meeting_id(p: RunParams) -> str:
    """Custom id if given, else '{date}-{slug(meeting_type)}'. Raises ValueError
    if the result isn't a safe single path component (matches run_local's rule)."""
    mid = (p.meeting_id or "").strip() or f"{p.date}-{_slug(p.meeting_type)}"
    mid = mid.strip("-")
    if not is_safe_meeting_id(mid) or mid in ("", "-"):
        raise ValueError(f"Cannot derive a valid meeting id from date={p.date!r} type={p.meeting_type!r}")
    return mid


def build_run_command(python_exe: str, script: str, p: RunParams, meeting_id: str) -> list[str]:
    """Compose the run_local.py argv. meeting_id is passed explicitly so the GUI
    knows the target dir. Optional flags are omitted when absent."""
    cmd = [
        python_exe, script,
        "--input", p.input,
        "--meeting-id", meeting_id,
        "--date", p.date,
        "--event-kind", p.event_kind,
        "--meeting-type", p.meeting_type,
        "--compute", p.compute,
        "--diarizer", p.diarizer,
    ]
    if p.city:
        cmd += ["--city", p.city]
    if p.title:
        cmd += ["--title", p.title]
    if p.num_speakers and p.num_speakers > 0:
        cmd += ["--num-speakers", str(p.num_speakers)]
    if p.clip_start and p.clip_end:
        cmd += ["--clip", p.clip_start, p.clip_end]
    return cmd

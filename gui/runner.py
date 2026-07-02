"""Launch run_local.py as a background subprocess and report its progress.

This module owns the *mechanics* of launching + monitoring; the pure helpers
(derive_meeting_id, build_run_command) are unit-tested without spawning."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src import config
from src.checkpoint import ensure_drive_structure

from gui.models import stage_label
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


# meeting_id -> Popen handle for the current process's launches. A local
# single-user tool runs one uvicorn worker, so a module dict is sufficient;
# handles are lost on restart (as are the children), which run_status tolerates.
_RUNS: dict = {}

_LOG_NAME = "gui_run.log"
_SIDE_NAME = "gui_run.json"


def launch_run(p: RunParams, *, python_exe: str, script: str, popen=subprocess.Popen) -> str:
    """Spawn run_local.py for these params in the background. Returns the meeting_id.
    stdout+stderr are captured to gui_run.log; stdin is /dev/null so the pipeline
    runs non-interactively (terminal review is skipped — review happens in the GUI)."""
    meeting_id = derive_meeting_id(p)
    meeting_dir = ensure_drive_structure(meeting_id)
    cmd = build_run_command(python_exe, script, p, meeting_id)

    log_f = open(meeting_dir / _LOG_NAME, "wb")
    proc = popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=None,  # inherit the GUI's working directory (the repo root)
    )
    _RUNS[meeting_id] = proc
    (meeting_dir / _SIDE_NAME).write_text(
        json.dumps({"pid": getattr(proc, "pid", None), "cmd": cmd, "status": "running"}),
        encoding="utf-8",
    )
    return meeting_id


def _log_tail(meeting_dir: Path, max_bytes: int = 16000) -> str:
    log = meeting_dir / _LOG_NAME
    if not log.exists():
        return ""
    data = log.read_bytes()
    return data[-max_bytes:].decode("utf-8", errors="replace")


def run_status(meeting_id: str) -> Optional[dict]:
    """Progress snapshot, or None if this meeting has no run sidecar/registry entry."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / _SIDE_NAME).exists() and meeting_id not in _RUNS:
        return None

    completed = 0
    ps = meeting_dir / "pipeline_state.json"
    if ps.exists():
        try:
            completed = int(json.loads(ps.read_text()).get("completed_stage", 0))
        except (ValueError, OSError, TypeError, AttributeError):
            completed = 0

    proc = _RUNS.get(meeting_id)
    if proc is not None:
        rc = proc.poll()
        running = rc is None
        exit_code = rc
    else:
        # No live handle (e.g. after a GUI restart): fall back to the sidecar.
        running = False
        exit_code = None

    return {
        "meeting_id": meeting_id,
        "completed_stage": completed,
        "stage_label": stage_label(completed),
        "running": running,
        "exit_code": exit_code,
        "log_tail": _log_tail(meeting_dir),
    }

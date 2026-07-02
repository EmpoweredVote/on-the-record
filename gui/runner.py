"""Launch run_local.py as a background subprocess and report its progress.

This module owns the *mechanics* of launching + monitoring; the pure helpers
(derive_meeting_id, build_run_command) are unit-tested without spawning."""
from __future__ import annotations

import json
import os
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

    # Open in a with-block so the PARENT closes its handle after Popen dups it
    # into the child (one leaked fd per launch otherwise). The child keeps its
    # own dup, so its writes to the log are unaffected.
    # PYTHONUNBUFFERED so run_local's print() output flushes line-by-line to the
    # log instead of sitting in a block buffer (stdout is a file, not a TTY, so
    # Python would otherwise buffer ~8KB — making stage 2+ output invisible until
    # the buffer fills or the process exits).
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(meeting_dir / _LOG_NAME, "wb") as log_f:
        proc = popen(
            cmd,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=None,  # inherit the GUI's working directory (the repo root)
            env=env,
        )
    _RUNS[meeting_id] = proc
    (meeting_dir / _SIDE_NAME).write_text(
        json.dumps({"pid": getattr(proc, "pid", None), "cmd": cmd, "status": "running"}),
        encoding="utf-8",
    )
    return meeting_id


def _collapse_progress(text: str) -> str:
    """Render carriage-return progress the way a terminal would: for each line,
    keep only the content after its final '\\r' overwrite. Turns yt-dlp's
    hundreds of '[download] N% ...\\r' frames into the single last value."""
    lines = []
    for raw in text.split("\n"):
        seg = raw.rstrip("\r")          # drop trailing CR (e.g. \r\n endings)
        if "\r" in seg:
            seg = seg.rsplit("\r", 1)[-1]  # keep only the final in-place update
        lines.append(seg)
    return "\n".join(lines)


def _log_tail(meeting_dir: Path, max_bytes: int = 16000) -> str:
    log = meeting_dir / _LOG_NAME
    if not log.exists():
        return ""
    data = log.read_bytes()
    return _collapse_progress(data[-max_bytes:].decode("utf-8", errors="replace"))


def run_status(meeting_id: str) -> Optional[dict]:
    """Progress snapshot for any meeting that has pipeline_state.json (GUI-launched
    or not), or None if the meeting is truly absent."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    has_state = (meeting_dir / "pipeline_state.json").exists()
    if not has_state and not (meeting_dir / _SIDE_NAME).exists() and meeting_id not in _RUNS:
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
        # No live handle (GUI restarted): recover liveness from the sidecar pid.
        running = False
        exit_code = None
        try:
            side = json.loads((meeting_dir / _SIDE_NAME).read_text())
            pid = side.get("pid")
        except (ValueError, OSError, TypeError, AttributeError):
            pid = None
        if pid:
            try:
                os.kill(int(pid), 0)
                running = True   # process still alive
            except ProcessLookupError:
                running = False  # dead
            except PermissionError:
                running = True   # exists but not ours -> alive
            except (ValueError, TypeError):
                running = False

    return {
        "meeting_id": meeting_id,
        "completed_stage": completed,
        "stage_label": stage_label(completed),
        "running": running,
        "exit_code": exit_code,
        "log_tail": _log_tail(meeting_dir),
    }


def _meeting_source_key(meeting_dir) -> Optional[str]:
    """A meeting's source key: the recorded state field, else derived from the
    saved audio_source (covers meetings processed before the field existed)."""
    from src.source_key import source_key
    state_file = meeting_dir / "pipeline_state.json"
    if state_file.exists():
        try:
            sk = json.loads(state_file.read_text()).get("source_key")
            if sk:
                return sk
        except (ValueError, OSError, AttributeError):
            pass
    named = meeting_dir / "transcript_named.json"
    if named.exists():
        try:
            audio_source = json.loads(named.read_text()).get("audio_source")
            if audio_source:
                return source_key(audio_source)
        except (ValueError, OSError, AttributeError):
            pass
    return None


def find_meeting_by_source(raw_input: str) -> Optional[str]:
    """meeting_id of an existing meeting sharing this input's source key, or None."""
    from src.source_key import source_key
    key = source_key(raw_input)
    if not key:
        return None
    if not config.MEETINGS_DIR.exists():
        return None
    for child in sorted(config.MEETINGS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if _meeting_source_key(child) == key:
            return child.name
    return None

"""Launch run_local.py as a background subprocess and report its progress.

This module owns the *mechanics* of launching + monitoring; the pure helpers
(derive_meeting_id, build_run_command) are unit-tested without spawning."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
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
    event_orgs: list = field(default_factory=list)
    body_slug: Optional[str] = None
    crec_chamber: Optional[str] = None   # 'house'|'senate' -> --congressional-record
    guest: Optional[str] = None          # interview subject; slugified into the id only
    race_id: Optional[str] = None        # essentials.races UUID -> --race-id
    race_slug: Optional[str] = None       # slug of the race's position_name, for the id


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


_MAX_ID_LEN = 80


def _locus_for(p: "RunParams") -> str:
    """The identifying token inserted between date and label, chosen by event kind.
    All inputs are slugged here except body_slug and race_slug (already slugs)."""
    kind = p.event_kind
    org = _slug(p.event_orgs[0]) if p.event_orgs else ""
    city = _slug(p.city or "")
    guest = _slug(p.guest or "")
    race = (p.race_slug or "").strip("-")
    if kind in ("council", "school_board"):
        return (p.body_slug or "").strip() or city
    if kind == "community_meeting":
        return city or org
    if kind in ("debate", "forum"):
        return race or org or city
    if kind in ("news_clip", "press_conference", "podcast"):
        parts = [t for t in (guest, race) if t]      # guest before race
        return "-".join(parts) or org
    if kind == "floor":
        return ""                                     # chamber lives in the label
    return city or org                                # "other"


def _overlaps(a: str, b: str) -> bool:
    """True if a and b share a whole hyphen-token containment (not a partial
    substring). Dash-wrapping makes 'ada' NOT match inside 'adaptation'."""
    return f"-{a}-" in f"-{b}-" or f"-{b}-" in f"-{a}-"


def derive_meeting_id(p: RunParams) -> str:
    """Custom id if given, else '{date}-{locus}-{label}' where locus is kind-aware
    (see _locus_for). New meetings only — existing slugs are never re-derived
    (ADR-0002). Raises ValueError if the result isn't a safe path component."""
    if (p.meeting_id or "").strip():
        mid = p.meeting_id.strip()
    else:
        label = _slug(p.meeting_type)
        locus = _locus_for(p)
        # Overlap de-dup: if the label already contains the locus (or vice versa),
        # drop the locus so we don't get 'bloomington-bloomington-...'.
        if locus and _overlaps(locus, label):
            locus = ""
        mid = "-".join(x for x in (p.date, locus, label) if x)
        if len(mid) > _MAX_ID_LEN:
            mid = mid[:_MAX_ID_LEN].rstrip("-")
    mid = mid.strip("-")
    if not is_safe_meeting_id(mid) or mid in ("", "-"):
        raise ValueError(
            f"Cannot derive a valid meeting id from date={p.date!r} type={p.meeting_type!r}"
        )
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
    for org in (p.event_orgs or []):
        if org:
            cmd += ["--event-org", org]
    if p.body_slug:
        cmd += ["--body", p.body_slug]
    if p.crec_chamber:
        # CREC package date == the floor session's meeting date
        cmd += ["--congressional-record", p.date, p.crec_chamber]
    if p.race_id:
        cmd += ["--race-id", p.race_id]
    if p.guest:
        cmd += ["--guest", p.guest]
    return cmd


# meeting_id -> Popen handle for the current process's launches. A local
# single-user tool runs one uvicorn worker, so a module dict is sufficient;
# handles are lost on restart (as are the children), which run_status tolerates.
_RUNS: dict = {}

_LOG_NAME = "gui_run.log"
_SIDE_NAME = "gui_run.json"


REDO_STAGES = ("diarize", "transcribe", "identify", "summary")


def _spawn(meeting_id: str, meeting_dir: Path, cmd: list[str], popen) -> str:
    """Spawn cmd as the background pipeline process for meeting_id: capture
    stdout+stderr to gui_run.log, run unbuffered + non-interactive, register the
    handle, write the sidecar. Shared by launch_run and launch_redo.

    Open in a with-block so the PARENT closes its handle after Popen dups it into
    the child (one leaked fd per launch otherwise). PYTHONUNBUFFERED so run_local's
    print() output flushes line-by-line to the log instead of sitting in a block
    buffer (stdout is a file, not a TTY, so Python would otherwise buffer ~8KB —
    making stage 2+ output invisible until the buffer fills or the process exits)."""
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


def _unique_meeting_id(base_id: str, source: str) -> str:
    """A meeting id that won't clobber a DIFFERENT source. Returns base_id if its
    dir is free or already belongs to this same source (a re-run); otherwise
    appends -2, -3, ... until a free or same-source slot is found. Guards against
    two different videos with the same {date}-{label} colliding."""
    candidate = base_id
    n = 1
    while True:
        mdir = config.MEETINGS_DIR / candidate
        if not mdir.exists():
            return candidate                    # free
        if source and _meeting_source_key(mdir) == source:
            return candidate                    # same video -> reuse (re-run)
        n += 1
        candidate = f"{base_id}-{n}"


def launch_run(p: RunParams, *, python_exe: str, script: str, popen=subprocess.Popen) -> str:
    """Spawn run_local.py for a NEW meeting in the background. Returns the meeting_id
    (bumped with a -N suffix if the derived id would collide with a different source).
    stdout+stderr are captured to gui_run.log; stdin is /dev/null so the pipeline
    runs non-interactively (terminal review is skipped — review happens in the GUI)."""
    from src.source_key import source_key
    base_id = derive_meeting_id(p)
    meeting_id = _unique_meeting_id(base_id, source_key(p.input))
    meeting_dir = ensure_drive_structure(meeting_id)
    cmd = build_run_command(python_exe, script, p, meeting_id)
    return _spawn(meeting_id, meeting_dir, cmd, popen)


def build_redo_command(python_exe: str, script: str, meeting_id: str, stage: str) -> list[str]:
    """`run_local.py --resume <id> --redo <stage>` — re-run a stage on an existing
    meeting (audio already on disk, so no --input needed)."""
    return [python_exe, script, "--resume", meeting_id, "--redo", stage]


def launch_redo(meeting_id: str, stage: str, *, python_exe: str, script: str,
                popen=subprocess.Popen) -> Optional[str]:
    """Re-run a stage for an existing meeting. Returns the meeting_id, or None on
    unsafe id / invalid stage / unknown meeting."""
    if not is_safe_meeting_id(meeting_id) or stage not in REDO_STAGES:
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    cmd = build_redo_command(python_exe, script, meeting_id, stage)
    return _spawn(meeting_id, meeting_dir, cmd, popen)


def build_resume_command(python_exe: str, script: str, meeting_id: str, *,
                         override_gate: bool = False) -> list[str]:
    """`run_local.py --resume <id> --no-publish` — pick up the pipeline from the
    last completed stage WITHOUT publishing (--resume auto-enables publish unless
    --no-publish; publishing stays the separate Publish action). override_gate adds
    --publish-anyway, which lifts the summary/enroll review gate only (with
    --no-publish in place it cannot publish)."""
    cmd = [python_exe, script, "--resume", meeting_id, "--no-publish"]
    if override_gate:
        cmd.append("--publish-anyway")
    return cmd


def launch_resume(meeting_id: str, *, override_gate: bool = False,
                  python_exe: str, script: str, popen=subprocess.Popen) -> Optional[str]:
    """Resume an existing meeting forward to completion. Returns meeting_id, or None
    on unsafe id / unknown meeting."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    cmd = build_resume_command(python_exe, script, meeting_id, override_gate=override_gate)
    return _spawn(meeting_id, meeting_dir, cmd, popen)


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


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness probe for a recovered sidecar pid.

    POSIX uses the signal-0 idiom. On Windows os.kill() maps to TerminateProcess
    for any non-CTRL signal, so signal 0 would *kill* a live process (or raise
    WinError 87 on a stale pid) — we must query the OS instead. Any error is
    treated as not-alive, since sidecar recovery is best-effort.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102  # handle not signaled -> process still running
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False  # no such process (or access denied) -> treat as dead
        try:
            return kernel32.WaitForSingleObject(wintypes.HANDLE(handle), 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # dead
    except PermissionError:
        return True   # exists but not ours -> alive
    except OSError:
        return False
    return True       # process still alive


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
                running = _pid_alive(int(pid))
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

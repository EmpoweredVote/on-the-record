"""Parallel batch processing: a concurrency-capped pool + a persisted pending
queue + a background scheduler. All new-meeting launches route through
launch_or_enqueue, so the cap governs a single add and a burst identically.
Local-only — reuses runner.launch_run, which never passes --publish."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from src import config
from gui import runner
from gui.runner import RunParams

_PYTHON_EXE = sys.executable
_RUN_LOCAL = str(Path(__file__).resolve().parent.parent / "run_local.py")

_STATE_NAME = "_batch.json"
_DEFAULT_MAX = 8
_MAX_CAP = 10
_lock = threading.RLock()

# RunParams fields serialized into a pending item (meeting_id/num_speakers are
# never queued — the id is minted at real launch time).
_PARAM_FIELDS = (
    "input", "date", "meeting_type", "event_kind", "city", "title",
    "compute", "diarizer", "clip_start", "clip_end", "event_orgs",
    "body_slug", "crec_chamber", "guest", "race_id", "race_slug",
)


def _state_path() -> Path:
    return config.MEETINGS_DIR / _STATE_NAME


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, OSError):
        data = {}
    data.setdefault("max_concurrent", _DEFAULT_MAX)
    data.setdefault("seq", 0)
    data.setdefault("pending", [])
    data.setdefault("active", [])
    return data


def _save(data: dict) -> None:
    tmp = _state_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _state_path())


def _params_to_dict(p: RunParams) -> dict:
    return {f: getattr(p, f) for f in _PARAM_FIELDS}


def _params_from_dict(d: dict) -> RunParams:
    kw = {f: d.get(f) for f in _PARAM_FIELDS}
    kw["event_orgs"] = kw.get("event_orgs") or []
    return RunParams(**kw)


def _prune_active(data: dict) -> None:
    """Drop meeting_ids whose run has finished/vanished from the active set."""
    alive = []
    for mid in data.get("active", []):
        st = runner.run_status(mid)
        if st is not None and st.get("running"):
            alive.append(mid)
    data["active"] = alive


def _running_count(data: dict) -> int:
    _prune_active(data)
    return len(data["active"])


def launch_or_enqueue(p: RunParams):
    """Launch p now if a pool slot is free, else enqueue it. Returns
    ("started", meeting_id) or ("pending", None)."""
    with _lock:
        data = _load()
        if _running_count(data) < data["max_concurrent"]:
            meeting_id = runner.launch_run(p, python_exe=_PYTHON_EXE, script=_RUN_LOCAL)
            data["active"].append(meeting_id)
            _save(data)
            return ("started", meeting_id)
        data["seq"] += 1
        data["pending"].append({"pending_id": data["seq"], "params": _params_to_dict(p)})
        _save(data)
        return ("pending", None)


def set_max_concurrent(n: int) -> None:
    with _lock:
        data = _load()
        data["max_concurrent"] = max(1, min(_MAX_CAP, int(n)))
        _save(data)


def status() -> dict:
    from gui.runner import derive_meeting_id
    with _lock:
        data = _load()
        _prune_active(data)
        running = []
        for mid in data["active"]:
            st = runner.run_status(mid)
            if st is None:
                continue
            running.append({"meeting_id": mid, "stage": st["completed_stage"],
                            "stage_label": st["stage_label"], "running": st["running"],
                            "exit_code": st.get("exit_code")})
        pending = []
        for item in data["pending"]:
            prm = item["params"]
            try:
                did = derive_meeting_id(_params_from_dict(prm))
            except Exception:
                did = ""
            label = (prm.get("title") or "").strip() or did or prm.get("input", "")
            pending.append({"pending_id": item["pending_id"], "label": label,
                            "event_kind": prm.get("event_kind"), "derived_id": did})
        _save(data)
        return {"counts": {"running": len(running), "pending": len(pending),
                           "max": data["max_concurrent"]},
                "running": running, "pending": pending}

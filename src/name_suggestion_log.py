"""Append-only log of what Chris did with each name suggestion (slice 3).

Feeds the auto-apply graduation rule in the parent spec. Never blocks an action.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .name_suggestion_view import SuggestionView, load_suggestions, to_view

LOG_NAME = "name_suggestion_log.jsonl"


def _line(view: SuggestionView, meeting_id: str, action: str, final_name: Optional[str]) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meeting_id": meeting_id, "label": view.label, "suggested_name": view.name, "tier": view.tier,
        "source": view.source, "verified": view.verified, "action": action, "final_name": final_name,
    }


def _append(meeting_dir: Path, line: dict) -> bool:
    try:
        with open(Path(meeting_dir) / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        print(f"  WARNING: name-suggestion log not written ({exc})")
        return False


def log_event(meeting_dir: Path, *, meeting_id: str, view: SuggestionView, action: str,
              final_name: Optional[str]) -> None:
    _append(meeting_dir, _line(view, meeting_id, action, final_name))


def _last_by_label(meeting_dir: Path) -> dict:
    last: dict = {}
    try:
        for raw in (Path(meeting_dir) / LOG_NAME).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("label"):
                last[d["label"]] = d
    except OSError:
        pass
    return last


def log_overrides(meeting_dir: Path, meeting_id: str, final_names: dict) -> int:
    from .name_lookup import norm_name

    recs, _ = load_suggestions(meeting_dir)
    last = _last_by_label(meeting_dir)
    written = 0
    for r in recs:
        if not r.get("prefill_name"):
            continue
        final = final_names.get(r["label"])
        if not final or norm_name(final) == norm_name(r["prefill_name"]):
            continue
        prev = last.get(r["label"])
        if (prev and prev.get("action") == "overridden"
                and norm_name(prev.get("final_name") or "") == norm_name(final)):
            continue
        v = to_view(r)
        if v and _append(meeting_dir, _line(v, meeting_id, "overridden", final)):
            written += 1
    return written

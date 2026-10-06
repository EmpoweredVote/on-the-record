"""Name suggestions as a pipeline step: skip rules + a run that never raises.

Spec: docs/superpowers/specs/2026-10-06-speaker-name-suggestions-review-design.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Optional

from .name_suggest import OUTPUT_NAME, Deps, suggest_names, unnamed_labels, write_suggestions

DB_WARNING = "database unavailable: politician and past-meeting lookups skipped"


def skip_reason(meeting_dir: Path, meeting: dict, *, disabled: bool = False,
                force: bool = False) -> Optional[str]:
    if disabled:
        return "disabled (--no-suggest-names)"
    if meeting.get("event_kind") == "floor":
        return "floor session"
    if not unnamed_labels(meeting):
        return "no unnamed speakers"
    out, named = Path(meeting_dir) / OUTPUT_NAME, Path(meeting_dir) / "transcript_named.json"
    if not force and out.exists() and named.exists() and out.stat().st_mtime >= named.stat().st_mtime:
        return "up to date"
    return None


def default_deps(meeting_dir: Path) -> tuple[Deps, list, list[str]]:
    from . import config
    from .name_lookup import PgNameDB, ResearchCache
    from .roster import load_roster

    warnings: list[str] = []
    body_slug = None
    state_path = Path(meeting_dir) / "pipeline_state.json"
    if state_path.exists():
        try:
            body_slug = json.loads(state_path.read_text(encoding="utf-8")).get("body_slug")
        except (OSError, ValueError, AttributeError):
            warnings.append("pipeline_state.json unreadable: roster lookups skipped")
    roster = load_roster(body_slug=body_slug) if body_slug else None
    db = None
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        try:
            db = PgNameDB(db_url)
        except Exception:
            warnings.append(DB_WARNING)
    else:
        warnings.append(DB_WARNING)
    deps = Deps(db=db, cache=ResearchCache(config.CONFIG_DIR / "name_lookup_cache.json"))
    return deps, (roster.members if roster else []), warnings


def run_name_suggestions(meeting_dir: Path, *, disabled: bool = False, force: bool = False,
                         make_deps: Callable = default_deps) -> Optional[dict]:
    """Write name_suggestions.json for unnamed speakers. Never raises."""
    deps = None
    try:
        meeting = json.loads((Path(meeting_dir) / "transcript_named.json").read_text(encoding="utf-8"))
        why = skip_reason(meeting_dir, meeting, disabled=disabled, force=force)
        if why:
            print(f"  Name suggestions skipped: {why}")
            return None
        deps, members, warnings = make_deps(meeting_dir)
        result = suggest_names(meeting, Path(meeting_dir), members=members, deps=deps,
                               warnings=warnings, only_labels=unnamed_labels(meeting))
        write_suggestions(Path(meeting_dir), result)
        n_ok = sum(1 for s in result["suggestions"] if s.get("prefill_name"))
        print(f"  Name suggestions: {len(result['suggestions'])} speaker(s), {n_ok} verified")
        for w in result["warnings"]:
            print(f"  WARNING: {w}")
        return result
    except Exception as exc:  # noqa: BLE001 - a failed lookup never fails the pipeline
        msg = f"name suggestions failed ({type(exc).__name__}: {exc})"
        print(f"  {msg[0].upper()}{msg[1:]} - continuing")
        try:  # leave a file whose warning drives the review banner + Re-run button
            if (Path(meeting_dir) / "transcript_named.json").exists():
                write_suggestions(Path(meeting_dir), {"generated_at": None, "model": None, "state": None,
                                                      "warnings": [msg], "suggestions": []})
        except Exception:
            pass
        return None
    finally:
        db = getattr(deps, "db", None)
        if db is not None and hasattr(db, "close"):
            try:
                db.close()
            except Exception:
                pass

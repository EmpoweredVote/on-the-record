# Speaker Name Suggestions — Slice 3 (review integration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate name suggestions automatically for unnamed speakers when a meeting is processed, show them on the GUI review cards (and minimally in the terminal review), accept them one at a time or all verified at once, and log every accept/edit/override.

**Architecture:** Two small pure modules carry the logic — `src/name_suggestion_view.py` (turn `name_suggestions.json` records into per-card views and accept actions) and `src/name_suggestion_log.py` (JSONL event log + publish-time overrides). `src/name_suggest_step.py` runs the slice-2 lookup as a never-failing pipeline step with skip rules; a file lock serializes web lookups across processes. The GUI and terminal review only call these modules plus the EXISTING identity actions (`apply_link`, `apply_make_local_person`, `src.review` helpers).

**Tech Stack:** Python 3 (repo `.venv`), FastAPI + Jinja2 (existing GUI), pytest, `fcntl` file lock. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-speaker-name-suggestions-review-design.md` (parent: `2026-10-02-speaker-name-suggestions-design.md`).

## Global Constraints

- When this plan's code and the spec differ, the spec governs (Chris, 2026-10-03).
- Only speakers **without an identity** are looked up and shown suggestions: no name, or status unidentified, and no politician link and no local person. A speaker already identified by a voice profile, a politician link or a local person gets **no lookup at all**.
- The pipeline step never fails the run. Skipped when `event_kind == "floor"`, when `--no-suggest-names` is passed, and on a resumed run when `name_suggestions.json` is newer than `transcript_named.json`.
- Web lookups are serialized across processes with an exclusive file lock on `CONFIG_DIR/name_lookup.lock`.
- A record is **acceptable** only if its `prefill_name` is set (verified, no conflict, not partial).
- Accept calls only existing identity actions: politician → `review_api.apply_link`; past-meeting person → `apply_make_local_person` with that `local_slug`; anything else → `apply_make_local_person` with `src.review.default_local_slug(name, label)` and the suggested role (`public_comment` when none).
- "Accept all verified" applies only acceptable suggestions on still-unnamed speakers and never overwrites an existing name.
- Log file: `<meeting_dir>/name_suggestion_log.jsonl`, one JSON object per line: `{ts, meeting_id, label, suggested_name, tier, source, verified, action, final_name}`, `action` ∈ `accepted | edited | bulk_accepted | overridden`. Logging never blocks an action.
- Missing or corrupt `name_suggestions.json` → review renders exactly as today.
- Nothing is auto-applied. No lookup tuning in this slice.
- Python: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`. Tests never call the real `claude` CLI, the network or the DB.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `src/name_suggest.py` (modify) | `unnamed_labels(meeting)`; `suggest_names(..., only_labels=...)` |
| `src/name_lookup.py` (modify) | `research_lock()`; `run_cli` holds it |
| `src/name_suggest_step.py` (create) | `skip_reason`, `default_deps`, `run_name_suggestions` (never raises) |
| `run_local.py` (modify) | pipeline step after Stage 4; `--no-suggest-names`; `_suggest_names` uses the step; terminal [Y] |
| `src/name_suggestion_view.py` (create) | `SuggestionView`, `AcceptAction`, `load_suggestions`, `to_view`, `is_unnamed_mapping`, `views_for_unnamed`, `accept_action`, `apply_action_to_mappings` |
| `src/name_suggestion_log.py` (create) | `log_event`, `log_overrides` |
| `src/publish.py` (modify) | call `log_overrides` after a successful publish |
| `gui/models.py`, `gui/review_api.py`, `gui/app.py`, `gui/runner.py`, `gui/templates/panels/_macros.html`, `gui/templates/panels/review.html` (modify) | card suggestion, banner, accept / accept-all / re-run routes, edit logging |
| `tests/test_name_suggest_step.py`, `tests/test_name_suggestion_view.py`, `tests/test_name_suggestion_log.py`, `tests/test_gui_name_suggestions.py` (create); `tests/test_name_lookup.py`, `tests/test_name_suggest.py` (modify) | tests |

---

### Task 1: Unnamed-only lookups and a cross-process lookup lock

**Files:**
- Modify: `src/name_suggest.py`, `src/name_lookup.py`
- Test: `tests/test_name_suggest.py`, `tests/test_name_lookup.py` (append)

**Interfaces:**
- Produces:
  - `unnamed_labels(meeting: dict) -> set[str]` in `src/name_suggest.py`
  - `suggest_names(meeting, meeting_dir, *, members, deps, warnings=None, only_labels: Optional[set[str]] = None) -> dict` — when `only_labels` is not None, candidates whose label is not in it are skipped entirely (no lookup, no record).
  - `research_lock(path: Optional[Path] = None)` context manager in `src/name_lookup.py`; `run_cli` runs the subprocess inside it.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_name_suggest.py
from src.name_suggest import unnamed_labels


def _meeting_with(speakers):
    segs = [{"segment_id": i, "start_time": i, "end_time": i + 1, "speaker_label": lab, "text": "x"}
            for i, lab in enumerate(["A", "B", "C", "D", "E", "F"])]
    return {"segments": segs, "speakers": speakers}


def test_unnamed_labels_only_speakers_without_identity():
    m = _meeting_with({
        "A": {"speaker_name": "Liz Brown", "id_method": "voice_profile"},
        "B": {"speaker_name": None},
        "C": {"speaker_name": "Unidentified Speaker 3", "speaker_status": "unidentified"},
        "D": {"speaker_name": "", "politician_id": "p1"},
        "E": {"speaker_name": "", "local_slug": "ann-lee"},
        "F": {"speaker_name": "Music", "speaker_status": "non_speaker"},
    })
    assert unnamed_labels(m) == {"B", "C"}


def test_suggest_names_only_labels_skips_lookup(tmp_path):
    meeting = {"meeting_id": "m1", "city": "Indianapolis", "event_kind": "council", "segments": [
        {"segment_id": 0, "start_time": 0, "end_time": 30, "speaker_label": "W",
         "text": "My name is Rachel Sample, I'm with Hoosier Families and thank the committee for its time today."},
        {"segment_id": 1, "start_time": 30, "end_time": 60, "speaker_label": "V",
         "text": "My name is Paul Webster, I'm with the Bar Association and thank the committee for its time today."},
    ]}
    calls = []

    def rs(name, title, affiliation, place, **kw):
        calls.append(name)
        return None

    out = suggest_names(meeting, tmp_path, members=[], deps=Deps(db=None, researcher=rs), only_labels={"W"})
    assert [s["label"] for s in out["suggestions"]] == ["W"]
    assert calls == ["Rachel Sample"]
```

```python
# append to tests/test_name_lookup.py
import threading
import time


def test_run_cli_serializes_with_research_lock(tmp_path, monkeypatch):
    from src import name_lookup

    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr(name_lookup, "claude_config_dir", lambda: None)
    spans = []

    def fake_run(cmd, **kw):
        start = time.monotonic()
        time.sleep(0.2)
        spans.append((start, time.monotonic()))

        class P:
            returncode, stdout, stderr = 0, "{}", ""
        return P()

    monkeypatch.setattr(name_lookup.subprocess, "run", fake_run)
    ts = [threading.Thread(target=name_lookup.run_cli, args=(["claude"], 5)) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    (a0, a1), (b0, b1) = sorted(spans)
    assert b0 >= a1 - 0.01  # second started only after the first finished
    assert (tmp_path / "name_lookup.lock").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/chrisandrews/Documents/GitHub/on-the-record-names3 && /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggest.py tests/test_name_lookup.py -q`
Expected: FAIL — `ImportError: cannot import name 'unnamed_labels'` (and the lock test failing on overlap).

- [ ] **Step 3: Implement**

In `src/name_suggest.py` add:

```python
def unnamed_labels(meeting: dict) -> set[str]:
    """Labels with no identity: no usable name (or status unidentified), no
    politician link, no local person, and not marked as a non-speaker."""
    labels = {s.get("speaker_label") for s in meeting.get("segments", []) if s.get("speaker_label")}
    speakers = meeting.get("speakers") or {}
    out: set[str] = set()
    for label in labels:
        m = speakers.get(label) or {}
        if m.get("speaker_status") == "non_speaker":
            continue
        if m.get("politician_id") or m.get("politician_slug") or m.get("local_slug"):
            continue
        name = (m.get("speaker_name") or "").strip()
        if name and m.get("speaker_status") != "unidentified" and not name.lower().startswith("unidentified"):
            continue
        out.add(label)
    return out
```

Add the `only_labels: Optional[set[str]] = None` keyword to `suggest_names` and, at the top of the candidate loop, `if only_labels is not None and label not in only_labels: continue`.

In `src/name_lookup.py` add (imports at the top of the module):

```python
from contextlib import contextmanager


@contextmanager
def research_lock(path: Optional[Path] = None):
    """Exclusive cross-process lock so only one claude lookup runs at a time
    (parallel GUI batch jobs queue here)."""
    import fcntl

    from . import config

    p = Path(path) if path else config.CONFIG_DIR / "name_lookup.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
```

and wrap the `subprocess.run(...)` call inside `run_cli` in `with research_lock():`.

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggest.py tests/test_name_lookup.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/name_suggest.py src/name_lookup.py tests/test_name_suggest.py tests/test_name_lookup.py
git commit -m "feat(names): look up unnamed speakers only; serialize lookups with a file lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Never-failing pipeline step, skip rules, CLI reuse

**Files:**
- Create: `src/name_suggest_step.py`
- Modify: `run_local.py` (Stage 4 end; argparse; `_suggest_names`)
- Test: `tests/test_name_suggest_step.py`

**Interfaces:**
- Consumes: `unnamed_labels`, `suggest_names(only_labels=...)`, `write_suggestions`, `Deps` (Task 1 / slice 2); `PgNameDB`, `ResearchCache` (slice 2); `src.roster.load_roster`.
- Produces:
  - `OUTPUT_NAME = "name_suggestions.json"` (re-export of `src.name_suggest.OUTPUT_NAME`)
  - `skip_reason(meeting_dir: Path, meeting: dict, *, disabled: bool = False, force: bool = False) -> Optional[str]`
  - `default_deps(meeting_dir: Path) -> tuple[Deps, list, list[str]]` → `(deps, roster_members, warnings)`
  - `run_name_suggestions(meeting_dir: Path, *, disabled: bool = False, force: bool = False, make_deps=default_deps) -> Optional[dict]` — never raises; returns the written result or None when skipped/failed; prints one line saying which.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_suggest_step.py
from __future__ import annotations

import json
import os
import time

from src.name_suggest import Deps
from src.name_suggest_step import run_name_suggestions, skip_reason

SEGS = [{"segment_id": 0, "start_time": 0, "end_time": 30, "speaker_label": "W",
         "text": "My name is Rachel Sample, I'm with Hoosier Families and thank the committee for its time today."}]


def _write(tmp_path, **extra):
    m = {"meeting_id": "m1", "city": "Indianapolis", "event_kind": "council", "segments": SEGS,
         "speakers": {}, **extra}
    (tmp_path / "transcript_named.json").write_text(json.dumps(m))
    return m


def _deps_factory(found=None):
    def make(meeting_dir):
        return Deps(db=None, researcher=lambda *a, **k: found), [], []
    return make


def test_skip_rules(tmp_path):
    m = _write(tmp_path)
    assert skip_reason(tmp_path, m, disabled=True) == "disabled (--no-suggest-names)"
    assert skip_reason(tmp_path, {**m, "event_kind": "floor"}) == "floor session"
    assert skip_reason(tmp_path, {**m, "speakers": {"W": {"speaker_name": "Rachel Sample"}}}) == "no unnamed speakers"
    assert skip_reason(tmp_path, m) is None
    (tmp_path / "name_suggestions.json").write_text("{}")
    future = time.time() + 5
    os.utime(tmp_path / "name_suggestions.json", (future, future))
    assert skip_reason(tmp_path, m) == "up to date"
    assert skip_reason(tmp_path, m, force=True) is None


def test_run_writes_file(tmp_path):
    _write(tmp_path)
    out = run_name_suggestions(tmp_path, make_deps=_deps_factory())
    assert out is not None and [s["label"] for s in out["suggestions"]] == ["W"]
    assert json.loads((tmp_path / "name_suggestions.json").read_text())["suggestions"][0]["label"] == "W"


def test_run_never_raises(tmp_path, capsys):
    _write(tmp_path)

    def boom(meeting_dir):
        raise RuntimeError("db exploded")

    assert run_name_suggestions(tmp_path, make_deps=boom) is None
    assert "Name suggestions failed" in capsys.readouterr().out
    written = json.loads((tmp_path / "name_suggestions.json").read_text())
    assert written["suggestions"] == [] and "db exploded" in written["warnings"][0]
    assert run_name_suggestions(tmp_path / "missing", make_deps=_deps_factory()) is None


def test_run_skipped_prints_reason(tmp_path, capsys):
    _write(tmp_path, event_kind="floor")
    assert run_name_suggestions(tmp_path, make_deps=_deps_factory()) is None
    assert "skipped: floor session" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggest_step.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_suggest_step'`

- [ ] **Step 3: Implement `src/name_suggest_step.py`**

```python
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
    roster = None
    if body_slug:
        try:
            roster = load_roster(body_slug=body_slug)
        except Exception:
            warnings.append("roster unreadable: roster lookups skipped")
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
    except Exception as exc:  # noqa: BLE001 — a failed lookup never fails the pipeline
        msg = f"name suggestions failed ({type(exc).__name__}: {exc})"
        print(f"  {msg[0].upper()}{msg[1:]} — continuing")
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
```

If `OUTPUT_NAME` is not already defined in `src/name_suggest.py`, check how `write_suggestions` names the file and use that constant (add `OUTPUT_NAME = "name_suggestions.json"` there if missing).

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggest_step.py -q`
Expected: 4 passed

- [ ] **Step 5: Wire into `run_local.py`**

1. Argparse (next to `--suggest-names`): 
```python
    parser.add_argument("--no-suggest-names", action="store_true",
                        help="Skip the automatic name-suggestion step after speaker identification")
```
2. In `run_pipeline`, immediately after the Stage 4 block ends (search for the line `state.mark_complete(PipelineStage.IDENTIFIED)` and the `print()` that follows the block, BEFORE the "Confidence gate (Phase A)" comment), insert:
```python
    # Stage 4.5: name suggestions for unnamed speakers (never fails the run).
    from src.name_suggest_step import run_name_suggestions
    run_name_suggestions(meeting_dir, disabled=getattr(args, "no_suggest_names", False))
```
3. Replace the body of `_suggest_names(meeting_id)` after its meeting-id validation and transcript-exists check with:
```python
    from src.name_suggest_step import run_name_suggestions
    result = run_name_suggestions(meeting_dir, force=True)
    if result is None:
        return
    for s in result["suggestions"]:
        lk = s["lookup"]
        mark = "✓" if lk["verified"] else "·"
        print(f"  {mark} {s['label']:<11} {s['tier'] or '-':<7} {lk['name']!s:<28} {lk['source']:<12} "
              f"{lk['url'] or lk['reason'] or ''}")
    print(f"Wrote {meeting_dir / 'name_suggestions.json'}")
```
Remove the now-unused imports in `_suggest_names`.

- [ ] **Step 6: Run the full suite and commit**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all pass (update any existing `_suggest_names` test only if its expectations relied on the removed code path — say which in the report).

```bash
git add src/name_suggest_step.py tests/test_name_suggest_step.py run_local.py src/name_suggest.py
git commit -m "feat(names): automatic name-suggestion step after speaker ID (never fails the run)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Suggestion views and accept actions (pure)

**Files:**
- Create: `src/name_suggestion_view.py`
- Test: `tests/test_name_suggestion_view.py`

**Interfaces:**
- Consumes: `src.review.clear_speaker_status`, `rename_speaker`, `link_speaker`, `assign_local_person`; `src.event_kinds.resolve_local_role`; `src.name_lookup.norm_name`.
- Produces:
  - `@dataclass SuggestionView(label, name, state, source, url, domain, quotes, role, reason, politician_id, local_slug, tier, verified)` — `state` ∈ `"acceptable" | "unverified" | "info"`.
  - `@dataclass AcceptAction(kind: str, name: str, politician_id: Optional[str] = None, slug: Optional[str] = None, role: Optional[str] = None)` — `kind` ∈ `"link" | "local"`.
  - `load_suggestions(meeting_dir: Path) -> tuple[list[dict], list[str]]` — `([], [])` on missing/corrupt.
  - `to_view(rec: dict) -> Optional[SuggestionView]`
  - `is_unnamed_mapping(m) -> bool` (accepts a dict or a `SpeakerMapping`)
  - `views_for_unnamed(meeting_dir: Path, mappings: dict, labels: Iterable[str]) -> tuple[dict[str, SuggestionView], list[str]]`
  - `accept_action(view: SuggestionView, default_slug: str) -> Optional[AcceptAction]`
  - `apply_action_to_mappings(mappings, segments, label: str, action: AcceptAction, event_kind: Optional[str]) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_suggestion_view.py
from __future__ import annotations

import json

from src.name_suggestion_view import (
    AcceptAction, accept_action, apply_action_to_mappings, is_unnamed_mapping, load_suggestions,
    to_view, views_for_unnamed,
)


def rec(label="W", *, prefill=None, verified=False, conflict=None, partial=False, source="web",
        name="Rachael Sample", url=None, reason=None, pid=None, local=None, role="presenter"):
    return {"label": label, "tier": "strong", "role": role, "titled": False, "partial": partial,
            "conflict": conflict, "spoken_name": "Rachel Sample", "prefill_name": prefill,
            "evidence": [{"kind": "E1", "quote": "My name is Rachel Sample"}, {"kind": "E4", "quote": "(source captions)"}],
            "lookup": {"name": name, "source": source, "verified": verified, "politician_id": pid,
                       "local_slug": local, "url": url, "affiliation": None, "reason": reason}}


def test_states():
    v = to_view(rec(prefill="Rachael Sample", verified=True, url="https://www.example.org/team"))
    assert (v.state, v.domain, v.quotes) == ("acceptable", "example.org", ["My name is Rachel Sample"])
    v = to_view(rec(reason="different name returned"))
    assert (v.state, v.reason) == ("unverified", "different name returned")
    v = to_view(rec(conflict="name_on_two_labels"))
    assert v.state == "info" and "another speaker" in v.reason
    v = to_view(rec(partial=True, name="Rachel"))
    assert v.state == "info" and v.reason == "first name only"


def test_load_suggestions_missing_and_corrupt(tmp_path):
    assert load_suggestions(tmp_path) == ([], [])
    (tmp_path / "name_suggestions.json").write_text("{not json")
    assert load_suggestions(tmp_path) == ([], [])
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": ["w"], "suggestions": [rec()]}))
    recs, warns = load_suggestions(tmp_path)
    assert len(recs) == 1 and warns == ["w"]


def test_is_unnamed_mapping():
    assert is_unnamed_mapping(None)
    assert is_unnamed_mapping({"speaker_name": ""})
    assert is_unnamed_mapping({"speaker_name": "Unidentified 2", "speaker_status": "unidentified"})
    assert not is_unnamed_mapping({"speaker_name": "Liz Brown"})
    assert not is_unnamed_mapping({"speaker_name": "", "politician_id": "p"})
    assert not is_unnamed_mapping({"speaker_name": "", "local_slug": "x"})


def test_views_for_unnamed_filters_named_and_missing_labels(tmp_path):
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        rec("W"), rec("N"), rec("GONE")]}))
    views, _ = views_for_unnamed(tmp_path, {"N": {"speaker_name": "Liz Brown"}}, ["W", "N"])
    assert set(views) == {"W"}


def test_accept_action_mapping():
    v = to_view(rec(prefill="Liz Brown", verified=True, source="roster", name="Liz Brown", pid="p-b"))
    assert accept_action(v, "liz-brown") == AcceptAction("link", "Liz Brown", politician_id="p-b")
    v = to_view(rec(prefill="Rachael Sample", verified=True, source="local_people", local="rachael-sample"))
    assert accept_action(v, "x") == AcceptAction("local", "Rachael Sample", slug="rachael-sample", role="presenter")
    v = to_view(rec(prefill="Rachael Sample", verified=True, role=None))
    assert accept_action(v, "rachael-sample") == AcceptAction("local", "Rachael Sample", slug="rachael-sample",
                                                              role="public_comment")
    assert accept_action(to_view(rec(reason="x")), "s") is None


def test_apply_action_to_mappings_local_and_link():
    from src.models import Segment, SpeakerMapping

    segs = [Segment(segment_id=0, start_time=0, end_time=1, speaker_label="W", text="hi")]
    maps = {"W": SpeakerMapping(speaker_label="W", speaker_name=None)}
    apply_action_to_mappings(maps, segs, "W", AcceptAction("local", "Rachael Sample", slug="rachael-sample",
                                                           role="presenter"), "council")
    assert maps["W"].speaker_name == "Rachael Sample" and maps["W"].local_slug == "rachael-sample"
    maps2 = {"W": SpeakerMapping(speaker_label="W", speaker_name=None)}
    apply_action_to_mappings(maps2, segs, "W", AcceptAction("link", "Liz Brown", politician_id="p-b"), "council")
    assert maps2["W"].speaker_name == "Liz Brown" and maps2["W"].politician_id == "p-b"
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggestion_view.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_suggestion_view'`

- [ ] **Step 3: Implement `src/name_suggestion_view.py`**

```python
"""Name suggestions as review views and accept actions (slice 3). Pure.

Spec: docs/superpowers/specs/2026-10-06-speaker-name-suggestions-review-design.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

SUGGESTIONS_NAME = "name_suggestions.json"
DEFAULT_ROLE = "public_comment"


@dataclass
class SuggestionView:
    label: str
    name: str
    state: str                     # acceptable | unverified | info
    source: str
    url: Optional[str]
    domain: Optional[str]
    quotes: list[str] = field(default_factory=list)
    role: Optional[str] = None
    reason: Optional[str] = None
    politician_id: Optional[str] = None
    local_slug: Optional[str] = None
    tier: Optional[str] = None
    verified: bool = False


@dataclass
class AcceptAction:
    kind: str                      # link | local
    name: str
    politician_id: Optional[str] = None
    slug: Optional[str] = None
    role: Optional[str] = None


def load_suggestions(meeting_dir: Path) -> tuple[list[dict], list[str]]:
    try:
        data = json.loads((Path(meeting_dir) / SUGGESTIONS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    recs = [r for r in data.get("suggestions") or [] if isinstance(r, dict) and r.get("label")]
    warns = [str(w) for w in data.get("warnings") or []]
    return recs, warns


def to_view(rec: dict) -> Optional[SuggestionView]:
    lk = rec.get("lookup") or {}
    name = (lk.get("name") or rec.get("spoken_name") or "").strip()
    if not name:
        return None
    if rec.get("prefill_name"):
        state, reason = "acceptable", None
    elif rec.get("conflict"):
        state = "info"
        reason = ("also suggested for another speaker" if rec["conflict"] == "name_on_two_labels"
                  else str(rec["conflict"]).replace("_", " "))
    elif rec.get("partial"):
        state, reason = "info", "first name only"
    else:
        state, reason = "unverified", lk.get("reason")
    url = lk.get("url")
    host = (urlparse(url).hostname or "") if url else ""
    return SuggestionView(
        label=rec["label"], name=rec.get("prefill_name") or name, state=state, source=lk.get("source") or "transcript",
        url=url, domain=(host[4:] if host.startswith("www.") else host) or None,
        quotes=[e.get("quote", "") for e in rec.get("evidence") or [] if e.get("kind") != "E4"][:2],
        role=rec.get("role"), reason=reason, politician_id=lk.get("politician_id"),
        local_slug=lk.get("local_slug"), tier=rec.get("tier"), verified=bool(lk.get("verified")),
    )


def _get(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def is_unnamed_mapping(m) -> bool:
    if m is None:
        return True
    if _get(m, "speaker_status") == "non_speaker":
        return False
    if _get(m, "politician_id") or _get(m, "politician_slug") or _get(m, "local_slug"):
        return False
    name = (_get(m, "speaker_name") or "").strip()
    return not name or _get(m, "speaker_status") == "unidentified" or name.lower().startswith("unidentified")


def views_for_unnamed(meeting_dir: Path, mappings: dict, labels: Iterable[str]
                      ) -> tuple[dict[str, SuggestionView], list[str]]:
    recs, warns = load_suggestions(meeting_dir)
    live = set(labels)
    out: dict[str, SuggestionView] = {}
    for r in recs:
        lab = r["label"]
        if lab in live and is_unnamed_mapping(mappings.get(lab)):
            v = to_view(r)
            if v:
                out[lab] = v
    return out, warns


def accept_action(view: SuggestionView, default_slug: str) -> Optional[AcceptAction]:
    if view.state != "acceptable":
        return None
    if view.politician_id:
        return AcceptAction("link", view.name, politician_id=view.politician_id)
    slug = view.local_slug or default_slug
    return AcceptAction("local", view.name, slug=slug, role=view.role or DEFAULT_ROLE)


def apply_action_to_mappings(mappings, segments, label: str, action: AcceptAction,
                             event_kind: Optional[str]) -> None:
    """In-memory equivalent of the GUI's apply_link / apply_make_local_person
    (clear status -> rename -> assign), for the terminal review."""
    from . import review
    from .event_kinds import resolve_local_role

    review.clear_speaker_status(mappings, segments, label)
    review.rename_speaker(mappings, segments, label, action.name, roster=None)
    if action.kind == "link":
        review.link_speaker(mappings, label, None, action.politician_id)
    else:
        review.assign_local_person(mappings, label, action.slug, resolve_local_role(action.role, event_kind))
```

- [ ] **Step 4: Run to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggestion_view.py -q`
Expected: 6 passed. If `SpeakerMapping` requires other constructor fields or `link_speaker`'s slug argument rejects None, read `src/models.py` / `src/review.py` and adapt the CODE (not the expected values).

- [ ] **Step 5: Commit**

```bash
git add src/name_suggestion_view.py tests/test_name_suggestion_view.py
git commit -m "feat(names): suggestion views and accept actions for review

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Event log and publish-time overrides

**Files:**
- Create: `src/name_suggestion_log.py`
- Modify: `src/publish.py` (`publish_meeting`, just before its `return PublishResult(`)
- Test: `tests/test_name_suggestion_log.py`

**Interfaces:**
- Consumes: `SuggestionView`, `load_suggestions` (Task 3); `src.name_lookup.norm_name`.
- Produces:
  - `LOG_NAME = "name_suggestion_log.jsonl"`
  - `log_event(meeting_dir: Path, *, meeting_id: str, view: SuggestionView, action: str, final_name: Optional[str]) -> None` (never raises)
  - `log_overrides(meeting_dir: Path, meeting_id: str, final_names: dict[str, Optional[str]]) -> int` — appends one `overridden` line per acceptable suggestion whose final name is set and differs (normalized), skipping a label whose LAST logged line is already `overridden` with the same final name; returns lines written. Speakers still unnamed at publish are ignored.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_suggestion_log.py
from __future__ import annotations

import json

from src.name_suggestion_log import LOG_NAME, log_event, log_overrides
from src.name_suggestion_view import to_view


def _rec(label, prefill):
    return {"label": label, "tier": "strong", "role": "presenter", "partial": False, "conflict": None,
            "spoken_name": prefill, "prefill_name": prefill, "evidence": [],
            "lookup": {"name": prefill, "source": "web", "verified": True, "url": "https://x.org/a"}}


def _lines(tmp_path):
    p = tmp_path / LOG_NAME
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_log_event_fields(tmp_path):
    v = to_view(_rec("W", "Rachael Sample"))
    log_event(tmp_path, meeting_id="m1", view=v, action="accepted", final_name="Rachael Sample")
    [l] = _lines(tmp_path)
    assert {k: l[k] for k in ("meeting_id", "label", "suggested_name", "tier", "source", "verified", "action",
                              "final_name")} == {"meeting_id": "m1", "label": "W", "suggested_name": "Rachael Sample",
                                                  "tier": "strong", "source": "web", "verified": True,
                                                  "action": "accepted", "final_name": "Rachael Sample"}
    assert "ts" in l


def test_log_event_never_raises(tmp_path):
    v = to_view(_rec("W", "A B"))
    log_event(tmp_path / "missing" / "deeper", meeting_id="m1", view=v, action="accepted", final_name="A B")


def test_log_overrides_dedup_and_ignores_unnamed(tmp_path):
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Rachael Sample"), _rec("V", "Paul Webster"), _rec("U", "Ann Lee")]}))
    finals = {"W": "Rachel Sample", "V": "paul  webster", "U": None}
    assert log_overrides(tmp_path, "m1", finals) == 1
    assert log_overrides(tmp_path, "m1", finals) == 0          # de-duplicated
    [l] = _lines(tmp_path)
    assert (l["label"], l["action"], l["final_name"]) == ("W", "overridden", "Rachel Sample")
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggestion_log.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# src/name_suggestion_log.py
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
    _append(meeting_dir, {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meeting_id": meeting_id, "label": view.label, "suggested_name": view.name, "tier": view.tier,
        "source": view.source, "verified": view.verified, "action": action, "final_name": final_name,
    })


def _last_by_label(meeting_dir: Path) -> dict[str, dict]:
    last: dict[str, dict] = {}
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
        if prev and prev.get("action") == "overridden" and norm_name(prev.get("final_name") or "") == norm_name(final):
            continue
        v = to_view(r)
        if v and _append(meeting_dir, {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "meeting_id": meeting_id,
            "label": v.label, "suggested_name": v.name, "tier": v.tier, "source": v.source,
            "verified": v.verified, "action": "overridden", "final_name": final,
        }):
            written += 1
    return written
```

In `src/publish.py`, inside `publish_meeting`, immediately before `return PublishResult(` add:

```python
    try:
        from . import config as _cfg
        from .name_suggestion_log import log_overrides

        log_overrides(_cfg.MEETINGS_DIR / meeting.meeting_id, meeting.meeting_id,
                      {lab: m.speaker_name for lab, m in meeting.speakers.items()})
    except Exception as exc:  # noqa: BLE001 — logging never blocks a publish
        print(f"  (name-suggestion override log skipped: {exc})")
```

- [ ] **Step 4: Run tests (log + publish)**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggestion_log.py tests/test_publish.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/name_suggestion_log.py tests/test_name_suggestion_log.py src/publish.py
git commit -m "feat(names): log accepts/edits and publish-time overrides of suggestions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: GUI — card suggestion, banner, accept / accept-all / re-run, edit logging

**Files:**
- Modify: `gui/models.py` (`SpeakerCard.suggestion`, `ReviewPageData.suggestion_warnings`, `ReviewPageData.bulk_accept_labels`), `gui/review_api.py` (`load_review_page`; new `apply_suggestion`, `apply_all_suggestions`, `log_suggestion_edit`), `gui/runner.py` (`launch_suggest_names`), `gui/app.py` (3 routes + `from_suggestion` on link/local-person routes), `gui/templates/panels/_macros.html`, `gui/templates/panels/review.html`
- Test: `tests/test_gui_name_suggestions.py`

**Interfaces:**
- Consumes: Task 3 (`views_for_unnamed`, `accept_action`, `SuggestionView`), Task 4 (`log_event`), existing `review_api.apply_link(meeting_id, label, politician_slug, politician_id, name=...)`, `review_api.apply_make_local_person(meeting_id, label, slug, role_raw, name=...)`, `src.review.default_local_slug(name, label)`, `runner._spawn(meeting_id, meeting_dir, cmd, popen)`.
- Produces:
  - `SpeakerCard.suggestion: Optional[SuggestionView] = None`
  - `ReviewPageData.suggestion_warnings: list[str]`; property `bulk_accept_labels -> list[str]` (labels whose card has an `acceptable` suggestion)
  - `review_api.apply_suggestion(meeting_id: str, label: str, *, bulk: bool = False) -> Optional[str]` (final name or None)
  - `review_api.apply_all_suggestions(meeting_id: str) -> list[tuple[str, str]]`
  - `review_api.log_suggestion_edit(meeting_id: str, label: str, final_name: str) -> None`
  - `runner.launch_suggest_names(meeting_id: str, *, python_exe: str, script: str, popen=subprocess.Popen) -> Optional[str]`
  - Routes: `POST /meetings/{meeting_id}/speakers/{label}/accept-suggestion`, `POST /meetings/{meeting_id}/accept-suggestions`, `POST /meetings/{meeting_id}/suggest-names`; existing link and local-person routes accept optional `from_suggestion: str = Form("")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_name_suggestions.py
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


def _rec(label, prefill=None, **lk):
    base = {"name": prefill or "Rachel Sample", "source": "web", "verified": bool(prefill),
            "politician_id": None, "local_slug": None, "url": "https://x.org/a" if prefill else None,
            "affiliation": None, "reason": None if prefill else "different name returned"}
    base.update(lk)
    return {"label": label, "tier": "strong", "role": "presenter", "titled": False, "partial": False,
            "conflict": None, "spoken_name": "Rachel Sample", "prefill_name": prefill,
            "evidence": [{"kind": "E1", "quote": "My name is Rachel Sample"}], "lookup": base}


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path / "cfg")
    d = tmp_path / "m1"
    d.mkdir()
    segs = [{"segment_id": i, "start_time": i * 10, "end_time": i * 10 + 9, "speaker_label": lab, "text": "hello there"}
            for i, lab in enumerate(["W", "V", "N"])]
    (d / "transcript_named.json").write_text(json.dumps({
        "meeting_id": "m1", "city": "Indianapolis", "date": "2026-01-14", "meeting_type": "Hearing",
        "event_kind": "council", "segments": segs,
        "speakers": {"N": {"speaker_label": "N", "speaker_name": "Liz Brown", "id_method": "human_review",
                           "confidence": 1.0}}}))
    (d / "name_suggestions.json").write_text(json.dumps({"warnings": ["Claude CLI cannot run lookups — run `claude-ev`, then /login"],
        "suggestions": [_rec("W", "Rachael Sample"), _rec("V"), _rec("N", "Someone Else")]}))
    return d


def test_review_page_shows_suggestions_banner_and_bulk(meeting):
    from gui import review_api

    page = review_api.load_review_page("m1")
    cards = {c.label: c for c in page.all_cards}
    assert cards["W"].suggestion.state == "acceptable" and cards["V"].suggestion.state == "unverified"
    assert cards["N"].suggestion is None                       # already named: no suggestion
    assert page.bulk_accept_labels == ["W"]
    assert "claude-ev" in page.suggestion_warnings[0]


def test_apply_suggestion_local_and_logs(meeting, monkeypatch):
    from gui import review_api

    calls = []
    monkeypatch.setattr(review_api, "apply_make_local_person",
                        lambda mid, lab, slug, role, name="": calls.append((mid, lab, slug, role, name)) or True)
    assert review_api.apply_suggestion("m1", "W") == "Rachael Sample"
    assert calls == [("m1", "W", "rachael-sample", "presenter", "Rachael Sample")]
    log = [json.loads(l) for l in (meeting / "name_suggestion_log.jsonl").read_text().splitlines()]
    assert log[-1]["action"] == "accepted"
    assert review_api.apply_suggestion("m1", "V") is None      # not acceptable


def test_apply_suggestion_link(meeting, monkeypatch):
    from gui import review_api

    (meeting / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Liz Brown", source="roster", politician_id="p-b")]}))
    calls = []
    monkeypatch.setattr(review_api, "apply_link",
                        lambda mid, lab, slug, pid, name="": calls.append((lab, pid, name)) or True)
    assert review_api.apply_suggestion("m1", "W") == "Liz Brown"
    assert calls == [("W", "p-b", "Liz Brown")]


def test_apply_all_only_acceptable_unnamed(meeting, monkeypatch):
    from gui import review_api

    done = []
    monkeypatch.setattr(review_api, "apply_make_local_person",
                        lambda mid, lab, slug, role, name="": done.append(lab) or True)
    assert review_api.apply_all_suggestions("m1") == [("W", "Rachael Sample")]
    assert done == ["W"]
    log = [json.loads(l) for l in (meeting / "name_suggestion_log.jsonl").read_text().splitlines()]
    assert log[-1]["action"] == "bulk_accepted"


def test_log_suggestion_edit(meeting):
    from gui import review_api

    review_api.log_suggestion_edit("m1", "W", "Rachel Sample")
    review_api.log_suggestion_edit("m1", "W", "Rachael Sample")
    actions = [json.loads(l)["action"] for l in (meeting / "name_suggestion_log.jsonl").read_text().splitlines()]
    assert actions == ["edited", "accepted"]


def test_routes(meeting, monkeypatch):
    from gui import review_api, runner
    from gui.app import create_app

    monkeypatch.setattr(review_api, "apply_suggestion", lambda mid, lab, bulk=False: "Rachael Sample")
    monkeypatch.setattr(review_api, "apply_all_suggestions", lambda mid: [("W", "Rachael Sample")])
    launched = []
    monkeypatch.setattr(runner, "launch_suggest_names", lambda mid, **kw: launched.append(mid) or mid)
    c = TestClient(create_app())
    assert c.post("/meetings/m1/speakers/W/accept-suggestion", follow_redirects=False).status_code == 303
    assert c.post("/meetings/m1/accept-suggestions", follow_redirects=False).status_code == 303
    assert c.post("/meetings/m1/suggest-names", follow_redirects=False).status_code == 303
    assert launched == ["m1"]
    monkeypatch.setattr(review_api, "apply_suggestion", lambda mid, lab, bulk=False: None)
    assert c.post("/meetings/m1/speakers/V/accept-suggestion", follow_redirects=False).status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_name_suggestions.py -q`
Expected: FAIL (missing attributes/functions/routes).

- [ ] **Step 3: Implement**

`gui/models.py`:
```python
# in SpeakerCard (after merge_mismatches)
    suggestion: Optional["SuggestionView"] = None
# in ReviewPageData (after local_role_options)
    suggestion_warnings: list[str] = field(default_factory=list)

    @property
    def bulk_accept_labels(self) -> list[str]:
        return [c.label for c in self.all_cards if c.suggestion is not None and c.suggestion.state == "acceptable"]
```
(Import `SuggestionView` under `TYPE_CHECKING` from `src.name_suggestion_view`.)

`gui/review_api.py`:
- In `load_review_page`, after the cards are built and before returning the page, attach suggestions:
```python
    from src.name_suggestion_view import views_for_unnamed
    sug_views, sug_warnings = views_for_unnamed(meeting_dir, meeting.speakers, [c.label for c in cards_all])
    for c in cards_all:
        c.suggestion = sug_views.get(c.label)
```
where `cards_all` is the list of all built cards (use the variables the function already has), and pass `suggestion_warnings=sug_warnings` into `ReviewPageData(...)`.
- Add:
```python
def _suggestion_for(meeting_id: str, label: str):
    from src.name_suggestion_view import views_for_unnamed

    if not is_safe_meeting_id(meeting_id):
        return None, None
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return None, None
    meeting, meeting_dir, _ = ctx
    labels = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    views, _ = views_for_unnamed(meeting_dir, meeting.speakers, labels)
    return views.get(label), meeting_dir


def apply_suggestion(meeting_id: str, label: str, *, bulk: bool = False) -> Optional[str]:
    from src import review
    from src.name_suggestion_log import log_event
    from src.name_suggestion_view import accept_action

    view, meeting_dir = _suggestion_for(meeting_id, label)
    if view is None:
        return None
    action = accept_action(view, review.default_local_slug(view.name, label))
    if action is None:
        return None
    if action.kind == "link":
        ok = apply_link(meeting_id, label, "", action.politician_id, name=action.name)
    else:
        try:
            ok = apply_make_local_person(meeting_id, label, action.slug, action.role, name=action.name)
        except ValueError:
            return None
    if not ok:
        return None
    log_event(meeting_dir, meeting_id=meeting_id, view=view,
              action="bulk_accepted" if bulk else "accepted", final_name=action.name)
    return action.name


def apply_all_suggestions(meeting_id: str) -> list[tuple[str, str]]:
    page = load_review_page(meeting_id)
    if page is None:
        return []
    done = []
    for label in page.bulk_accept_labels:
        name = apply_suggestion(meeting_id, label, bulk=True)
        if name:
            done.append((label, name))
    return done


def log_suggestion_edit(meeting_id: str, label: str, final_name: str) -> None:
    from src.name_lookup import norm_name
    from src.name_suggestion_log import log_event
    from src.name_suggestion_view import load_suggestions, to_view

    if not is_safe_meeting_id(meeting_id):
        return
    meeting_dir = config.MEETINGS_DIR / meeting_id
    recs, _ = load_suggestions(meeting_dir)
    rec = next((r for r in recs if r.get("label") == label), None)
    view = to_view(rec) if rec else None
    if view is None:
        return
    same = norm_name(final_name or "") == norm_name(view.name)
    log_event(meeting_dir, meeting_id=meeting_id, view=view,
              action="accepted" if same else "edited", final_name=final_name)
```
Note: `_suggestion_for` checks "still unnamed" against the CURRENT transcript, so a label named meanwhile returns None (route answers 404).

`gui/runner.py`:
```python
def launch_suggest_names(meeting_id: str, *, python_exe: str, script: str,
                         popen=subprocess.Popen) -> Optional[str]:
    """Re-run the name lookup for one meeting in the background."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "transcript_named.json").exists():
        return None
    return _spawn(meeting_id, meeting_dir, [python_exe, script, "--suggest-names", meeting_id], popen)
```

`gui/app.py` — add next to the other speaker routes (follow how the existing redo route passes `python_exe` / `script` to `runner.launch_redo`):
```python
    @app.post("/meetings/{meeting_id}/speakers/{label}/accept-suggestion")
    def accept_suggestion_route(meeting_id: str, label: str):
        if review_api.apply_suggestion(meeting_id, label) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/accept-suggestions")
    def accept_all_suggestions_route(meeting_id: str):
        done = review_api.apply_all_suggestions(meeting_id)
        msg = f"Accepted {len(done)}: " + ", ".join(f"{lab} {name}" for lab, name in done) if done else "Accepted 0"
        return RedirectResponse(url=f"/meetings/{meeting_id}/review?" + urlencode({"notice": msg}), status_code=303)

    @app.post("/meetings/{meeting_id}/suggest-names")
    def suggest_names_route(meeting_id: str):
        if runner.launch_suggest_names(meeting_id, python_exe=sys.executable, script=_RUN_SCRIPT) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
```
(Use the same names the existing redo route uses for the python executable and the run_local.py path; `_RUN_SCRIPT` above stands for that existing value. Import `urlencode` from `urllib.parse`.)
The review GET route reads an optional `notice: str = ""` query parameter and passes it to the template; `review.html` shows it in a `<p class="notice">` above the sections when set (Jinja autoescapes it). Add to `test_routes`: `r = c.post("/meetings/m1/accept-suggestions", follow_redirects=False); assert "Accepted+1" in r.headers["location"]`.
On the existing `link` and `local-person` routes add `from_suggestion: str = Form("")`; after a successful apply, if `from_suggestion.strip() == label`, call `review_api.log_suggestion_edit(meeting_id, label, name)`.

`gui/templates/panels/_macros.html` — inside the card's evidence block (make the block also render when `c.suggestion` is set), before the voice hints:
```html
{% if c.suggestion %}{% set s = c.suggestion %}
<div class="suggestion suggestion-{{ s.state }}">
  {% if s.state == 'acceptable' %}<strong>Suggested: {{ s.name }} ✓</strong>
  {% elif s.state == 'unverified' %}<strong>Suggested (not verified): {{ s.name }}</strong> <span class="why">{{ s.reason or '' }}</span>
  {% else %}<strong>Possible: {{ s.name }}</strong> <span class="why">{{ s.reason or '' }}</span>{% endif %}
  <span class="src">{% if s.url %}<a href="{{ s.url }}" target="_blank" rel="noopener noreferrer">{{ s.source }} · {{ s.domain }}</a>{% else %}{{ s.source }}{% endif %}{% if s.role %} · {{ s.role }}{% endif %}</span>
  {% for q in s.quotes %}<p class="sample">“{{ q[:200] }}”</p>{% endfor %}
  {% if s.state == 'acceptable' %}
  <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/accept-suggestion" class="inline">
    <button type="submit">Accept</button>
  </form>
  {% endif %}
</div>
{% endif %}
```
In the existing local-person form, when `c.suggestion` and `c.identity_kind in ('none', 'unidentified')`, pre-fill `name` with `c.suggestion.name` and `role` with `c.suggestion.role or ''`, and add `<input type="hidden" name="from_suggestion" value="{{ c.label }}">` (also add the hidden field to the link form when `c.suggestion` is set). "Edit" therefore = the existing identity forms, pre-filled.

`gui/templates/panels/review.html` — before the "Needs attention" section:
```html
{% if page.suggestion_warnings %}
<section class="review-warnings suggestion-warnings">
  <h2>Name lookup</h2>
  <ul>{% for w in page.suggestion_warnings %}<li>{{ w }}</li>{% endfor %}</ul>
  <form method="post" action="/meetings/{{ page.meeting_id }}/suggest-names"><button type="submit">Re-run name lookup</button></form>
</section>
{% endif %}
{% if page.bulk_accept_labels %}
<form method="post" action="/meetings/{{ page.meeting_id }}/accept-suggestions" class="bulk-accept">
  <button type="submit">Accept all verified ({{ page.bulk_accept_labels|length }})</button>
</form>
{% endif %}
```
Add minimal CSS in the GUI stylesheet for `.suggestion`, `.suggestion-acceptable` (green left border), `.suggestion-unverified` (amber), `.suggestion-info` (grey), using existing design tokens.

- [ ] **Step 4: Run tests**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_name_suggestions.py tests/test_gui_review.py -q`
Expected: all pass. If `load_review_page` needs a profile DB or embeddings that the fixture lacks, monkeypatch `src.enroll.load_profiles` in the test fixture to return an empty profile DB (read how other GUI tests do it) — never weaken an assertion.

- [ ] **Step 5: Full suite, commit**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q -p no:cacheprovider`

```bash
git add gui/ tests/test_gui_name_suggestions.py
git commit -m "feat(gui): name suggestions on review cards; accept, accept-all, re-run; edit logging

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Terminal review — show suggestion, [Y] accepts it first

**Files:**
- Modify: `run_local.py` (`_interactive_speaker_review`, near the per-speaker header print and the `elif choice.lower() in ("y", "yes") and top_hint:` branch; the help text lines `"[Y]      Accept suggested voice match (if shown)"`)
- Test: `tests/test_name_suggestion_view.py` (append one test of a small pure helper)

**Interfaces:**
- Consumes: `views_for_unnamed`, `accept_action`, `apply_action_to_mappings` (Task 3), `log_event` (Task 4), `src.review.default_local_slug`.
- Produces: `terminal_suggestion_line(view: SuggestionView) -> str` in `src/name_suggestion_view.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_name_suggestion_view.py
from src.name_suggestion_view import terminal_suggestion_line


def test_terminal_suggestion_line():
    v = to_view(rec(prefill="Rachael Sample", verified=True, url="https://www.example.org/x"))
    assert terminal_suggestion_line(v) == "Suggested: Rachael Sample (verified, web · example.org) — [Y] to accept"
    v = to_view(rec(reason="different name returned"))
    assert terminal_suggestion_line(v) == "Suggested (not verified): Rachael Sample — different name returned"
```

- [ ] **Step 2: Run to verify it fails**, then implement in `src/name_suggestion_view.py`:

```python
def terminal_suggestion_line(view: SuggestionView) -> str:
    src = f"{view.source} · {view.domain}" if view.domain else view.source
    if view.state == "acceptable":
        return f"Suggested: {view.name} (verified, {src}) — [Y] to accept"
    if view.state == "unverified":
        return f"Suggested (not verified): {view.name} — {view.reason or 'not verified'}"
    return f"Possible: {view.name} — {view.reason or ''}".rstrip(" —")
```

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_name_suggestion_view.py -q` → all pass.

- [ ] **Step 3: Wire into `_interactive_speaker_review`**

1. At the start of the function (after its arguments are available), when `meeting_id` is set:
```python
    sug_views = {}
    if meeting_id:
        from src import config as _cfg
        from src.name_suggestion_view import views_for_unnamed
        sug_views, _ = views_for_unnamed(_cfg.MEETINGS_DIR / meeting_id, mappings,
                                         {s.speaker_label for s in segments})
```
2. Where each speaker's header and sample are printed (before the command prompt), add:
```python
            sv = sug_views.get(label)
            if sv is not None:
                from src.name_suggestion_view import terminal_suggestion_line
                print(f"  {terminal_suggestion_line(sv)}")
```
3. Insert a new branch BEFORE `elif choice.lower() in ("y", "yes") and top_hint:`:
```python
                elif choice.lower() in ("y", "yes") and sug_views.get(label) is not None \
                        and sug_views[label].state == "acceptable":
                    from src import config as _cfg
                    from src.name_suggestion_log import log_event
                    from src.name_suggestion_view import accept_action, apply_action_to_mappings
                    sv = sug_views.pop(label)
                    action = accept_action(sv, review.default_local_slug(sv.name, label))
                    _push_undo()
                    old_name = mappings.get(label).speaker_name if mappings.get(label) else None
                    apply_action_to_mappings(mappings, segments, label, action, event_kind)
                    changes.append({"label": label, "old_name": old_name, "new_name": action.name})
                    log_event(_cfg.MEETINGS_DIR / meeting_id, meeting_id=meeting_id, view=sv,
                              action="accepted", final_name=action.name)
                    print(f"  Accepted suggestion: {action.name}")
                    views = review.build_review_state(segments, mappings, embeddings, profile_db, show_text=show_text)
                    break
```
Match the surrounding code's exact control-flow conventions (`advance`, `break`/`continue`) by reading the adjacent voice-match `[Y]` branch, and make the new branch end the same way that branch does.
4. Update both help lines to: `"  [Y]      Accept the name suggestion (or the voice match) if shown"`.

- [ ] **Step 4: Full suite and commit**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q -p no:cacheprovider`

```bash
git add run_local.py src/name_suggestion_view.py tests/test_name_suggestion_view.py
git commit -m "feat(names): terminal review shows suggestions; [Y] accepts them first

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Real check in the GUI and PR

**Files:** none (verification), unless a defect is found.

- [ ] **Step 1:** In the worktree, regenerate suggestions for the IGA Senate Judiciary 2026-01-14 meeting is NOT needed (it is fully named). Pick a meeting with unnamed speakers: run `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import json,glob;from src.name_suggest import unnamed_labels;[print(p.split('/')[-2], len(unnamed_labels(json.load(open(p))))) for p in glob.glob('/Users/chrisandrews/CouncilScribe/meetings/*/transcript_named.json')]" | sort -k2 -n | tail -5` and choose one with several unnamed speakers. Run `run_local.py --suggest-names <id>` (real lookup under the claude-ev login is allowed; it writes only into that meeting dir).
- [ ] **Step 2:** The controller previews the GUI from this worktree in the browser pane and checks: suggestion blocks render in the three states, the banner (if any), "Accept all verified (N)". It does NOT click Accept on real data unless Chris says so.
- [ ] **Step 3:** Full suite; push; open the PR with screenshots/summary. Merge only after Chris says so and CI is green.

---

## Self-review notes (plan author)

- Spec coverage: generation step + skip rules + lock + unnamed-only + label drift + re-run → Tasks 1, 2, 5 (route); card states, accept mapping, bulk, banner → Tasks 3, 5; logging (accepted, edited, bulk_accepted, overridden + de-dup) → Tasks 4, 5; terminal → Task 6; error handling (missing/corrupt file, changed label → 404, logging never blocks) → Tasks 3, 4, 5; real check → Task 7.
- `politician_slug` is passed as "" on accept-link (the existing route accepts either field); the link is by `politician_id`.
- Names used across tasks: `unnamed_labels`, `run_name_suggestions`, `SuggestionView`, `AcceptAction`, `views_for_unnamed`, `accept_action`, `apply_action_to_mappings`, `log_event`, `log_overrides`, `apply_suggestion`, `apply_all_suggestions`, `log_suggestion_edit`, `launch_suggest_names`, `terminal_suggestion_line` — consistent.

# Stop Silently Stamping Meetings as Bloomington/council — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An unknown meeting classification is never silently invented — the operator supplies `city`/`meeting_type`/`event_kind`/`date`, explicitly opts into civic defaults via `--default`, or the run fails loudly.

**Architecture:** Three layers of defense. (1) Ingest enforcement in `run_local._resolve_metadata` captures operator intent and hard-fails under-specified non-interactive runs. (2) `src/models.Meeting` stops fabricating defaults in the dataclass/`from_dict`. (3) `src/publish._upsert_meeting` rejects a missing/invalid kind or a city-less civic meeting before any DB write. Batch is routed through the same enforcement (which also fixes a latent missing-`event_kind` `AttributeError`).

**Tech Stack:** Python 3, pytest (`.venv/bin/python -m pytest`), psycopg2. Tests use `monkeypatch` and a `RecordingCursor` fake — no live DB.

**Spec:** [docs/superpowers/specs/2026-06-29-meeting-metadata-no-silent-defaults-design.md](../specs/2026-06-29-meeting-metadata-no-silent-defaults-design.md)

---

## File Structure

- `src/models.py` — `Meeting` dataclass defaults + `from_dict` (Layer 2).
- `src/publish.py` — `_upsert_meeting` backstop guard (Layer 3).
- `run_local.py` — `_resolve_metadata`, single-run error handling, batch parsing/dispatch, `--default` help (Layers 1 & 4).
- `tests/test_models.py` — honest-default tests.
- `tests/test_publish.py` — publish-guard tests.
- `tests/test_metadata_prompt.py` — rewritten `_resolve_metadata` tests.
- `tests/test_batch_metadata.py` — **new** — batch parsing + non-interactive enforcement.

**Out of scope (do not change):** the `--resume` defaulting branch (`run_local.py:3929-3939`) — resume restores metadata from saved state and the Layer 3 backstop still guards it; AI-derived classification (option b); historical backfill.

---

## Task 1: Layer 2 — `Meeting` stops fabricating defaults

**Files:**
- Modify: `src/models.py:240,242` (dataclass defaults) and `src/models.py:289,291` (`from_dict`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
def test_meeting_no_silent_classification_defaults():
    # Constructing without metadata must NOT invent council / Regular Session.
    m = Meeting(meeting_id="m1", city=None, date="2026-06-28")
    assert m.event_kind is None
    assert m.meeting_type is None


def test_meeting_from_dict_absent_fields_stay_none():
    back = Meeting.from_dict({"meeting_id": "m1", "city": None, "date": "2026-06-28"})
    assert back.event_kind is None
    assert back.meeting_type is None


def test_meeting_from_dict_preserves_real_values():
    d = {
        "meeting_id": "m1", "city": "Bloomington", "date": "2026-06-28",
        "meeting_type": "Regular Session", "event_kind": "council",
    }
    back = Meeting.from_dict(d)
    assert back.meeting_type == "Regular Session"
    assert back.event_kind == "council"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_models.py -k "silent_classification or absent_fields or preserves_real" -v`
Expected: the first two FAIL (currently `event_kind == "council"`, `meeting_type == "Regular Session"`); the third PASSES.

- [ ] **Step 3: Change the dataclass defaults**

In `src/models.py`, in the `Meeting` dataclass:

```python
    meeting_type: Optional[str] = None
    title: Optional[str] = None
    event_kind: Optional[str] = None
```

(Only `meeting_type` and `event_kind` change; `title` is already `Optional[str] = None`. `Optional` is already imported.)

- [ ] **Step 4: Drop the invented fallbacks in `from_dict`**

In `Meeting.from_dict`, change the two lines:

```python
            meeting_type=d.get("meeting_type"),
            ...
            event_kind=d.get("event_kind"),
```

(Remove the `, "Regular Session"` and `, "council"` second arguments.)

- [ ] **Step 5: Run the model tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`
Expected: PASS (all, including the pre-existing clip/summary tests).

- [ ] **Step 6: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "fix(models): stop fabricating council/Regular Session defaults"
```

---

## Task 2: Layer 3 — publish backstop guard

**Files:**
- Modify: `src/publish.py` (imports + top of `_upsert_meeting`, ~193-202)
- Test: `tests/test_publish.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_publish.py` (it already imports `Meeting`, `_upsert_meeting`, `pytest`, and defines `RecordingCursor`):

```python
def test_upsert_rejects_missing_event_kind():
    cur = RecordingCursor()
    meeting = Meeting(
        meeting_id="mystery", city="Bloomington", date="2026-02-18",
        meeting_type="Regular Session", event_kind=None,
    )
    with pytest.raises(ValueError):
        _upsert_meeting(cur, meeting, None)
    assert cur.calls == []  # rejected before any DB work


def test_upsert_rejects_invalid_event_kind():
    cur = RecordingCursor()
    meeting = Meeting(
        meeting_id="mystery", city="Bloomington", date="2026-02-18",
        meeting_type="Regular Session", event_kind="townhall",
    )
    with pytest.raises(ValueError):
        _upsert_meeting(cur, meeting, None)


def test_upsert_rejects_missing_meeting_type():
    cur = RecordingCursor()
    meeting = Meeting(
        meeting_id="mystery", city="Bloomington", date="2026-02-18",
        meeting_type=None, event_kind="council",
    )
    with pytest.raises(ValueError):
        _upsert_meeting(cur, meeting, None)


def test_upsert_rejects_council_without_city():
    cur = RecordingCursor()
    meeting = Meeting(
        meeting_id="mystery", city=None, date="2026-02-18",
        meeting_type="Regular Session", event_kind="council",
    )
    with pytest.raises(ValueError):
        _upsert_meeting(cur, meeting, None)


def test_upsert_allows_cityless_forum():
    # Non-civic kinds legitimately have no city; guard must not block them.
    cur = RecordingCursor(select_row=("existing-uuid",))
    meeting = Meeting(
        meeting_id="forum-1", city=None, date="2026-02-18",
        meeting_type="Candidate Forum", event_kind="forum",
    )
    _upsert_meeting(cur, meeting, None)  # must not raise
    assert cur.calls  # proceeded to DB work
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_publish.py -k "rejects or cityless_forum" -v`
Expected: the four `rejects_*` tests FAIL (no guard yet — e.g. `None` kind currently flows through). `cityless_forum` may pass or fail depending on existing flow; it must pass after Step 3.

- [ ] **Step 3: Add the import**

In `src/publish.py`, near the existing `from .event_entities import validate_event_entities`:

```python
from .event_kinds import validate_event_kind
```

- [ ] **Step 4: Add the guard at the top of `_upsert_meeting`**

In `src/publish.py`, make the guard the **first** statements in `_upsert_meeting` (before `_resolve_chamber_id`), so a bad meeting never touches the cursor:

```python
def _upsert_meeting(cur, meeting: Meeting, body_slug: Optional[str]) -> str:
    """Insert or update the meeting row. Returns the meetings.meetings UUID."""
    # Backstop: never let a guessed/missing classification reach the DB.
    validate_event_kind(meeting.event_kind or "")  # raises ValueError if None/empty/invalid
    if not (meeting.meeting_type or "").strip():
        raise ValueError(f"{meeting.meeting_id}: meeting_type is required to publish")
    if meeting.event_kind in ("council", "school_board") and not (meeting.city or "").strip():
        raise ValueError(
            f"{meeting.meeting_id}: city is required to publish a {meeting.event_kind} meeting"
        )

    chamber_id = _resolve_chamber_id(cur, body_slug)
    ...
```

(Leave the rest of the function unchanged. Note the existing later local `kind, playback_url = resolve_playback(...)` is untouched — do not introduce a `kind` variable here.)

- [ ] **Step 5: Run the publish tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_publish.py -v`
Expected: PASS. (The pre-existing `test_publish_writes_chamber_id_for_council` and `test_upsert_meeting_writes_title_and_event_kind` already pass valid meetings, so they still pass.)

- [ ] **Step 6: Commit**

```bash
git add src/publish.py tests/test_publish.py
git commit -m "fix(publish): reject missing/invalid event_kind and city-less civic meetings"
```

---

## Task 3: Layer 1 — `_resolve_metadata` enforcement + event_kind prompt + required date

**Files:**
- Modify: `run_local.py:2588-2589` (add `EVENT_KIND_DEFAULT`), `run_local.py:2600-2635` (`_resolve_metadata`)
- Test: `tests/test_metadata_prompt.py` (rewrite)

- [ ] **Step 1: Rewrite the tests to encode the new behavior**

Replace the entire body of `tests/test_metadata_prompt.py` with:

```python
"""_resolve_metadata: explicit metadata required; prompts interactively; --default
opts into civic defaults (never date); non-interactive + unset hard-fails."""
from __future__ import annotations

import argparse
import pytest
import run_local


def _args(**kw):
    base = dict(city=None, date="", meeting_type=None, title=None,
                event_kind=None, default=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_default_flag_fills_civic_but_requires_date(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not prompt with --default")))
    args = _args(default=True, date="2026-06-09")
    run_local._resolve_metadata(args)
    assert args.city == "Bloomington"
    assert args.meeting_type == "Regular Session"
    assert args.event_kind == "council"
    assert args.date == "2026-06-09"


def test_default_flag_without_date_raises(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    args = _args(default=True)  # date unset
    with pytest.raises(ValueError, match="--date"):
        run_local._resolve_metadata(args)


def test_non_tty_unset_raises_naming_fields(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    args = _args()
    with pytest.raises(ValueError) as exc:
        run_local._resolve_metadata(args)
    msg = str(exc.value)
    assert "--event-kind" in msg and "--meeting-type" in msg and "--date" in msg


def test_non_tty_explicit_flags_ok(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    args = _args(city="Monroe County", date="2026-05-01",
                 meeting_type="Candidate Forum", event_kind="forum")
    run_local._resolve_metadata(args)
    assert (args.city, args.date, args.meeting_type, args.event_kind) == (
        "Monroe County", "2026-05-01", "Candidate Forum", "forum")


def test_non_tty_forum_needs_no_city(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no prompt")))
    # forum: city not required; meeting_type + date + kind given explicitly.
    args = _args(date="2026-05-01", meeting_type="Forum", event_kind="forum")
    run_local._resolve_metadata(args)
    assert args.city is None
    assert args.event_kind == "forum"


def test_interactive_prompts_event_kind_then_city_then_date(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # prompt order: event_kind (Enter->council), city (typed), date (typed).
    answers = iter(["", "Carmel", "2026-06-09"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    args = _args(meeting_type="Plan Commission")  # provided -> not prompted
    run_local._resolve_metadata(args)
    assert args.event_kind == "council"
    assert args.city == "Carmel"
    assert args.date == "2026-06-09"
    assert args.meeting_type == "Plan Commission"


def test_interactive_date_reprompts_until_given(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # event_kind Enter->council, city Enter->Bloomington, date: "" then real.
    answers = iter(["", "", "", "2026-03-03"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    args = _args(meeting_type="Regular Session")
    run_local._resolve_metadata(args)
    assert args.date == "2026-03-03"


def test_interactive_keeps_cli_values(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("nothing to prompt")))
    args = _args(city="Bloomington", date="2026-01-01",
                 meeting_type="Special", event_kind="council")
    run_local._resolve_metadata(args)
    assert (args.city, args.date, args.meeting_type, args.event_kind) == (
        "Bloomington", "2026-01-01", "Special", "council")


def test_invalid_event_kind_flag_raises(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = _args(city="X", date="2026-01-01", meeting_type="Y", event_kind="bogus")
    with pytest.raises(ValueError):
        run_local._resolve_metadata(args)


def test_batch_mode_does_not_prompt_even_on_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)  # batch on a terminal
    monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("batch must not prompt")))
    args = _args(batch_mode=True)  # unset + non-interactive-by-batch
    with pytest.raises(ValueError):
        run_local._resolve_metadata(args)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_metadata_prompt.py -v`
Expected: multiple FAIL (current code defaults silently on non-tty and never prompts event_kind).

- [ ] **Step 3: Add the `EVENT_KIND_DEFAULT` constant**

In `run_local.py`, next to the existing constants (currently lines 2588-2589):

```python
CITY_DEFAULT = "Bloomington"
MEETING_TYPE_DEFAULT = "Regular Session"
EVENT_KIND_DEFAULT = "council"
```

- [ ] **Step 4: Replace `_resolve_metadata`**

Replace the whole `_resolve_metadata` function (currently `run_local.py:2600-2635`) with:

```python
def _resolve_metadata(args) -> None:
    """Fill args.city/date/meeting_type/event_kind for a new run.

    Per field, three modes:
      * supplied on the CLI            -> kept (event_kind validated vs the enum)
      * --default                      -> civic defaults applied silently for
                                          city/meeting_type/event_kind; date is
                                          NEVER defaulted and is always required
      * interactive TTY (no --default) -> prompt for each unset field
    Non-interactive (no TTY, --default, or batch_mode) with neither a CLI value
    nor an applicable default raises ValueError naming every missing field, so a
    run never silently guesses metadata. Prompt order: event_kind, city,
    meeting_type, date, title.
    """
    use_defaults = bool(getattr(args, "default", False))
    interactive = (
        sys.stdin.isatty()
        and not use_defaults
        and not getattr(args, "batch_mode", False)
    )

    # event_kind first: it decides whether a city is required.
    if args.event_kind is not None:
        args.event_kind = validate_event_kind(args.event_kind)
    elif interactive:
        ans = input(
            f"  Event kind [{EVENT_KIND_DEFAULT}] ({'/'.join(EVENT_KINDS)}): "
        ).strip()
        args.event_kind = validate_event_kind(ans or EVENT_KIND_DEFAULT)
    elif use_defaults:
        args.event_kind = EVENT_KIND_DEFAULT

    requires_city = args.event_kind in ("council", "school_board")

    if args.city is None and requires_city:
        if interactive:
            ans = input(f"  City [{CITY_DEFAULT}]: ").strip()
            args.city = ans or CITY_DEFAULT
        elif use_defaults:
            args.city = CITY_DEFAULT

    if args.meeting_type is None:
        if interactive:
            ans = input(f"  Meeting type [{MEETING_TYPE_DEFAULT}]: ").strip()
            args.meeting_type = ans or MEETING_TYPE_DEFAULT
        elif use_defaults:
            args.meeting_type = MEETING_TYPE_DEFAULT

    if not args.date and interactive:
        while not args.date:
            args.date = input("  Date YYYY-MM-DD (required): ").strip()

    # Anything still unset means we could neither get it explicitly nor were
    # told to default it (or it has no default, like date). Fail loudly.
    missing = []
    if args.event_kind is None:
        missing.append("--event-kind")
    if requires_city and args.city is None:
        missing.append("--city")
    if args.meeting_type is None:
        missing.append("--meeting-type")
    if not args.date:
        missing.append("--date")
    if missing:
        raise ValueError(
            "Refusing to guess meeting metadata: missing "
            + ", ".join(missing)
            + f". Pass them explicitly, or pass --default for {CITY_DEFAULT} / "
            f"{MEETING_TYPE_DEFAULT} / {EVENT_KIND_DEFAULT} "
            "(--date is always required)."
        )

    if args.event_kind in _INTERVIEW_KINDS and not args.title and interactive:
        ans = input("  Title (required for interview/media events): ").strip()
        args.title = ans or None
```

(`validate_event_kind` and `EVENT_KINDS` are already imported at `run_local.py:43`. `_INTERVIEW_KINDS` is defined just above at `run_local.py:2597`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_metadata_prompt.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add run_local.py tests/test_metadata_prompt.py
git commit -m "fix(run_local): require explicit meeting metadata; prompt event_kind; date mandatory"
```

---

## Task 4: Single-run CLI — clean error instead of traceback

**Files:**
- Modify: `run_local.py:3940-3941` (the `else: _resolve_metadata(args)` branch in `main`)

- [ ] **Step 1: Wrap the single-run call**

In `run_local.py`, change the non-resume branch (currently `run_local.py:3940-3941`):

```python
    else:
        try:
            _resolve_metadata(args)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(2)
```

- [ ] **Step 2: Verify the clean-exit behavior manually**

Run (no TTY via the pipe, no metadata, no `--default`):

```bash
echo "" | .venv/bin/python run_local.py --input /nonexistent.mp4 ; echo "exit=$?"
```

Expected: prints a single `Error: Refusing to guess meeting metadata: missing ...` line and `exit=2` — **no Python traceback**.

- [ ] **Step 3: Commit**

```bash
git add run_local.py
git commit -m "fix(run_local): exit cleanly when metadata is under-specified"
```

---

## Task 5: Layer 4 — batch routes through enforcement (and fixes missing event_kind)

**Files:**
- Modify: `run_local.py:1753-1758` and `1772-1778` (`_parse_batch_inputs` — stop hardcoding)
- Modify: `run_local.py:1807-1857` (`_run_batch` — resume precheck guard, batch_args, resolve+mid, remove today fallback)
- Test: `tests/test_batch_metadata.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_metadata.py`:

```python
"""Batch parsing leaves metadata unset (no hardcoded Bloomington); batch
resolution is non-interactive and hard-fails under-specified entries."""
from __future__ import annotations

import argparse
import pytest
import run_local


def test_parse_batch_dir_does_not_hardcode_city(tmp_path):
    (tmp_path / "2026-05-01-something.mp4").write_bytes(b"x")
    entries = run_local._parse_batch_inputs(str(tmp_path))
    assert len(entries) == 1
    e = entries[0]
    assert e["date"] == "2026-05-01"
    assert e["city"] is None
    assert e["meeting_type"] is None


def test_parse_batch_textfile_omitted_fields_are_none(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("/videos/a.mp4 2026-05-01\n")  # only path + date
    entries = run_local._parse_batch_inputs(str(f))
    assert entries[0]["city"] is None
    assert entries[0]["meeting_type"] is None


def test_parse_batch_textfile_keeps_supplied_fields(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("/videos/a.mp4 2026-05-01 Bloomington Special\n")
    e = run_local._parse_batch_inputs(str(f))[0]
    assert e["city"] == "Bloomington"
    assert e["meeting_type"] == "Special"


def test_batch_underspecified_entry_resolution_raises(monkeypatch):
    # batch_mode forces non-interactive even on a TTY -> ValueError, which
    # _run_batch records per-entry as a failure (sibling entries continue).
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    args = argparse.Namespace(
        city=None, date="2026-05-01", meeting_type=None, title=None,
        event_kind=None, default=False, batch_mode=True,
    )
    with pytest.raises(ValueError):
        run_local._resolve_metadata(args)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_batch_metadata.py -v`
Expected: the three `parse_batch` tests FAIL (currently return `"Bloomington"`/`"Regular Session"`); the resolution test PASSES (Task 3 already in place).

- [ ] **Step 3: Stop hardcoding in `_parse_batch_inputs` (directory branch)**

In `run_local.py`, the directory-entries append (currently `run_local.py:1753-1758`):

```python
                entries.append({
                    "input": str(f),
                    "date": date,
                    "city": None,
                    "meeting_type": None,
                })
```

- [ ] **Step 4: Stop hardcoding in `_parse_batch_inputs` (text-file branch)**

In `run_local.py`, the text-file `entry` dict (currently `run_local.py:1772-1777`):

```python
                entry = {
                    "input": parts[0],
                    "date": parts[1] if len(parts) > 1 else "",
                    "city": parts[2] if len(parts) > 2 else None,
                    "meeting_type": parts[3] if len(parts) > 3 else None,
                }
```

- [ ] **Step 5: Guard the `--batch-resume` precheck against an unset meeting_type**

In `run_local.py`, the precheck condition (currently `run_local.py:1807`):

```python
        if args.batch_resume and entry["date"] and entry.get("meeting_type"):
```

(When `meeting_type` is unset the `mid` cannot be computed yet; skip the precheck and let resolution/normal flow decide.)

- [ ] **Step 6: Add `event_kind`/`default`/`title` to `batch_args`**

In `run_local.py`, inside the `batch_args = argparse.Namespace(...)` block (currently ends ~`run_local.py:1841`), add these keys (alongside the existing ones):

```python
            event_kind=getattr(args, "event_kind", None),
            default=getattr(args, "default", False),
            title=getattr(args, "title", None),
```

(`batch_mode=True` is already set in this block — that is what forces non-interactive resolution.)

- [ ] **Step 7: Resolve metadata before `mid`/`run_pipeline`; drop the today fallback**

In `run_local.py`, replace the block that currently auto-defaults the date and builds `mid` (currently `run_local.py:1844-1857`) with:

```python
        # Resolve metadata up front (batch is non-interactive): an
        # under-specified entry fails here and is recorded, not silently stamped.
        try:
            _resolve_metadata(batch_args)
        except ValueError as e:
            print(f"\n  ERROR: {e}")
            results.append({"input": entry["input"], "status": f"failed: {e}", "meeting_id": ""})
            print()
            continue

        mid = f"{batch_args.date}-{batch_args.meeting_type.lower().replace(' ', '-')}"

        try:
            run_pipeline(batch_args)
            results.append({"input": entry["input"], "status": "completed", "meeting_id": mid})
        except Exception as e:
            print(f"\n  ERROR: {e}")
            results.append({"input": entry["input"], "status": f"failed: {e}", "meeting_id": mid})

        print()
```

(This removes the `if not batch_args.date: ... date.today()` fallback entirely. `_resolve_metadata` now guarantees `batch_args.date` and `batch_args.meeting_type` are non-empty before `mid` is built.)

- [ ] **Step 8: Run the batch tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_batch_metadata.py -v`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add run_local.py tests/test_batch_metadata.py
git commit -m "fix(run_local): batch requires explicit metadata; route through resolver (fixes missing event_kind)"
```

---

## Task 6: Update `--default` help text

**Files:**
- Modify: `run_local.py:3544-3546`

- [ ] **Step 1: Update the help string**

In `run_local.py`, the `--default` argument help (currently `run_local.py:3544-3546`):

```python
    parser.add_argument("--default", action="store_true",
                        help="Skip metadata prompts and use civic defaults "
                             f"({CITY_DEFAULT} / {MEETING_TYPE_DEFAULT} / "
                             f"{EVENT_KIND_DEFAULT}); --date is still required")
```

- [ ] **Step 2: Verify the help renders**

Run: `.venv/bin/python run_local.py --help 2>&1 | grep -A2 -- "--default"`
Expected: shows the new text mentioning the three civic defaults and that `--date` is still required.

- [ ] **Step 3: Commit**

```bash
git add run_local.py
git commit -m "docs(run_local): clarify --default no longer fills date"
```

---

## Task 7: Full regression run

- [ ] **Step 1: Run the entire suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. If any pre-existing test constructs a `Meeting` relying on the old `"council"`/`"Regular Session"` defaults, or publishes a city-less council meeting, fix that test to pass explicit metadata (the new behavior is intentional) — do **not** weaken the guards.

- [ ] **Step 2: Final commit (only if Step 1 required test fixes)**

```bash
git add -A
git commit -m "test: update fixtures for explicit-metadata requirement"
```

---

## Self-Review Notes

- **Spec coverage:** Layer 1 → Task 3 (+ Task 4 clean exit, Task 6 help); Layer 2 → Task 1; Layer 3 → Task 2; Layer 4 (batch) → Task 5; date-required-everywhere → Tasks 3 & 5; testing section → tests in every task + Task 7.
- **Type consistency:** `event_kind`/`meeting_type` are `Optional[str]` after Task 1; every guard uses `meeting.event_kind or ""` / `(meeting.x or "").strip()` to stay `None`-safe; `_resolve_metadata` guarantees non-`None` `date`/`meeting_type` (and `event_kind`, plus `city` for civic kinds) before downstream use.
- **Known remaining gap (out of scope):** the `--resume` branch (`run_local.py:3929-3939`) still falls back to civic defaults when no saved state preserved them; the Layer 3 backstop still applies, so it cannot write an invalid kind, but a resumed council with lost state could still default city to Bloomington. Revisit separately if resume-without-state proves to be a real path.

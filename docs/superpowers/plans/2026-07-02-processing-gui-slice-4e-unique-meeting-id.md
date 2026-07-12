# Processing GUI — Slice 4e: Unique meeting_id on collision

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Data-safety fix. `meeting_id` derives as `{date}-{slug(meeting_type)}`, so two *different* videos with the same date + label (e.g. two interviews on 2026-05-15 → both `2026-05-15-interview`) collide: `launch_run` would spawn into the **existing** meeting's directory and clobber/re-run the wrong video. The source-key dedup does NOT catch this (different videos → different source keys → no dedup match). Fix: when the derived id already belongs to a **different source**, append a numeric suffix (`…-interview-2`, `-3`, …) so each video is its own meeting. Only affects `launch_run` (new-meeting launches); `launch_redo`/`launch_resume` operate on a known id and are untouched.

**Goal:** Two same-date/same-label videos become two distinct meetings; re-submitting the *same* video still reuses its id (no runaway suffixes).

**Architecture:** `derive_meeting_id` stays pure (base id). `launch_run` computes the source key of its input and passes both to a new `_unique_meeting_id(base_id, source)` that scans `MEETINGS_DIR`: returns the base if free or if the existing meeting has the SAME source (re-run), else bumps `-2/-3/…` until free-or-same-source. Reuses `_meeting_source_key` (from 3c) and `src.source_key.source_key`.

**Tech Stack:** `gui/runner.py`. Tests use `tagged_meeting_dir` + `PipelineState.source_key`; fake Popen; no server.

---

### Task 1: `_unique_meeting_id` + wire into `launch_run`

**Files:**
- Modify: `gui/runner.py`
- Test: `tests/test_gui_runner.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
def _set_source(mdir, source_key_value):
    from src.checkpoint import PipelineState
    st = PipelineState(mdir); st.source_key = source_key_value; st.save()


def test_unique_meeting_id_free_base(tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    assert _unique_meeting_id("2026-05-15-interview", "youtube:AAA") == "2026-05-15-interview"


def test_unique_meeting_id_same_source_reuses(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4)
    _set_source(mdir, "youtube:AAA")
    # same video re-submitted -> reuse the existing id (no new dir)
    assert _unique_meeting_id("2026-05-15-interview", "youtube:AAA") == "2026-05-15-interview"


def test_unique_meeting_id_bumps_on_different_source(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4)
    _set_source(mdir, "youtube:AAA")
    # a DIFFERENT video, same date+label -> must not collide
    assert _unique_meeting_id("2026-05-15-interview", "youtube:ZZZ") == "2026-05-15-interview-2"


def test_unique_meeting_id_bumps_past_multiple(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    _set_source(tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4), "youtube:AAA")
    _set_source(tagged_meeting_dir("x", meeting_id="2026-05-15-interview-2", completed_stage=4), "youtube:BBB")
    assert _unique_meeting_id("2026-05-15-interview", "youtube:ZZZ") == "2026-05-15-interview-3"


def test_launch_run_bumps_colliding_id(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    _set_source(tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4), "youtube:AAA")
    p = runner.RunParams(input="https://youtu.be/ZZZ", date="2026-05-15",
                         meeting_type="Interview", event_kind="news_clip")
    mid = runner.launch_run(p, python_exe="py", script="s", popen=_FakePopen)
    assert mid == "2026-05-15-interview-2"           # new video -> distinct meeting
    assert (tmp_meetings_dir / "2026-05-15-interview-2").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "unique_meeting_id or bumps_colliding" -v`
Expected: FAIL — `_unique_meeting_id` missing; `launch_run` collides (returns base id).

- [ ] **Step 3: Implement in `gui/runner.py`**

Add the helper (near `_meeting_source_key`):

```python
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
```

Wire it into `launch_run` — derive the base, then uniquify against the input's source:

```python
def launch_run(p: RunParams, *, python_exe: str, script: str, popen=subprocess.Popen) -> str:
    """Spawn run_local.py for a NEW meeting in the background. Returns the meeting_id
    (bumped with a -N suffix if the derived id would collide with a different source)."""
    from src.source_key import source_key
    base_id = derive_meeting_id(p)
    meeting_id = _unique_meeting_id(base_id, source_key(p.input))
    meeting_dir = ensure_drive_structure(meeting_id)
    cmd = build_run_command(python_exe, script, p, meeting_id)
    return _spawn(meeting_id, meeting_dir, cmd, popen)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -v`
Expected: PASS (new tests + all existing runner tests — `derive_meeting_id`, `build_run_command`, redo/resume unchanged).

- [ ] **Step 5: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "fix(gui): give a colliding date+label meeting a unique -N id (no clobber)

Two different videos with the same {date}-{label} derived the same meeting_id
and the second would launch into the first's directory. launch_run now bumps
the id (-2, -3, ...) when the derived id belongs to a different source; the same
source still reuses its id (re-run).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. NO server, NO real subprocess (fake Popen).

---

## Self-Review

**Spec coverage:** colliding date+label → unique -N id (Task 1 `_unique_meeting_id` + `launch_run` wiring) ✅ · same source reuses its id, no runaway suffix (Task 1 same-source branch) ✅ · only `launch_run` affected; redo/resume use a known id (untouched) ✅ · reuses `_meeting_source_key` + `source_key` (no dup) ✅.

**Placeholder scan:** none.

**Type consistency:** `_unique_meeting_id(base_id, source) -> str`; `launch_run` returns it (the POST /new route already redirects to `/meetings/<returned_id>/run`, so the suffixed id flows through). `derive_meeting_id` stays pure. `_meeting_source_key` returns the state key or normalized audio_source (so it matches what `source_key(input)` produces). A meeting with an unknown source (None) compares unequal → bumps (safe: never clobbers an unknown-source meeting).

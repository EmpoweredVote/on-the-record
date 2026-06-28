# Clip Window (`--clip`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--clip START END` so the pipeline transcribes only a contiguous slice of a source (e.g. a politician interview inside a long podcast), while the published artifact keeps source-absolute timestamps and the site plays/links the full original.

**Architecture:** The clip is an *ingest-time compute optimization*, never a domain artifact (see `docs/adr/0001-clip-window-ingest-time-only.md`). ffmpeg cuts the window during the existing normalize pass (accurate seek → frame-exact, offset authoritative). The pipeline runs **clip-local (0-based) internally**; the start offset is persisted on `PipelineState` and on the `Meeting`, and added back **once at each output boundary** (publish DB writes, exports) via a single pure helper, so all published timestamps live in the full source's timeline. The site auto-starts playback at the clip start and shows a provenance note. Feature threads through four repos: on-the-record → supabase → ev-accounts → web.

**Tech Stack:** Python 3 (pytest), ffmpeg, Postgres (Supabase), Node/TypeScript (ev-accounts API), Next.js (web).

**Conventions:**
- TDD throughout: red → green → commit.
- All git commit messages end with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (omitted from the sample messages below for brevity — add it to every commit).
- Python tests: `.venv/bin/python -m pytest` (system python lacks deps — see project memory).
- Storage: two nullable columns `clip_start_seconds` / `clip_end_seconds`. `NULL` = whole recording (the entire existing corpus stays null — zero backfill).

---

## File Structure

**on-the-record (pipeline):**
- Create: `src/clip.py` — pure helpers: `parse_clip_time()`, `absolutize_meeting_times()`. Single home for time-format parsing and the offset transform.
- Modify: `src/models.py` — add `clip_start_seconds` / `clip_end_seconds` to `Meeting` (+ `to_dict`/`from_dict`).
- Modify: `src/checkpoint.py` — add the two fields to `PipelineState` (+ `_load`/`save`).
- Modify: `src/ingest.py` — `normalize_audio()` accepts a clip window; extract a pure `_normalize_cmd()` for the ffmpeg arg list.
- Modify: `run_local.py` — `--clip` argument, a `_reconcile_clip_window()` helper (persist-once / error-on-mismatch, mirrors `--body`), wire into Stage 1 + set on the `Meeting`.
- Modify: `src/publish.py` — absolutize at `publish_meeting` entry; write the two columns in `_upsert_meeting`.
- Modify: `src/export.py` — absolutize at `export_all` entry.

**supabase:**
- Create: `supabase/migrations/0004_clip_window.sql`.

**ev-accounts:**
- Modify: `backend/src/lib/meetingsService.ts` — `Meeting` interface, `MeetingRow` interface, `mapMeeting`, `MEETING_COLS`.

**web:**
- Modify: `web/lib/types.ts` — `Meeting` type.
- Modify: `web/lib/queries.ts` — `mapMeeting`.
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx` — auto-start at clip start + provenance note.
- Modify (optional): `web/app/meetings/[meetingId]/players/YouTubePlayer.tsx` — `start` playerVar.

---

## Task 1: Pure clip helpers (`src/clip.py`)

**Files:**
- Create: `src/clip.py`
- Test: `tests/test_clip.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clip.py
"""Tests for clip-window time parsing and the source-absolute offset transform."""

import copy

import pytest

from src.clip import absolutize_meeting_times, parse_clip_time
from src.models import Meeting, MeetingSummary, Segment, SummarySection


# --- parse_clip_time -------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("1380", 1380.0),
        ("1380.5", 1380.5),
        ("23:00", 1380.0),
        ("0:30", 30.0),
        ("1:05:00", 3900.0),
        ("01:05:00", 3900.0),
        ("00:00", 0.0),
    ],
)
def test_parse_clip_time_valid(text, expected):
    assert parse_clip_time(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "12:60", "-5", "1:-1", "  "])
def test_parse_clip_time_invalid(text):
    with pytest.raises(ValueError):
        parse_clip_time(text)


# --- absolutize_meeting_times ----------------------------------------------

def _meeting_with_times(clip_start):
    return Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        duration_seconds=1500.0,
        clip_start_seconds=clip_start,
        clip_end_seconds=(clip_start + 1500.0) if clip_start else None,
        segments=[
            Segment(segment_id=0, start_time=0.0, end_time=10.0, speaker_label="S0", text="hi"),
            Segment(segment_id=1, start_time=10.0, end_time=20.0, speaker_label="S1", text="yo"),
        ],
        summary=MeetingSummary(sections=[
            SummarySection(section_type="discussion", title="T", content="c",
                           start_time=0.0, end_time=20.0, start_segment=0, end_segment=1),
        ]),
    )


def test_absolutize_shifts_segment_and_section_times():
    m = _meeting_with_times(1380.0)
    out = absolutize_meeting_times(m)
    assert [s.start_time for s in out.segments] == [1380.0, 1390.0]
    assert [s.end_time for s in out.segments] == [1390.0, 1400.0]
    assert out.summary.sections[0].start_time == 1380.0
    assert out.summary.sections[0].end_time == 1400.0


def test_absolutize_does_not_shift_duration_or_clip_fields():
    m = _meeting_with_times(1380.0)
    out = absolutize_meeting_times(m)
    assert out.duration_seconds == 1500.0          # a length, not a timestamp
    assert out.clip_start_seconds == 1380.0         # raw window preserved
    assert out.clip_end_seconds == 2880.0


def test_absolutize_noop_when_no_clip():
    m = _meeting_with_times(None)
    out = absolutize_meeting_times(m)
    assert [s.start_time for s in out.segments] == [0.0, 10.0]


def test_absolutize_returns_copy_does_not_mutate_input():
    m = _meeting_with_times(1380.0)
    absolutize_meeting_times(m)
    assert m.segments[0].start_time == 0.0          # original untouched
    assert m.summary.sections[0].start_time == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clip.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.clip'`

- [ ] **Step 3: Write the implementation**

```python
# src/clip.py
"""Clip-window helpers: time-format parsing and the source-absolute offset transform.

A clip window is a single contiguous slice of a source recording that was
transcribed (e.g. an interview inside a longer podcast). The pipeline runs
clip-local (0-based) internally; these helpers convert published timestamps
back into the full source's timeline. See docs/adr/0001-clip-window-ingest-time-only.md.
"""

from __future__ import annotations

import copy

from .models import Meeting


def parse_clip_time(text: str) -> float:
    """Parse a clip boundary as seconds, HH:MM:SS, or MM:SS into float seconds.

    Accepts: "1380", "1380.5", "23:00" (MM:SS), "1:05:00" (HH:MM:SS).
    Raises ValueError on empty/malformed input, negative values, or a
    minutes/seconds field >= 60.
    """
    s = (text or "").strip()
    if not s:
        raise ValueError("empty clip time")

    if ":" not in s:
        value = float(s)  # raises ValueError on non-numeric
        if value < 0:
            raise ValueError(f"clip time cannot be negative: {text!r}")
        return value

    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid clip time {text!r} — use SS, MM:SS, or HH:MM:SS")

    nums = [float(p) for p in parts]  # raises ValueError on non-numeric
    if any(n < 0 for n in nums):
        raise ValueError(f"clip time cannot be negative: {text!r}")
    # Only the leading field may exceed 59 (e.g. 90:00 = 90 min is disallowed;
    # use 1:30:00). Sub-fields are clock fields and must be < 60.
    if any(n >= 60 for n in nums[1:]):
        raise ValueError(f"invalid clip time {text!r} — minutes/seconds must be < 60")

    if len(nums) == 2:
        minutes, seconds = nums
        return minutes * 60 + seconds
    hours, minutes, seconds = nums
    return hours * 3600 + minutes * 60 + seconds


def absolutize_meeting_times(meeting: Meeting) -> Meeting:
    """Return a deep copy of `meeting` with all timestamps shifted into the
    full source's timeline.

    Adds `clip_start_seconds` (the offset) to every segment start/end and every
    summary-section start/end. Lengths (`duration_seconds`) and the clip-window
    fields themselves are left untouched. A meeting with no clip window
    (`clip_start_seconds` falsy) is returned as an unchanged copy.
    """
    offset = meeting.clip_start_seconds or 0.0
    out = copy.deepcopy(meeting)
    if not offset:
        return out

    for seg in out.segments:
        seg.start_time += offset
        seg.end_time += offset
    if out.summary:
        for sec in out.summary.sections:
            sec.start_time += offset
            sec.end_time += offset
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clip.py -v`
Expected: PASS (all)

> Note: Task 1 depends on `Meeting` having `clip_start_seconds` / `clip_end_seconds`. If you implement Task 1 before Task 2, the tests will fail to construct the `Meeting`. **Implement Task 2 first, or implement them together and commit once.** The order below (Task 2 then re-run Task 1) is the safe sequence.

- [ ] **Step 5: Commit** (after Task 2 is green too)

```bash
git add src/clip.py tests/test_clip.py
git commit -m "feat(clip): add parse_clip_time and absolutize_meeting_times helpers"
```

---

## Task 2: Add clip fields to the `Meeting` model

**Files:**
- Modify: `src/models.py:235-296` (the `Meeting` dataclass + `to_dict`/`from_dict`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py — add this test
from src.models import Meeting


def test_meeting_clip_window_roundtrip():
    m = Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        clip_start_seconds=1380.0, clip_end_seconds=2880.0,
    )
    d = m.to_dict()
    assert d["clip_start_seconds"] == 1380.0
    assert d["clip_end_seconds"] == 2880.0
    back = Meeting.from_dict(d)
    assert back.clip_start_seconds == 1380.0
    assert back.clip_end_seconds == 2880.0


def test_meeting_clip_window_defaults_none():
    m = Meeting(meeting_id="m1", city="X", date="2026-06-28")
    assert m.clip_start_seconds is None
    assert m.clip_end_seconds is None
    # Legacy dicts without the keys still load:
    back = Meeting.from_dict({"meeting_id": "m1", "city": "X", "date": "2026-06-28"})
    assert back.clip_start_seconds is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py -k clip_window -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'clip_start_seconds'`

- [ ] **Step 3: Edit `src/models.py`**

In the `Meeting` dataclass field list (after `event_orgs: list[str] = field(default_factory=list)` at line 251), add:

```python
    clip_start_seconds: Optional[float] = None
    clip_end_seconds: Optional[float] = None
```

In `Meeting.to_dict` (before `if self.summary is not None:` at line 269), add:

```python
        if self.clip_start_seconds is not None:
            d["clip_start_seconds"] = self.clip_start_seconds
        if self.clip_end_seconds is not None:
            d["clip_end_seconds"] = self.clip_end_seconds
```

In `Meeting.from_dict` (inside the `cls(...)` call, after `event_orgs=d.get("event_orgs", []),` at line 295), add:

```python
            clip_start_seconds=d.get("clip_start_seconds"),
            clip_end_seconds=d.get("clip_end_seconds"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_models.py -v && .venv/bin/python -m pytest tests/test_clip.py -v`
Expected: PASS (both files — Task 1 now constructs `Meeting` successfully)

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py src/clip.py tests/test_clip.py
git commit -m "feat(clip): persist clip window on the Meeting model + clip helpers"
```

---

## Task 3: Persist clip fields in `PipelineState`

**Files:**
- Modify: `src/checkpoint.py:26-89` (`PipelineState.__init__`, `_load`, `save`)
- Test: `tests/test_quality_state.py` (existing `PipelineState` test module) or a new `tests/test_checkpoint_clip.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_checkpoint_clip.py
"""Clip-window persistence on PipelineState."""

from src.checkpoint import PipelineState


def test_clip_window_persists_and_reloads(tmp_path):
    state = PipelineState(tmp_path)
    assert state.clip_start_seconds is None
    assert state.clip_end_seconds is None

    state.clip_start_seconds = 1380.0
    state.clip_end_seconds = 2880.0
    state.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.clip_start_seconds == 1380.0
    assert reloaded.clip_end_seconds == 2880.0


def test_clip_window_absent_in_legacy_state_file(tmp_path):
    # A state file written before this feature has no clip keys.
    (tmp_path / "pipeline_state.json").write_text('{"completed_stage": 1}')
    state = PipelineState(tmp_path)
    assert state.clip_start_seconds is None
    assert state.clip_end_seconds is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_checkpoint_clip.py -v`
Expected: FAIL — `AttributeError: 'PipelineState' object has no attribute 'clip_start_seconds'`

- [ ] **Step 3: Edit `src/checkpoint.py`**

In `__init__` (after `self.trusted_coverage: Optional[float] = None` at line 43), add:

```python
        self.clip_start_seconds: Optional[float] = None
        self.clip_end_seconds: Optional[float] = None
```

In `_load` (after `self.trusted_coverage = data.get("trusted_coverage")` at line 61), add:

```python
            self.clip_start_seconds = data.get("clip_start_seconds")
            self.clip_end_seconds = data.get("clip_end_seconds")
```

In `save`'s `data` dict (after `"trusted_coverage": self.trusted_coverage,` at line 77), add:

```python
            "clip_start_seconds": self.clip_start_seconds,
            "clip_end_seconds": self.clip_end_seconds,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_checkpoint_clip.py tests/test_quality_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/checkpoint.py tests/test_checkpoint_clip.py
git commit -m "feat(clip): persist clip window in PipelineState for resume"
```

---

## Task 4: Cut the window in `normalize_audio`

**Files:**
- Modify: `src/ingest.py:27-109` (`normalize_audio`; extract `_normalize_cmd`)
- Test: `tests/test_ingest_clip.py`

The existing ffmpeg call always re-encodes to 16 kHz mono WAV. We add `-ss {start} -to {end}` **after** `-i` (accurate/decode seek → frame-exact cut, so the persisted `clip_start` is authoritative). Extract the command builder into a pure helper so it is unit-testable without invoking ffmpeg.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingest_clip.py
"""ffmpeg command construction for clip windows."""

from src.ingest import _normalize_cmd


def test_normalize_cmd_no_clip_has_no_seek_flags():
    cmd = _normalize_cmd("in.mp4", "out.wav", clip_start=None, clip_end=None)
    assert "-ss" not in cmd and "-to" not in cmd
    assert cmd[:3] == ["ffmpeg", "-y", "-i"]
    assert cmd[-1] == "out.wav"


def test_normalize_cmd_clip_uses_accurate_seek_after_input():
    cmd = _normalize_cmd("in.mp4", "out.wav", clip_start=1380.0, clip_end=2880.0)
    i = cmd.index("-i")
    ss = cmd.index("-ss")
    to = cmd.index("-to")
    # Accurate seek: -ss/-to come AFTER -i (output-side seek), not before.
    assert ss > i and to > i
    assert cmd[ss + 1] == "1380.0"
    assert cmd[to + 1] == "2880.0"


def test_normalize_cmd_start_only():
    cmd = _normalize_cmd("in.mp4", "out.wav", clip_start=1380.0, clip_end=None)
    assert "-ss" in cmd and "-to" not in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ingest_clip.py -v`
Expected: FAIL — `ImportError: cannot import name '_normalize_cmd'`

- [ ] **Step 3: Edit `src/ingest.py`**

Add the pure helper above `normalize_audio` (after the imports, before line 27):

```python
def _normalize_cmd(
    ffmpeg_input: str,
    output_path: str,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> list[str]:
    """Build the ffmpeg arg list for normalizing (and optionally clipping) audio.

    `-ss`/`-to` are placed AFTER `-i` (output-side, decode-accurate seek) so the
    cut is frame-exact and the persisted clip_start is authoritative — never a
    keyframe-rounded approximation.
    """
    cmd = ["ffmpeg", "-y", "-i", ffmpeg_input]
    if clip_start is not None:
        cmd += ["-ss", str(clip_start)]
    if clip_end is not None:
        cmd += ["-to", str(clip_end)]
    cmd += [
        "-ac", str(config.CHANNELS),
        "-ar", str(config.SAMPLE_RATE),
        "-vn",
        str(output_path),
    ]
    return cmd
```

Change the `normalize_audio` signature (line 27) to add the two params:

```python
def normalize_audio(
    input_path: str | Path,
    output_path: str | Path,
    noise_reduce: bool = False,
    cookies_file: str | None = None,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> dict:
```

Replace the inline `subprocess.run([...])` ffmpeg block (lines 78-90) with:

```python
    subprocess.run(
        _normalize_cmd(ffmpeg_input, str(output_path), clip_start, clip_end),
        check=True,
        capture_output=True,
    )
```

Add the window to the returned metadata dict (after `"noise_reduced": noise_reduce,` near line 107):

```python
        "clip_start_seconds": clip_start,
        "clip_end_seconds": clip_end,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ingest_clip.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ingest.py tests/test_ingest_clip.py
git commit -m "feat(clip): cut window in normalize_audio via accurate ffmpeg seek"
```

---

## Task 5: `--clip` CLI argument, reconciliation, and wiring

**Files:**
- Modify: `run_local.py` — argparse (near line 3355), a new `_reconcile_clip_window()` helper (place it near `ensure_body_roster_cached`, ~line 106), wiring in `run_pipeline` (the Stage 1 region ~line 737-766 and the `Meeting(...)` construction ~line 716).
- Test: `tests/test_clip_reconcile.py`

### 5a — The reconciliation helper (TDD)

Persist-once / error-on-mismatch, mirroring the `--body` D-01/D-02 pattern. Because `audio.wav` is cut at Stage 1 and can't be re-cut in place, a conflicting `--clip` on an already-ingested meeting is a hard error directing the operator to a fresh `--meeting-id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_clip_reconcile.py
"""Reconciliation of the --clip flag against persisted PipelineState."""

import pytest

from src.checkpoint import PipelineStage, PipelineState
from run_local import _reconcile_clip_window


def test_first_run_persists_window(tmp_path):
    state = PipelineState(tmp_path)
    start, end = _reconcile_clip_window(state, 1380.0, 2880.0)
    assert (start, end) == (1380.0, 2880.0)
    assert PipelineState(tmp_path).clip_start_seconds == 1380.0  # saved


def test_resume_without_flag_reads_persisted(tmp_path):
    state = PipelineState(tmp_path)
    state.clip_start_seconds, state.clip_end_seconds = 1380.0, 2880.0
    state.save()
    start, end = _reconcile_clip_window(state, None, None)
    assert (start, end) == (1380.0, 2880.0)


def test_repassing_same_window_is_noop(tmp_path):
    state = PipelineState(tmp_path)
    state.clip_start_seconds, state.clip_end_seconds = 1380.0, 2880.0
    state.save()
    start, end = _reconcile_clip_window(state, 1380.0, 2880.0)
    assert (start, end) == (1380.0, 2880.0)


def test_conflicting_window_after_ingest_errors(tmp_path):
    state = PipelineState(tmp_path)
    state.clip_start_seconds, state.clip_end_seconds = 1380.0, 2880.0
    state.completed_stage = PipelineStage.INGESTED
    state.save()
    with pytest.raises(SystemExit):
        _reconcile_clip_window(state, 1500.0, 3000.0)


def test_no_clip_anywhere_returns_none(tmp_path):
    state = PipelineState(tmp_path)
    assert _reconcile_clip_window(state, None, None) == (None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clip_reconcile.py -v`
Expected: FAIL — `ImportError: cannot import name '_reconcile_clip_window'`

- [ ] **Step 3: Add `_reconcile_clip_window` to `run_local.py`** (near `ensure_body_roster_cached`)

```python
def _reconcile_clip_window(
    state: "PipelineState",
    cli_start: Optional[float],
    cli_end: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Resolve and persist the clip window, mirroring the --body persist pattern.

    - First run with --clip: persist to state.
    - Resume with no --clip: read from state.
    - Re-pass with the SAME window: no-op.
    - Re-pass with a DIFFERENT window: hard error (audio.wav is already cut and
      cannot be re-clipped in place; the operator must use a fresh --meeting-id).
    """
    from src.checkpoint import PipelineStage

    persisted = (state.clip_start_seconds, state.clip_end_seconds)
    requested = (cli_start, cli_end)

    if cli_start is None and cli_end is None:
        return persisted  # no flag — use whatever is persisted (may be None,None)

    if persisted == (None, None):
        state.clip_start_seconds, state.clip_end_seconds = requested
        state.save()
        return requested

    if requested == persisted:
        return persisted

    # Conflict: a different window was requested for an already-clipped meeting.
    s0, e0 = persisted
    print(
        f"ERROR: this meeting was already clipped to {s0}-{e0}s. The cut audio "
        f"cannot be re-clipped in place. To use a different window, process the "
        f"source into a new --meeting-id.",
        file=sys.stderr,
    )
    sys.exit(2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clip_reconcile.py -v`
Expected: PASS

### 5b — Wire the argument and pass it through (no new unit test; covered by 5a + manual)

- [ ] **Step 5: Add the argparse argument** in `run_local.py` after the `--num-speakers` block (~line 3356):

```python
    parser.add_argument(
        "--clip",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="Transcribe only the contiguous window START..END of the source "
             "(seconds, MM:SS, or HH:MM:SS). The site still plays/links the full "
             "source. Example: --clip 23:00 48:00",
    )
```

- [ ] **Step 6: Parse + reconcile** in `run_pipeline`, immediately after `state = PipelineState(meeting_dir)` (line 616) and before the metadata-persist block:

```python
    # Resolve the clip window (parse the flag, reconcile against persisted state).
    from src.clip import parse_clip_time
    _cli_clip_start = _cli_clip_end = None
    if getattr(args, "clip", None):
        try:
            _cli_clip_start = parse_clip_time(args.clip[0])
            _cli_clip_end = parse_clip_time(args.clip[1])
        except ValueError as exc:
            print(f"ERROR: invalid --clip value: {exc}", file=sys.stderr)
            sys.exit(2)
        if _cli_clip_end <= _cli_clip_start:
            print("ERROR: --clip END must be greater than START", file=sys.stderr)
            sys.exit(2)
    clip_start, clip_end = _reconcile_clip_window(state, _cli_clip_start, _cli_clip_end)
    if clip_start is not None:
        print(f"Clip window: {clip_start}-{clip_end}s (transcribing this slice only)",
              file=sys.stderr)
```

- [ ] **Step 7: Pass the window into ingestion.** In the Stage 1 `else` branch, update the `normalize_audio(...)` call (lines 757-761):

```python
        metadata = normalize_audio(
            audio_path, wav_path,
            noise_reduce=args.noise_reduce,
            cookies_file=getattr(args, "cookies", None),
            clip_start=clip_start,
            clip_end=clip_end,
        )
```

- [ ] **Step 8: Stamp the window on the `Meeting`.** In the `Meeting(...)` constructor (line 716-725), add two kwargs:

```python
        clip_start_seconds=clip_start,
        clip_end_seconds=clip_end,
```

Also handle the resume-after-IDENTIFIED path: in the Stage 4 `if state.is_complete(PipelineStage.IDENTIFIED):` branch, after `meeting = Meeting.from_dict(meeting_data)` (line 1194), re-assert from state so a meeting whose `transcript_named.json` predates this feature still gets the window:

```python
            meeting.clip_start_seconds = state.clip_start_seconds
            meeting.clip_end_seconds = state.clip_end_seconds
```

- [ ] **Step 9: Run the focused + full suite**

Run: `.venv/bin/python -m pytest tests/test_clip_reconcile.py tests/test_clip.py tests/test_models.py tests/test_checkpoint_clip.py tests/test_ingest_clip.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add run_local.py tests/test_clip_reconcile.py
git commit -m "feat(clip): add --clip CLI arg with persist-once reconciliation"
```

---

## Task 6: Apply the offset at publish

**Files:**
- Modify: `src/publish.py` — absolutize at `publish_meeting` entry (line 538); write columns in `_upsert_meeting` (lines 223-296).

> Per the codebase's testing convention (see `tests/test_publish.py` docstring), cursor-bound upserts need a live Postgres and are not unit-tested; only pure helpers are. The offset transform is already fully covered by `tests/test_clip.py` (Task 1). This task is thin wiring + SQL.

- [ ] **Step 1: Absolutize at the entry of `publish_meeting`.** Immediately after the docstring (line 541), before `db_url = _require_db_url()`:

```python
    from .clip import absolutize_meeting_times
    meeting = absolutize_meeting_times(meeting)
```

- [ ] **Step 2: Write the columns in `_upsert_meeting`.**

In the `UPDATE` statement column list (after `playback_kind = %s,` at line 237) add:

```python
              clip_start_seconds = %s,
              clip_end_seconds = %s,
```

In the matching UPDATE params tuple (after `kind,` at line 255) add:

```python
                meeting.clip_start_seconds,
                meeting.clip_end_seconds,
```

In the `INSERT` column list (line 264-268) add `clip_start_seconds, clip_end_seconds` after `playback_kind`:

```python
            INSERT INTO meetings.meetings
              (id, city, date, meeting_type, title, event_kind, duration_seconds,
               audio_source, video_url, status,
               chamber_id, source_url, playback_kind, clip_start_seconds, clip_end_seconds, slug,
               summary, processing_metadata,
               created_at, updated_at)
```

Add two `%s` to the VALUES list (the third VALUES line currently `%s, %s, %s, %s,` for chamber_id, source_url, playback_kind, slug → becomes six placeholders):

```python
            VALUES
              (gen_random_uuid(), %s, %s, %s, %s, %s, %s,
               %s, %s, %s,
               %s, %s, %s, %s, %s, %s,
               %s, %s,
               NOW(), NOW())
```

In the INSERT params tuple (after `kind,` at line 290, before `meeting.meeting_id,`) add:

```python
                meeting.clip_start_seconds,
                meeting.clip_end_seconds,
```

> ⚠️ Placement matters: the column order in the INSERT is `…, playback_kind, clip_start_seconds, clip_end_seconds, slug, …`, so the params must be `…, kind, meeting.clip_start_seconds, meeting.clip_end_seconds, meeting.meeting_id, …`. Double-check the count of `%s` matches the params tuple length.

- [ ] **Step 3: Verify nothing else broke**

Run: `.venv/bin/python -m pytest tests/test_publish.py -v`
Expected: PASS (existing pure-helper tests unaffected)

- [ ] **Step 4: Commit**

```bash
git add src/publish.py
git commit -m "feat(clip): write clip columns and publish source-absolute timestamps"
```

---

## Task 7: Apply the offset at export

**Files:**
- Modify: `src/export.py:219-235` (`export_all`)
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py — add this test
from src.export import export_all
from src.models import Meeting, Segment


def test_export_all_shifts_timestamps_into_source_timeline(tmp_path):
    m = Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        duration_seconds=20.0,
        clip_start_seconds=1380.0, clip_end_seconds=1400.0,
        segments=[Segment(0, 0.0, 10.0, "S0", text="hello world")],
    )
    export_all(m, tmp_path)
    srt = (tmp_path / "subtitles.srt").read_text()
    # 1380s == 00:23:00 — the subtitle must align to the full episode, not 0:00.
    assert "00:23:00" in srt
    assert "00:00:00,000 -->" not in srt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_export.py -k source_timeline -v`
Expected: FAIL — SRT contains `00:00:00,000` (clip-local, not shifted)

- [ ] **Step 3: Edit `export_all`** — absolutize before exporting. After the docstring (line 220), before `export_dir = Path(export_dir)`:

```python
    from .clip import absolutize_meeting_times
    meeting = absolutize_meeting_times(meeting)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_export.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/export.py tests/test_export.py
git commit -m "feat(clip): export transcripts/subtitles in the full-source timeline"
```

---

## Task 8: Database migration

**Files:**
- Create: `supabase/migrations/0004_clip_window.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 0004_clip_window.sql
-- Clip window provenance: the contiguous source slice that was transcribed.
-- NULL on both = the whole recording was processed (the default; existing rows
-- stay NULL — no backfill). Published segment/section timestamps are stored in
-- the FULL source's timeline regardless. See docs/adr/0001-clip-window-ingest-time-only.md.

ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS clip_start_seconds double precision,
  ADD COLUMN IF NOT EXISTS clip_end_seconds   double precision;

COMMENT ON COLUMN meetings.meetings.clip_start_seconds IS
  'Start (seconds, source timeline) of the transcribed slice; NULL = whole recording.';
COMMENT ON COLUMN meetings.meetings.clip_end_seconds IS
  'End (seconds, source timeline) of the transcribed slice; NULL = whole recording.';
```

- [ ] **Step 2: Apply the migration** to Supabase (use the project's normal migration path — the same way `0001`–`0003` were applied; e.g. the Supabase SQL editor or `psql "$DATABASE_URL" -f supabase/migrations/0004_clip_window.sql`). Quote any meeting IDs in shell per project memory.

- [ ] **Step 3: Verify the columns exist**

Run: `psql "$DATABASE_URL" -c "\d meetings.meetings" | grep clip`
Expected: two `clip_start_seconds` / `clip_end_seconds` `double precision` rows.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0004_clip_window.sql
git commit -m "feat(clip): add clip_start_seconds/clip_end_seconds to meetings.meetings"
```

---

## Task 9: Serialize the columns in ev-accounts

**Files:**
- Modify: `ev-accounts/backend/src/lib/meetingsService.ts` (interfaces ~27 & ~136, `mapMeeting` ~212, `MEETING_COLS` ~313-321)

> This repo is separate (`/Users/chrisandrews/Documents/GitHub/ev-accounts`). The web app reads through this API, so until this ships the web sees `null` clip fields (graceful — the meeting renders as a normal full meeting). Build/deploy per the repo's normal process after editing.

- [ ] **Step 1: Add to the `Meeting` interface** (line ~27, after `playbackKind: string | null;`):

```typescript
  clipStartSeconds: number | null;
  clipEndSeconds: number | null;
```

- [ ] **Step 2: Add to the `MeetingRow` interface** (line ~136, after `playback_kind: string | null;`):

```typescript
  clip_start_seconds: string | null;
  clip_end_seconds: string | null;
```

(Numeric/double columns arrive from `pg` as strings.)

- [ ] **Step 3: Map them in `mapMeeting`** (line ~212, after `playbackKind: row.playback_kind,`):

```typescript
    clipStartSeconds: row.clip_start_seconds !== null ? Number(row.clip_start_seconds) : null,
    clipEndSeconds: row.clip_end_seconds !== null ? Number(row.clip_end_seconds) : null,
```

- [ ] **Step 4: Add to `MEETING_COLS`** (the SELECT column list, ~line 313-321) — add `clip_start_seconds, clip_end_seconds,` alongside `source_url, playback_kind, slug`:

```
  source_url, playback_kind, clip_start_seconds, clip_end_seconds, slug, summary, processing_metadata
```

- [ ] **Step 5: Typecheck / build**

Run (in `ev-accounts/backend`): `npm run build` (or the repo's typecheck script)
Expected: no TypeScript errors.

- [ ] **Step 6: Commit** (in the ev-accounts repo)

```bash
git add backend/src/lib/meetingsService.ts
git commit -m "feat(meetings): serialize clip window on the meeting endpoint"
```

---

## Task 10: Web types + query mapping

**Files:**
- Modify: `web/lib/types.ts` (the `Meeting` type, ~line 21-28)
- Modify: `web/lib/queries.ts` (`mapMeeting`, ~line 22-53)

> Read `node_modules/next/dist/docs/` before touching web code per `web/AGENTS.md`. These edits are plain TS type + object-literal changes (no Next APIs), so no framework reading is required for this task.

- [ ] **Step 1: Add to the `Meeting` type** in `web/lib/types.ts` (after `duration_seconds: number | null;`):

```typescript
  clip_start_seconds: number | null;
  clip_end_seconds: number | null;
```

- [ ] **Step 2: Map them in `mapMeeting`** in `web/lib/queries.ts` (after `duration_seconds: m.durationSeconds ?? null,` at line 36):

```typescript
    clip_start_seconds: m.clipStartSeconds ?? null,
    clip_end_seconds: m.clipEndSeconds ?? null,
```

- [ ] **Step 3: Typecheck**

Run (in `web/`): `npm run build` (or `npx tsc --noEmit` if the project exposes it)
Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add web/lib/types.ts web/lib/queries.ts
git commit -m "feat(clip): carry clip window through the web meeting query"
```

---

## Task 11: Auto-start at the clip + provenance note

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx` (deep-link effect ~88-105; render region ~203-217)

- [ ] **Step 1: Default the initial seek to the clip start.** Replace the deep-link effect body (lines 88-105) so that, when there is no explicit `?t=`, it falls back to `clip_start_seconds`:

```tsx
  // Deep links: ?t=SECONDS seeks the player; #seg-N scrolls without a player.
  // With no ?t=, a clipped meeting auto-starts at the interview (clip_start).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = Number(params.get("t"));
    const explicit = Number.isFinite(t) && t > 0;
    const initial = explicit ? t : (meeting.clip_start_seconds ?? 0);
    if (initial > 0) {
      if (adapterRef.current) adapterRef.current.seekTo(initial);
      else pendingSeek.current = initial;
      const idx = segmentIndexAt(starts, initial);
      document
        .getElementById(`seg-${segments[idx]?.segment_id}`)
        ?.scrollIntoView({ block: "center" });
    } else if (window.location.hash.startsWith("#seg-")) {
      document
        .querySelector(window.location.hash)
        ?.scrollIntoView({ block: "center" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

- [ ] **Step 2: Add a `formatTime`-based provenance note.** `formatTime` is already imported/used in this file (e.g. line 260). Insert this block inside `.mediaPane`, immediately after the player block closes (after line 217, before `<div className="searchBar">`):

```tsx
        {meeting.clip_start_seconds != null && (
          <p className="clipNote">
            <span>
              Transcript covers the interview
              {meeting.clip_end_seconds != null
                ? ` — ${formatTime(meeting.clip_start_seconds)}–${formatTime(meeting.clip_end_seconds)}`
                : ""}
              {meeting.source_title ? ` of “${meeting.source_title}”` : " of a longer recording"}.
            </span>{" "}
            <span
              className="clipNoteInfo"
              title="We transcribe and summarize only the relevant interview; the player plays the full original recording so you can see the surrounding context."
              aria-label="Why only part is transcribed"
            >
              ⓘ
            </span>
            {meeting.source_url && (
              <>
                {" "}
                <a href={meeting.source_url} target="_blank" rel="noreferrer">
                  Watch the full recording ↗
                </a>
              </>
            )}
          </p>
        )}
```

- [ ] **Step 3: Add minimal styling** in `web/app/globals.css` (append):

```css
.clipNote {
  font-size: 0.85rem;
  color: var(--muted, #555);
  margin: 0.5rem 0;
  line-height: 1.4;
}
.clipNoteInfo {
  cursor: help;
  border-bottom: 1px dotted currentColor;
}
```

(If the project uses CSS variables for muted text, reuse the existing one instead of the `#555` fallback — grep `globals.css` for an existing `--muted`/secondary-text token.)

- [ ] **Step 4: Verify in the browser** using the preview workflow.
  - `preview_start`, then open a clipped meeting (process one locally with `--clip`, publish, or temporarily set `clip_start_seconds` on a test meeting in the DB).
  - Confirm: the player auto-jumps to the clip start; the note renders with the range, the ⓘ tooltip, and the "Watch the full recording" link; a `?t=` deep link still overrides the auto-start.
  - `preview_screenshot` to capture the note for the PR.

- [ ] **Step 5: Commit**

```bash
git add web/app/meetings/[meetingId]/MeetingView.tsx web/app/globals.css
git commit -m "feat(clip): auto-start at the interview and show a provenance note"
```

---

## Task 12 (optional): YouTube `start` playerVar (flicker polish)

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx` (YouTube branch, line 171-172) and `web/app/meetings/[meetingId]/players/YouTubePlayer.tsx`

The Task 11 auto-start works for all playback kinds via `seekTo`, but YouTube briefly loads at 0:00 before jumping. Passing the `start` playerVar avoids that flicker. Only do this if the flicker is noticeable.

- [ ] **Step 1: Thread `start` into `YouTubePlayer`.** Add an optional prop:

```tsx
export default function YouTubePlayer({
  videoId,
  start,
  onAdapter,
}: {
  videoId: string;
  start?: number;
  onAdapter: (adapter: PlayerAdapter) => void;
}) {
```

In `playerVars` (line 69):

```tsx
        playerVars: { playsinline: 1, rel: 0, ...(start ? { start: Math.floor(start) } : {}) },
```

Add `start` to the effect dependency array (line 87): `}, [videoId, start, onAdapter]);`

- [ ] **Step 2: Pass it from `MeetingView`** (line 172):

```tsx
      <YouTubePlayer
        videoId={meeting.playback_url}
        start={meeting.clip_start_seconds ?? undefined}
        onAdapter={onAdapter}
      />
```

- [ ] **Step 3: Verify + commit**

Verify the YouTube player opens already at the clip start (no 0:00 flash). Then:

```bash
git add web/app/meetings/[meetingId]/MeetingView.tsx web/app/meetings/[meetingId]/players/YouTubePlayer.tsx
git commit -m "feat(clip): start YouTube embeds at the clip start (no flicker)"
```

---

## End-to-end verification

- [ ] Process a real clipped meeting:

```bash
.venv/bin/python run_local.py --input "https://www.youtube.com/watch?v=..." \
  --city "X" --date 2026-06-28 --event-kind news_clip \
  --clip 23:00 48:00 --meeting-id 2026-06-28-test-interview
```

- [ ] Confirm `audio.wav` is ~25 minutes (the clip length, not the full episode).
- [ ] Confirm `pipeline_state.json` has `clip_start_seconds: 1380.0`, `clip_end_seconds: 2880.0`.
- [ ] Publish, then check the DB: `meetings.meetings` row has the clip columns set and `meetings.segments.start_time` values are ≥ 1380 (source-absolute).
- [ ] On the site: the player auto-starts at 23:00, the provenance note shows "23:00–48:00", a transcript timestamp deep-link seeks to the correct moment in the full episode, and "Watch the full recording" opens the original.
- [ ] Resume safety: re-run with `--resume 2026-06-28-test-interview` (no `--clip`) → publishes with the correct offset. Re-run with a *different* `--clip` → hard error.
- [ ] Full suite green: `.venv/bin/python -m pytest -q`

---

## Self-Review (completed during authoring)

- **Spec coverage:** Q1 (absolute timeline) → Tasks 1,6,7. Q2 (persist provenance) → Tasks 2,3,8,9,10. Q3 (clip-local internal, offset at boundaries) → Tasks 1,6,7. Q4 (accurate-seek cut) → Task 4. Q5 (single contiguous) → enforced by scalar columns + `END>START` check (Task 5). Q6 (CLI + resume) → Task 5. Q7 (orthogonal to event kind) → no coupling added; `--clip` independent of `--event-kind`. Q8 (auto-start, no cap, deep-link override) → Tasks 11,12. Q9 (detail-only note + framing) → Task 11 (the summary framing clause is a prompt-side follow-up, see note). Q10 (two nullable columns, four-repo thread) → Tasks 8,9,10.
- **Deferred (out of scope, by decision):** `yt-dlp --download-sections` bandwidth optimization; the one-clause summary-prompt framing (Q9) — a `src/summarize.py` prompt tweak that can ride in a follow-up since it has no schema/coordinate impact. Note this in the PR so it isn't forgotten.
- **Type consistency:** `clip_start_seconds`/`clip_end_seconds` (Python/SQL/web) ↔ `clipStartSeconds`/`clipEndSeconds` (ev-accounts API) used consistently. `absolutize_meeting_times` and `parse_clip_time` signatures match call sites in Tasks 5/6/7.
```

# Repair Existing Transcript Implementation Plan

> **Status: Completed on 2026-06-13**
>
> This is a historical pre-implementation execution plan, not current
> instructions. Review-driven implementation evolved beyond draft snippets,
> especially transactional rollback, dynamic transaction manifests, interrupt
> rollback, and complete CLI conflict provenance.
>
> Authoritative behavior is in `src/repair.py`, `run_local.py`,
> `tests/test_repair_transcript.py`, `tests/test_repair_dispatch.py`, and
> `docs/pipeline.md`.
>
> **Do not execute or copy draft snippets from this plan.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `run_local.py --repair-transcript <MEETING_ID>` to rebuild one caption-backed meeting's transcript and exports while preserving reviewed identities, metadata, summaries, and pipeline state.

**Architecture:** Put repair orchestration in a focused `src/repair.py` module. It loads original diarization plus captions, builds repaired raw and named meeting data entirely in a staging directory, backs up live artifacts, and installs each staged file with `os.replace`; `run_local.py` only validates standalone CLI usage and reports the result.

**Tech Stack:** Python 3.11+, dataclasses, `pathlib`, `json`, `shutil`, `tempfile`, existing transcript/model/export helpers, pytest.

---

## File Structure

- Create `src/repair.py`: validation, repair data flow, staging, backups, and atomic installation.
- Create `tests/test_repair_transcript.py`: end-to-end repair behavior and failure-path coverage.
- Create `tests/test_repair_dispatch.py`: CLI dispatch and standalone-option validation.
- Modify `run_local.py`: add `_repair_transcript_standalone`, parser option, validation, and dispatch.
- Modify `docs/pipeline.md`: document the new focused repair command and its caption-only limitation.
- Use the existing uncommitted changes in `src/transcribe.py`, `src/vtt_align.py`, `tests/test_transcribe.py`, and `tests/test_vtt_align.py` as the shared overlap/deduplication prerequisite.
- Do not stage or modify the user's unrelated `.gitignore` change.

### Task 1: Stabilize Shared Transcript Normalization

**Files:**
- Modify: `src/transcribe.py:17-33`
- Modify: `src/vtt_align.py:15-198`
- Test: `tests/test_transcribe.py`
- Test: `tests/test_vtt_align.py`

- [ ] **Step 1: Review the existing regression tests already in the working tree**

Confirm these five concrete tests exist and contain assertions against the
expected segment boundaries or transcript text:

- `test_remove_segment_overlaps_trims_the_later_speaker`
- `test_remove_segment_overlaps_collapses_fully_covered_segment`
- `test_parse_vtt_collapses_expanding_lines_within_a_cue`
- `test_rolling_vtt_captions_flow_once_across_speakers`
- `test_repeated_text_in_separate_cues_is_preserved`

- [ ] **Step 2: Run the focused tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_transcribe.py tests/test_vtt_align.py
```

Expected: all focused tests pass.

- [ ] **Step 3: Confirm future Stage 3 runs use the shared normalization**

Verify `run_local.py` imports and calls `remove_segment_overlaps(segments)` before both VTT and Modal transcription, while completed checkpoint loads remain unchanged:

```python
if state.is_complete(PipelineStage.TRANSCRIBED):
    segments = load_raw_transcript(transcript_path)
else:
    remove_segment_overlaps(segments)
```

Verify local Whisper also protects direct callers:

```python
def transcribe_segments(
    model,
    wav_path: str | Path,
    segments: list[Segment],
    checkpoint_callback: Optional[Callable[[int, int], None]] = None,
    resume_from: int = 0,
) -> list[Segment]:
    remove_segment_overlaps(segments)
    samples, sr = load_wav(wav_path)
```

- [ ] **Step 4: Run the full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes.

- [ ] **Step 5: Verify secrets and stage only the shared transcript fix**

Run:

```bash
git check-ignore -v .env .env.local .env.local.bak
git add run_local.py src/transcribe.py src/vtt_align.py tests/test_transcribe.py tests/test_vtt_align.py
git diff --cached --check
git diff --cached --name-only
```

Expected: `.env`, `.env.local`, and `.env.local.bak` are ignored; the staged file list contains only the five transcript-fix files above.

- [ ] **Step 6: Commit**

```bash
git commit -m "fix: remove transcript overlap and rolling caption repeats"
```

### Task 2: Build the Repair Service

**Files:**
- Create: `src/repair.py`
- Create: `tests/test_repair_transcript.py`

- [ ] **Step 1: Add the end-to-end failing repair test and fixture helpers**

Create `tests/test_repair_transcript.py` with:

```python
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.models import (
    Meeting,
    MeetingSummary,
    ProcessingMetadata,
    Segment,
    SpeakerMapping,
)
from src.repair import RepairError, repair_transcript


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _meeting_dir(tmp_path):
    meeting_dir = tmp_path / "meeting-1"
    meeting_dir.mkdir()
    _write_json(
        meeting_dir / "diarization.json",
        [
            Segment(0, 10.0, 18.0, "SPEAKER_00").to_dict(),
            Segment(1, 17.0, 21.0, "SPEAKER_01").to_dict(),
        ],
    )
    (meeting_dir / "captions.vtt").write_text(
        """WEBVTT

00:00:10.000 --> 00:00:14.000
only 13 homes are completed
only 13 homes are completed and ready

00:00:12.000 --> 00:00:16.000
only 13 homes are completed and ready for move-in

00:00:15.000 --> 00:00:18.000
for move-in Do you have a permit

00:00:17.000 --> 00:00:20.000
Do you have a permit No
""",
        encoding="utf-8",
    )
    meeting = Meeting(
        meeting_id="meeting-1",
        city="Los Angeles",
        date="2026-05-06",
        meeting_type="Mayoral Debate",
        audio_source="https://example.com/debate",
        duration_seconds=60.0,
        segments=[
            Segment(
                0, 10.0, 18.0, "SPEAKER_00",
                text="old duplicate text",
                speaker_name="Nithya Raman",
                confidence=0.99,
                id_method="manual",
            ),
            Segment(
                1, 17.0, 21.0, "SPEAKER_01",
                text="old carryover",
                speaker_name="Peter Branch",
                confidence=0.95,
                id_method="voice_profile",
            ),
        ],
        speakers={
            "SPEAKER_00": SpeakerMapping(
                speaker_label="SPEAKER_00",
                speaker_name="Nithya Raman",
                confidence=0.99,
                id_method="manual",
                politician_slug="nithya-raman",
                politician_id="pol-1",
            ),
            "SPEAKER_01": SpeakerMapping(
                speaker_label="SPEAKER_01",
                speaker_name="Peter Branch",
                confidence=0.95,
                id_method="voice_profile",
            ),
        },
        summary=MeetingSummary(executive_summary="Preserved summary."),
        processing_metadata=ProcessingMetadata(
            pipeline_version="1.0.0",
            diarization_model="pyannote/ai-precision-2",
            transcription_model="vtt_alignment",
        ),
    )
    _write_json(meeting_dir / "transcript_named.json", meeting.to_dict())
    _write_json(
        meeting_dir / "transcript_raw.json",
        [segment.to_dict() for segment in meeting.segments],
    )
    (meeting_dir / "pipeline_state.json").write_text(
        '{"completed_stage": 7, "body_slug": "la-city-council"}',
        encoding="utf-8",
    )
    exports = meeting_dir / "exports"
    exports.mkdir()
    for name in ("transcript.md", "transcript.json", "subtitles.srt", "summary.md"):
        (exports / name).write_text(f"old {name}", encoding="utf-8")
    return meeting_dir


def test_repair_rebuilds_text_preserves_reviewed_data_and_state(tmp_path):
    meeting_dir = _meeting_dir(tmp_path)
    original_state = (meeting_dir / "pipeline_state.json").read_bytes()

    result = repair_transcript(
        meeting_dir,
        now=datetime(2026, 6, 13, 13, 45, 0),
    )

    raw = json.loads((meeting_dir / "transcript_raw.json").read_text())
    named = json.loads((meeting_dir / "transcript_named.json").read_text())

    assert result.meeting_id == "meeting-1"
    assert result.segment_count == 2
    assert result.backup_dir.name == "transcript-repair-20260613-134500"
    assert raw[0]["text"] == (
        "only 13 homes are completed and ready for move-in "
        "Do you have a permit"
    )
    assert raw[1]["text"] == "No"
    assert raw[1]["start_time"] == 18.0
    assert "speaker_name" not in raw[0]
    assert named["segments"][0]["speaker_name"] == "Nithya Raman"
    assert named["segments"][1]["speaker_name"] == "Peter Branch"
    assert named["speakers"]["SPEAKER_00"]["politician_id"] == "pol-1"
    assert named["summary"]["executive_summary"] == "Preserved summary."
    assert named["processing_metadata"]["diarization_model"] == "pyannote/ai-precision-2"
    assert (meeting_dir / "pipeline_state.json").read_bytes() == original_state
    assert "Nithya Raman" in (meeting_dir / "exports" / "transcript.md").read_text()
    assert "[Peter Branch] No" in (meeting_dir / "exports" / "subtitles.srt").read_text()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_transcript.py::test_repair_rebuilds_text_preserves_reviewed_data_and_state -v
```

Expected: FAIL during import because `src.repair` does not exist.

- [ ] **Step 3: Implement the repair module data model and validation**

Create `src/repair.py`:

```python
"""Repair caption-backed transcripts without rerunning the pipeline."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .export import export_all
from .identify import apply_mappings_to_segments, merge_adjacent_segments
from .models import Meeting, Segment
from .transcribe import remove_segment_overlaps
from .vtt_align import align_vtt_to_segments, parse_vtt


class RepairError(RuntimeError):
    """Raised when an existing transcript cannot be repaired safely."""


@dataclass(frozen=True)
class RepairResult:
    meeting_id: str
    segment_count: int
    backup_dir: Path
    exports: dict[str, Path]


_REQUIRED_FILES = (
    "pipeline_state.json",
    "diarization.json",
    "captions.vtt",
    "transcript_named.json",
)

_BACKUP_FILES = (
    Path("transcript_raw.json"),
    Path("transcript_named.json"),
    Path("exports/transcript.md"),
    Path("exports/transcript.json"),
    Path("exports/subtitles.srt"),
    Path("exports/summary.md"),
)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepairError(f"Could not read valid JSON from {path}: {exc}") from exc


def _validate_inputs(meeting_dir: Path) -> None:
    if not meeting_dir.is_dir():
        raise RepairError(f"Meeting directory not found: {meeting_dir}")
    missing = [name for name in _REQUIRED_FILES if not (meeting_dir / name).is_file()]
    if missing:
        raise RepairError(
            f"Cannot repair {meeting_dir.name}; missing: {', '.join(missing)}"
        )
    _load_json(meeting_dir / "pipeline_state.json")
    try:
        cues = parse_vtt(meeting_dir / "captions.vtt")
    except (OSError, UnicodeError) as exc:
        raise RepairError(
            f"Could not read captions for {meeting_dir.name}: {exc}"
        ) from exc
    if not cues:
        raise RepairError(
            f"Cannot repair {meeting_dir.name}; captions.vtt contains no usable cues"
        )
```

- [ ] **Step 4: Implement staging, backups, and atomic installation**

Continue `src/repair.py`:

```python
def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def _create_backup(meeting_dir: Path, now: datetime) -> Path:
    backup_dir = (
        meeting_dir
        / "backups"
        / f"transcript-repair-{now.strftime('%Y%m%d-%H%M%S')}"
    )
    created = False
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
        created = True
        for relative in _BACKUP_FILES:
            source = meeting_dir / relative
            if source.is_file():
                destination = backup_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    except OSError as exc:
        if created:
            shutil.rmtree(backup_dir, ignore_errors=True)
        raise RepairError(f"Could not create repair backup: {exc}") from exc
    return backup_dir


def _install(staging_dir: Path, meeting_dir: Path, relatives: list[Path]) -> None:
    for relative in relatives:
        source = staging_dir / relative
        if not source.is_file():
            continue
        destination = meeting_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
```

- [ ] **Step 5: Implement the repair orchestration**

Finish `src/repair.py`:

```python
def repair_transcript(
    meeting_dir: str | Path,
    *,
    now: datetime | None = None,
) -> RepairResult:
    meeting_dir = Path(meeting_dir)
    _validate_inputs(meeting_dir)

    diarization_data = _load_json(meeting_dir / "diarization.json")
    named_data = _load_json(meeting_dir / "transcript_named.json")
    try:
        raw_segments = [Segment.from_dict(item) for item in diarization_data]
        meeting = Meeting.from_dict(named_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise RepairError(f"Invalid meeting data for {meeting_dir.name}: {exc}") from exc

    remove_segment_overlaps(raw_segments)
    align_vtt_to_segments(meeting_dir / "captions.vtt", raw_segments)
    if not any(segment.text for segment in raw_segments):
        raise RepairError(
            f"Cannot repair {meeting_dir.name}; no caption text aligned to diarization"
        )

    named_segments = [
        Segment.from_dict(segment.to_dict())
        for segment in raw_segments
    ]
    apply_mappings_to_segments(named_segments, meeting.speakers)
    meeting.segments = merge_adjacent_segments(named_segments)

    install_files = [
        Path("transcript_raw.json"),
        Path("transcript_named.json"),
        Path("exports/transcript.md"),
        Path("exports/transcript.json"),
        Path("exports/subtitles.srt"),
    ]

    try:
        with tempfile.TemporaryDirectory(
            prefix=".transcript-repair-",
            dir=meeting_dir,
        ) as temp_name:
            staging_dir = Path(temp_name)
            _write_json(
                staging_dir / "transcript_raw.json",
                [segment.to_dict() for segment in raw_segments],
            )
            _write_json(
                staging_dir / "transcript_named.json",
                meeting.to_dict(),
            )
            staged_exports = export_all(meeting, staging_dir / "exports")
            if "summary" in staged_exports:
                install_files.append(Path("exports/summary.md"))

            backup_dir = _create_backup(meeting_dir, now or datetime.now())
            _install(staging_dir, meeting_dir, install_files)
    except RepairError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise RepairError(f"Transcript repair failed: {exc}") from exc

    exports = {
        key: meeting_dir / "exports" / path.name
        for key, path in staged_exports.items()
    }
    return RepairResult(
        meeting_id=meeting.meeting_id,
        segment_count=len(meeting.segments),
        backup_dir=backup_dir,
        exports=exports,
    )
```

- [ ] **Step 6: Run the end-to-end test**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_transcript.py::test_repair_rebuilds_text_preserves_reviewed_data_and_state -v
```

Expected: PASS.

- [ ] **Step 7: Add backup-content assertions**

Extend the end-to-end test:

```python
backup = result.backup_dir
assert (backup / "transcript_raw.json").is_file()
assert (backup / "transcript_named.json").is_file()
assert (backup / "exports" / "transcript.md").read_text() == "old transcript.md"
assert (backup / "exports" / "subtitles.srt").read_text() == "old subtitles.srt"
```

- [ ] **Step 8: Run the test again**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_transcript.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/repair.py tests/test_repair_transcript.py
git diff --cached --check
git commit -m "feat: repair one caption-backed transcript"
```

### Task 3: Cover Validation and Pre-Write Failure Safety

**Files:**
- Modify: `tests/test_repair_transcript.py`
- Modify: `src/repair.py`

- [ ] **Step 1: Add parameterized missing-input tests**

Add:

```python
@pytest.mark.parametrize(
    "missing_name",
    [
        "pipeline_state.json",
        "diarization.json",
        "captions.vtt",
        "transcript_named.json",
    ],
)
def test_repair_rejects_missing_required_input(tmp_path, missing_name):
    meeting_dir = _meeting_dir(tmp_path)
    (meeting_dir / missing_name).unlink()

    with pytest.raises(RepairError, match=missing_name):
        repair_transcript(meeting_dir)
```

- [ ] **Step 2: Add invalid and empty-caption tests**

Add:

```python
def test_repair_rejects_invalid_named_json_before_backup(tmp_path):
    meeting_dir = _meeting_dir(tmp_path)
    original_raw = (meeting_dir / "transcript_raw.json").read_bytes()
    (meeting_dir / "transcript_named.json").write_text("{", encoding="utf-8")

    with pytest.raises(RepairError, match="valid JSON"):
        repair_transcript(meeting_dir)

    assert (meeting_dir / "transcript_raw.json").read_bytes() == original_raw
    assert not (meeting_dir / "backups").exists()


def test_repair_rejects_caption_file_without_usable_cues(tmp_path):
    meeting_dir = _meeting_dir(tmp_path)
    (meeting_dir / "captions.vtt").write_text("WEBVTT\n", encoding="utf-8")

    with pytest.raises(RepairError, match="no usable cues"):
        repair_transcript(meeting_dir)

    assert not (meeting_dir / "backups").exists()
```

- [ ] **Step 3: Add serialization failure coverage**

Add:

```python
def test_serialization_failure_changes_no_live_artifacts(monkeypatch, tmp_path):
    meeting_dir = _meeting_dir(tmp_path)
    tracked = {
        path: path.read_bytes()
        for path in [
            meeting_dir / "transcript_raw.json",
            meeting_dir / "transcript_named.json",
            meeting_dir / "exports" / "transcript.md",
            meeting_dir / "exports" / "transcript.json",
            meeting_dir / "exports" / "subtitles.srt",
            meeting_dir / "exports" / "summary.md",
        ]
    }

    def fail_export(*args, **kwargs):
        raise TypeError("simulated serialization failure")

    monkeypatch.setattr("src.repair.export_all", fail_export)

    with pytest.raises(RepairError, match="simulated serialization failure"):
        repair_transcript(meeting_dir)

    assert {path: path.read_bytes() for path in tracked} == tracked
    assert not (meeting_dir / "backups").exists()
```

- [ ] **Step 4: Add backup failure coverage**

Add:

```python
def test_backup_failure_changes_no_live_artifacts(monkeypatch, tmp_path):
    meeting_dir = _meeting_dir(tmp_path)
    original_named = (meeting_dir / "transcript_named.json").read_bytes()

    def fail_copy(*args, **kwargs):
        raise OSError("simulated backup failure")

    monkeypatch.setattr("src.repair.shutil.copy2", fail_copy)

    with pytest.raises(RepairError, match="Could not create repair backup"):
        repair_transcript(meeting_dir)

    assert (meeting_dir / "transcript_named.json").read_bytes() == original_named
```

- [ ] **Step 5: Run the failure-path tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_transcript.py -v
```

Expected: all repair tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/repair.py tests/test_repair_transcript.py
git diff --cached --check
git commit -m "test: cover transcript repair failure safety"
```

### Task 4: Wire the Standalone CLI Command

**Files:**
- Modify: `run_local.py:1491-1515`
- Modify: `run_local.py:2361-2412`
- Modify: `run_local.py:2414-2526`
- Create: `tests/test_repair_dispatch.py`

- [ ] **Step 1: Add the failing dispatch test**

Create `tests/test_repair_dispatch.py`:

```python
from __future__ import annotations

import sys

import pytest

import run_local


def test_repair_transcript_dispatches_and_exits(monkeypatch):
    called = {}
    monkeypatch.setattr(
        run_local,
        "_repair_transcript_standalone",
        lambda meeting_id: called.setdefault("meeting_id", meeting_id),
    )
    monkeypatch.setattr(
        run_local,
        "run_pipeline",
        lambda args: pytest.fail("pipeline must not run"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_local.py", "--repair-transcript", "meeting-1"],
    )

    run_local.main()

    assert called == {"meeting_id": "meeting-1"}
```

- [ ] **Step 2: Run the dispatch test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_dispatch.py::test_repair_transcript_dispatches_and_exits -v
```

Expected: FAIL because `_repair_transcript_standalone` and the parser option do not exist.

- [ ] **Step 3: Add the standalone handler**

Add near `_publish_meeting_standalone` in `run_local.py`:

```python
def _repair_transcript_standalone(meeting_id: str) -> None:
    """Repair one caption-backed meeting without rerunning pipeline stages."""
    from src import config
    from src.repair import RepairError, repair_transcript

    meeting_dir = config.MEETINGS_DIR / meeting_id
    try:
        result = repair_transcript(meeting_dir)
    except RepairError as exc:
        print(f"Transcript repair failed: {exc}")
        sys.exit(1)

    print(f"Repaired transcript: {result.meeting_id}")
    print(f"  Segments: {result.segment_count}")
    print(f"  Backup:   {result.backup_dir}")
    print("  Exports:")
    for name, path in result.exports.items():
        print(f"    {name}: {path}")
```

- [ ] **Step 4: Add the parser option and dispatch**

Add under utility arguments:

```python
parser.add_argument(
    "--repair-transcript",
    metavar="MEETING_ID",
    help=(
        "Rebuild one processed caption-backed transcript and exports "
        "without rerunning diarization or speaker identification"
    ),
)
```

Dispatch before `--fix-transcripts`:

```python
if args.repair_transcript:
    _repair_transcript_standalone(args.repair_transcript)
    return
```

- [ ] **Step 5: Run the dispatch test**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_dispatch.py::test_repair_transcript_dispatches_and_exits -v
```

Expected: PASS.

- [ ] **Step 6: Add standalone conflict tests**

Append:

```python
@pytest.mark.parametrize(
    "extra_args",
    [
        ["--input", "meeting.mp4"],
        ["--resume", "meeting-1"],
        ["--redo", "transcribe", "--resume", "meeting-1"],
        ["--batch", "meetings.txt"],
        ["--review", "meeting-1"],
    ],
)
def test_repair_transcript_rejects_pipeline_or_review_options(
    monkeypatch,
    extra_args,
):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_local.py", "--repair-transcript", "meeting-1", *extra_args],
    )

    with pytest.raises(SystemExit) as exc_info:
        run_local.main()

    assert exc_info.value.code == 2
```

- [ ] **Step 7: Implement explicit conflict validation**

Immediately after `args = parser.parse_args()` add:

```python
if args.repair_transcript:
    repair_conflicts = {
        "--input": args.input,
        "--browse-catstv": args.browse_catstv,
        "--resume": args.resume,
        "--redo": args.redo,
        "--batch": args.batch,
        "--review": args.review,
        "--review-meeting": args.review_meeting,
        "--identify-speakers": args.identify_speakers,
    }
    used = [name for name, value in repair_conflicts.items() if value]
    if used:
        parser.error(
            "--repair-transcript is standalone and cannot be combined with "
            + ", ".join(used)
        )
```

- [ ] **Step 8: Run CLI tests and help smoke test**

Run:

```bash
.venv/bin/python -m pytest tests/test_repair_dispatch.py -v
.venv/bin/python run_local.py --help
```

Expected: all dispatch tests PASS; help includes `--repair-transcript MEETING_ID`.

- [ ] **Step 9: Commit**

```bash
git add run_local.py tests/test_repair_dispatch.py
git diff --cached --check
git commit -m "feat: add transcript repair CLI"
```

### Task 5: Document and Verify the Complete Workflow

**Files:**
- Modify: `docs/pipeline.md:145-159`

- [ ] **Step 1: Add user documentation**

Add after “Re-running a past meeting”:

```markdown
### Repairing one caption-backed transcript

If a processed meeting has `captions.vtt`, rebuild only its transcript text and
exports while preserving reviewed speaker identities, links, metadata, summary,
and checkpoint state:

```bash
.venv/bin/python run_local.py --repair-transcript <MEETING_ID>
```

The command creates a timestamped backup under
`<meeting>/backups/transcript-repair-YYYYMMDD-HHMMSS/`. Meetings without saved
captions must use `--resume <MEETING_ID> --redo transcribe`, which reruns
transcription and downstream stages.
```

- [ ] **Step 2: Run focused and full verification**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_transcribe.py \
  tests/test_vtt_align.py \
  tests/test_repair_transcript.py \
  tests/test_repair_dispatch.py
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile \
  run_local.py \
  src/transcribe.py \
  src/vtt_align.py \
  src/repair.py
git diff --check
```

Expected: focused tests PASS, full suite PASS, compilation exits 0, and `git diff --check` prints nothing.

- [ ] **Step 3: Perform a dry inspection against the target meeting**

Run read-only checks:

```bash
MEETING="$HOME/CouncilScribe/meetings/2026-05-06-la-mayoral-debate-(nbcla)"
test -f "$MEETING/pipeline_state.json"
test -f "$MEETING/diarization.json"
test -f "$MEETING/captions.vtt"
test -f "$MEETING/transcript_named.json"
```

Expected: all commands exit 0. Do not run the repair against real meeting data until the user explicitly requests it.

- [ ] **Step 4: Verify repository safety before the final commit**

Run:

```bash
git check-ignore -v .env .env.local .env.local.bak
git status --short
git diff -- docs/pipeline.md
```

Expected: `.env` files remain ignored; `.gitignore` remains unstaged and untouched by this feature.

- [ ] **Step 5: Commit**

```bash
git add docs/pipeline.md
git diff --cached --check
git commit -m "docs: explain focused transcript repair"
```

- [ ] **Step 6: Final verification after all commits**

Run:

```bash
.venv/bin/python -m pytest -q
git status --short
git log -5 --oneline
```

Expected: full suite PASS; only pre-existing unrelated user changes, if any, remain unstaged.

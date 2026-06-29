# Continuous Transcription + Word→Speaker Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-diarization-turn audio slicing in transcription with whole-audio transcription followed by timestamp-based word→speaker assignment, eliminating the ~1s word-timestamp drift that misattributes words near speaker boundaries.

**Architecture:** Transcribe the full meeting audio in one pass (accurate, drift-free word timestamps — proven over a 100s span in spike), then assign each word to the diarization turn whose time span contains it, reusing the assignment strategy already proven in `vtt_align.py` (midpoint → max-overlap → gap-snap). Add a short-turn guard so a brief backchannel turn cannot steal a word from a surrounding dominant speaker, and (optionally) recover genuine faint backchannels via targeted slice transcription of orphaned turns.

**Tech Stack:** Python 3.x, faster-whisper >=1.0.0, numpy, pytest. Pipeline stages tracked via `src/checkpoint.py` `PipelineStage`.

---

## Background & Evidence

The defect (confirmed on `2026-04-01-ca-courier-stevehiltoninterview`, ~17:09):

- Current `transcribe_segments()` (`src/transcribe.py:55`) slices audio per diarization turn, runs Whisper on each slice, and rebases word times as `seg.start_time + w.start` (`src/transcribe.py:104`).
- This produces word timestamps that **lag the true audio by up to ~1.4s and drift forward**, so words spoken near a turn boundary land in the *next* turn's slice / the wrong speaker.
- Ground truth (continuous re-transcription): *"...described as **abortion tourism** where our taxpayer money is being spent on **ads in other states**, saying come to California"* — all Steve Hilton. The stored transcript wrongly assigned "abortion" and "other" to Hailey Gomez.

Spike result (`scratchpad/spike_assign.py`, 100s window): continuous transcription has **no drift accumulation** ("abortion" @1028.18 identical to the 9s diagnostic), and midpoint assignment puts `abortion → Steve`, `tourism → Steve`, `other → Steve`. One residual: Steve's word "saying" was tied at a boundary and leaked to Hailey's 0.37s turn — the short-turn guard (Task 4) addresses this. Hailey's faint "yeah" is not surfaced by whole-audio Whisper — Task 6 (optional) recovers it.

## File Structure

- **Create `src/word_assign.py`** — shared word→segment assignment. Owns `_overlap`, `_segment_for_gap_word`, `assign_words_to_segments`, and the new short-turn guard. Single responsibility: given a chronological `list[Word]` and diarized `list[Segment]`, populate `seg.words`/`seg.text` correctly.
- **Modify `src/vtt_align.py`** — delete its private copies of `_overlap`/`_segment_for_gap_word` and the inline assignment loop; call `word_assign.assign_words_to_segments`. Keeps existing VTT tests green (DRY — one assignment implementation).
- **Modify `src/transcribe.py`** — add `transcribe_full_audio(model, wav_path) -> list[Word]` and `transcribe_and_assign(model, wav_path, segments, ...) -> list[Segment]`. Keep `remove_segment_overlaps` and `load_whisper_model` unchanged. Leave `transcribe_segments` in place until run_local is switched (removed in Task 5).
- **Modify `run_local.py`** — local Whisper branch (~`:1228`) calls `transcribe_and_assign` instead of `transcribe_segments`.
- **Modify `bench/modal_app.py`** — GPU path (`pipeline_transcribe`, ~`:1471`) mirrors the same whole-audio + assignment flow.
- **Create `tests/test_word_assign.py`** — unit tests for assignment + short-turn guard, using the real Hilton timings as fixtures.
- **Modify `tests/test_transcribe.py`** — add tests for `transcribe_full_audio` (fake model) and `transcribe_and_assign`.

---

### Task 1: Extract shared word→segment assignment into `src/word_assign.py`

Move the assignment logic out of `vtt_align.py` verbatim (no behavior change yet) so both VTT and Whisper paths share it.

**Files:**
- Create: `src/word_assign.py`
- Test: `tests/test_word_assign.py`
- Modify: `src/vtt_align.py` (replace inline logic with a call)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_word_assign.py
from __future__ import annotations

from src.models import Segment, Word
from src.word_assign import assign_words_to_segments


def _seg(seg_id, start, end, label):
    return Segment(segment_id=seg_id, start_time=start, end_time=end, speaker_label=label)


def test_assigns_word_by_midpoint_to_containing_segment():
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "B")]
    words = [Word("hello", 0.2, 0.8), Word("there", 2.2, 2.8)]

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[0].words] == ["hello"]
    assert [w.word for w in segs[1].words] == ["there"]
    assert segs[0].text == "hello"
    assert segs[1].text == "there"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_word_assign.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.word_assign'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/word_assign.py
"""Assign a chronological word stream to diarized segments by timestamp.

Shared by the VTT alignment path and the whole-audio Whisper path. The strategy
(proven in the former vtt_align implementation): assign each word to the segment
whose span contains the word midpoint; fall back to the segment of greatest
temporal overlap; finally snap a zero-overlap word that lands in an
inter-segment gap to the preceding turn (otherwise drop it).
"""

from __future__ import annotations

from .models import Segment, Word


def _overlap(seg_start: float, seg_end: float, w_start: float, w_end: float) -> float:
    """Overlap duration between a segment span and a word span."""
    return max(0.0, min(seg_end, w_end) - max(seg_start, w_start))


def _segment_for_gap_word(word: Word, segments: list[Segment]) -> Segment | None:
    """Snap a zero-overlap word in an inter-segment gap to the preceding turn.

    Returns the preceding turn when the word falls strictly between two turns
    (trailing word of that turn), else None (outside the diarized timeline).
    """
    preceding = None
    following = None
    for seg in segments:
        if seg.end_time <= word.start:
            if preceding is None or seg.end_time > preceding.end_time:
                preceding = seg
        if seg.start_time >= word.end:
            if following is None or seg.start_time < following.start_time:
                following = seg
    if preceding is not None and following is not None:
        return preceding
    return None


def assign_words_to_segments(
    words: list[Word], segments: list[Segment]
) -> list[Segment]:
    """Populate seg.words and seg.text for each diarized segment from `words`."""
    for seg in segments:
        seg.words = []
        seg.text = ""

    for word in words:
        midpoint = (word.start + word.end) / 2
        target = next(
            (s for s in segments if s.start_time <= midpoint < s.end_time),
            None,
        )
        if target is None:
            candidates = [
                (_overlap(s.start_time, s.end_time, word.start, word.end), s)
                for s in segments
            ]
            overlap_dur, target = max(candidates, key=lambda item: item[0])
            if overlap_dur <= 0:
                target = _segment_for_gap_word(word, segments)
                if target is None:
                    continue
        target.words.append(word)

    for seg in segments:
        seg.text = " ".join(w.word for w in seg.words)
    return segments
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_word_assign.py -v`
Expected: PASS

- [ ] **Step 5: Refactor `vtt_align.py` to use the shared function**

In `src/vtt_align.py`: delete the local `_overlap` and `_segment_for_gap_word` definitions and the inline assignment loop inside `align_vtt_to_segments` (the block that loops over words assigning by midpoint/overlap/gap and then rebuilds `seg.text`). Replace the body after the clip-offset rebasing with:

```python
    from .word_assign import assign_words_to_segments
    return assign_words_to_segments(words, diarized_segments)
```

Keep `align_vtt_to_segments`'s signature, the `parse_vtt`/`_deduplicated_words` calls, and the `clip_offset` rebasing exactly as they are.

- [ ] **Step 6: Run the full VTT + new suite to verify no regression**

Run: `.venv/bin/python -m pytest tests/test_vtt_align.py tests/test_word_assign.py -v`
Expected: PASS (all existing VTT tests still green — assignment behavior is unchanged)

- [ ] **Step 7: Commit**

```bash
git add src/word_assign.py tests/test_word_assign.py src/vtt_align.py
git commit -m "refactor: extract shared word->segment assignment into word_assign"
```

---

### Task 2: Pin the real-data assignment behavior with a regression test

Lock in the corrected attribution for the Hilton case using the actual diarization turn timings and continuous-transcription word timings, so future changes can't regress it.

**Files:**
- Test: `tests/test_word_assign.py`

- [ ] **Step 1: Write the failing test (real Hilton timings)**

```python
# append to tests/test_word_assign.py

def test_hilton_clip_assigns_continuous_words_to_correct_speakers():
    # Diarization turns (from diarization.json, ~17:06-17:15).
    segs = [
        _seg(0, 1026.655, 1029.254, "SPEAKER_00"),  # Steve
        _seg(1, 1029.322, 1029.777, "SPEAKER_01"),  # Hailey (short)
        _seg(2, 1029.777, 1030.283, "SPEAKER_00"),  # Steve
        _seg(3, 1030.570, 1034.148, "SPEAKER_00"),  # Steve
        _seg(4, 1034.148, 1034.519, "SPEAKER_01"),  # Hailey (short)
        _seg(5, 1034.603, 1036.949, "SPEAKER_00"),  # Steve
    ]
    # Continuous-transcription word timings (from spike: drift-free, accurate).
    words = [
        Word("abortion", 1028.18, 1028.64),
        Word("tourism", 1028.64, 1029.20),
        Word("where", 1029.20, 1030.20),
        Word("other", 1033.44, 1033.70),
        Word("states", 1033.70, 1034.20),
    ]

    assign_words_to_segments(words, segs)

    def owner(token):
        for s in segs:
            if any(w.word == token for w in s.words):
                return s.speaker_label
        return None

    assert owner("abortion") == "SPEAKER_00"  # Steve's "abortion tourism"
    assert owner("tourism") == "SPEAKER_00"
    assert owner("other") == "SPEAKER_00"      # Steve's "ads in other states"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `.venv/bin/python -m pytest tests/test_word_assign.py::test_hilton_clip_assigns_continuous_words_to_correct_speakers -v`
Expected: PASS for `abortion`/`tourism` (midpoints fall in Steve turns). If `other` (midpoint 1033.57, inside Steve turn 3 `[1030.570, 1034.148]`) also passes, this test documents the baseline — proceed. If any assertion fails, that is the regression the midpoint rule must satisfy; do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_word_assign.py
git commit -m "test: pin Hilton-clip word attribution to correct speakers"
```

---

### Task 3: Short-turn guard — a brief turn cannot claim a word via overlap-fallback

Prevent the "saying → Hailey" leak: a word whose midpoint is NOT inside any turn must not be forced (via max-overlap) into a turn shorter than `SHORT_TURN_SECONDS`; it snaps to the dominant neighbor instead.

**Files:**
- Modify: `src/word_assign.py`
- Test: `tests/test_word_assign.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_word_assign.py

def test_short_turn_does_not_steal_boundary_word_from_dominant():
    # Steve speaks continuously; a 0.37s Hailey turn sits at a word boundary.
    segs = [
        _seg(0, 1030.570, 1034.148, "SPEAKER_00"),  # Steve (long)
        _seg(1, 1034.148, 1034.519, "SPEAKER_01"),  # Hailey (0.371s, short)
        _seg(2, 1034.603, 1036.949, "SPEAKER_00"),  # Steve (long)
    ]
    # "saying" spans the boundary; midpoint 1034.56 lies in the gap, not inside
    # the short Hailey turn. It must NOT be handed to Hailey.
    words = [Word("saying", 1034.20, 1034.92)]

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[1].words] == []        # Hailey gets nothing
    assert "saying" in [w.word for w in segs[0].words] or \
           "saying" in [w.word for w in segs[2].words]  # stays with Steve
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_word_assign.py::test_short_turn_does_not_steal_boundary_word_from_dominant -v`
Expected: FAIL — max-overlap currently hands "saying" to the short Hailey turn (overlap 0.319 vs 0.317).

- [ ] **Step 3: Implement the short-turn guard**

In `src/word_assign.py`, add the constant and a duration helper, and change the gap/overlap fallback so short turns are excluded from overlap-claiming and gap-snapping prefers the nearest *non-short* turn:

```python
SHORT_TURN_SECONDS = 0.8  # turns shorter than this cannot claim boundary words


def _duration(seg: Segment) -> float:
    return seg.end_time - seg.start_time
```

Replace the fallback block inside `assign_words_to_segments` (the `if target is None:` branch) with:

```python
        if target is None:
            # Only turns long enough to be a real utterance may claim a word
            # whose midpoint lies outside every turn. This stops a brief
            # backchannel turn from stealing a word from a surrounding speaker.
            claimable = [s for s in segments if _duration(s) >= SHORT_TURN_SECONDS]
            candidates = [
                (_overlap(s.start_time, s.end_time, word.start, word.end), s)
                for s in claimable
            ]
            overlap_dur, target = (
                max(candidates, key=lambda item: item[0])
                if candidates
                else (0.0, None)
            )
            if not overlap_dur or overlap_dur <= 0:
                target = _segment_for_gap_word(word, claimable)
                if target is None:
                    continue
```

Note: `_segment_for_gap_word` now receives only `claimable` (non-short) turns, so a gap word snaps to the preceding *substantial* turn, not a wedged backchannel.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_word_assign.py -v`
Expected: PASS (new test green; earlier midpoint tests still green — the guard only affects the no-midpoint fallback)

- [ ] **Step 5: Run the VTT suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_vtt_align.py -v`
Expected: PASS. If `test_align_vtt_retains_trailing_word_in_inter_segment_gap` fails because its fixture turns are < 0.8s, adjust the test fixture durations to be realistic (>= 0.8s) rather than weakening the guard, and note why in the commit.

- [ ] **Step 6: Commit**

```bash
git add src/word_assign.py tests/test_word_assign.py
git commit -m "feat: short-turn guard prevents backchannel turns stealing boundary words"
```

---

### Task 4: `transcribe_full_audio` — one Whisper pass over the whole file

**Files:**
- Modify: `src/transcribe.py`
- Test: `tests/test_transcribe.py`

- [ ] **Step 1: Write the failing test (fake model — no real Whisper)**

```python
# append to tests/test_transcribe.py
from src.transcribe import transcribe_full_audio
from src.models import Word


class _FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class _FakeSeg:
    def __init__(self, words):
        self.words = words
        self.text = " ".join(w.word for w in words)


class _FakeModel:
    def __init__(self, segs):
        self._segs = segs
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter(self._segs), {}


def test_transcribe_full_audio_returns_flat_chronological_words(monkeypatch, tmp_path):
    import src.transcribe as t
    # Stub audio loading so no real WAV is needed.
    monkeypatch.setattr(t, "load_wav", lambda p: (b"", 16000))
    model = _FakeModel([
        _FakeSeg([_FakeWord(" abortion", 1028.18, 1028.64),
                  _FakeWord(" tourism", 1028.64, 1029.20)]),
        _FakeSeg([_FakeWord(" where", 1029.20, 1030.20)]),
    ])

    words = transcribe_full_audio(model, tmp_path / "audio.wav")

    assert [w.word for w in words] == ["abortion", "tourism", "where"]
    assert words[0].start == 1028.18 and words[0].end == 1028.64
    # whole-audio call uses word_timestamps and is NOT sliced per segment
    assert model.calls[0]["word_timestamps"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py::test_transcribe_full_audio_returns_flat_chronological_words -v`
Expected: FAIL with `ImportError: cannot import name 'transcribe_full_audio'`

- [ ] **Step 3: Implement `transcribe_full_audio`**

Add to `src/transcribe.py` (after `load_whisper_model`):

```python
def transcribe_full_audio(model, wav_path: str | Path) -> list[Word]:
    """Transcribe the entire audio in one pass with word-level timestamps.

    Returns a flat, chronological list of Words whose start/end are already on
    the meeting's global timeline (the WAV is the meeting). No per-segment
    rebasing — that is the source of the drift this replaces.
    """
    samples, sr = load_wav(wav_path)
    result_segments, _ = model.transcribe(
        samples,
        word_timestamps=True,
        language="en",
    )
    words: list[Word] = []
    for rs in result_segments:
        for w in (rs.words or []):
            words.append(
                Word(word=w.word.strip(), start=round(w.start, 3), end=round(w.end, 3))
            )
    return words
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py::test_transcribe_full_audio_returns_flat_chronological_words -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/transcribe.py tests/test_transcribe.py
git commit -m "feat: transcribe_full_audio — single whole-file pass with word timestamps"
```

---

### Task 5: `transcribe_and_assign` — orchestrate whole-audio → assignment

**Files:**
- Modify: `src/transcribe.py`
- Test: `tests/test_transcribe.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_transcribe.py
from src.transcribe import transcribe_and_assign
from src.models import Segment


def test_transcribe_and_assign_attributes_words_to_diarized_turns(monkeypatch, tmp_path):
    import src.transcribe as t
    monkeypatch.setattr(t, "load_wav", lambda p: (b"", 16000))
    model = _FakeModel([
        _FakeSeg([_FakeWord(" abortion", 1028.18, 1028.64),
                  _FakeWord(" tourism", 1028.64, 1029.20),
                  _FakeWord(" where", 1029.20, 1030.20)]),
    ])
    segments = [
        Segment(0, 1026.655, 1029.254, "SPEAKER_00"),
        Segment(1, 1029.322, 1029.777, "SPEAKER_01"),  # short Hailey turn
        Segment(2, 1029.777, 1030.570, "SPEAKER_00"),
    ]

    result = transcribe_and_assign(model, tmp_path / "audio.wav", segments)

    steve_words = [w.word for s in result if s.speaker_label == "SPEAKER_00" for w in s.words]
    hailey_words = [w.word for s in result if s.speaker_label == "SPEAKER_01" for w in s.words]
    assert "abortion" in steve_words and "tourism" in steve_words
    assert hailey_words == []  # short turn captured no genuine word
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py::test_transcribe_and_assign_attributes_words_to_diarized_turns -v`
Expected: FAIL with `ImportError: cannot import name 'transcribe_and_assign'`

- [ ] **Step 3: Implement `transcribe_and_assign`**

Add to `src/transcribe.py`:

```python
def transcribe_and_assign(
    model,
    wav_path: str | Path,
    segments: list[Segment],
) -> list[Segment]:
    """Whole-audio transcription, then assign each word to its diarized turn.

    Replaces per-segment slicing (`transcribe_segments`). Segments are modified
    in place: seg.words and seg.text are populated from the global word stream.
    """
    from .word_assign import assign_words_to_segments

    remove_segment_overlaps(segments)
    words = transcribe_full_audio(model, wav_path)
    assign_words_to_segments(words, segments)
    return segments
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/transcribe.py tests/test_transcribe.py
git commit -m "feat: transcribe_and_assign — whole-audio transcription + word assignment"
```

---

### Task 6: Wire the local Whisper path in `run_local.py`

**Files:**
- Modify: `run_local.py` (local Whisper branch, ~`:1228-1260`)

- [ ] **Step 1: Update the import**

In `run_local.py`, the transcription import block (~`:1175`) currently imports `transcribe_segments`. Add `transcribe_and_assign`:

```python
from src.transcribe import (
    load_raw_transcript,
    load_whisper_model,
    remove_segment_overlaps,
    save_raw_transcript,
    transcribe_and_assign,
    transcribe_segments,
)
```

- [ ] **Step 2: Switch the local branch to the new function**

Replace the local-branch call (~`:1228`):

```python
    elif not state.is_complete(PipelineStage.TRANSCRIBED):
        whisper_model = load_whisper_model()
        segments = transcribe_and_assign(whisper_model, wav_path, segments)
        save_raw_transcript(segments, transcript_path)
        state.mark_complete(PipelineStage.TRANSCRIBED)
```

The previous `transcribe_segments` call (with `checkpoint_callback`/`resume_from`) is removed. Per-segment checkpointing no longer applies because transcription is a single pass; the whole `transcript_raw.json` is written atomically at the end. Leave the `resume_from`/`checkpoint_fn` setup for the other branches untouched.

- [ ] **Step 3: Verify the module imports and the pipeline help runs**

Run: `.venv/bin/python -c "import run_local"`
Expected: no error (import succeeds).

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat: local pipeline uses whole-audio transcription + word assignment"
```

---

### Task 7: End-to-end validation on the Hilton clip (real audio)

Prove the fix on real data before touching more meetings. This re-runs transcription on the known-bad clip and confirms attribution.

**Files:**
- Use existing meeting data at `~/CouncilScribe/meetings/2026-04-01-ca-courier-stevehiltoninterview`

- [ ] **Step 1: Back up the current transcript**

```bash
cp ~/CouncilScribe/meetings/2026-04-01-ca-courier-stevehiltoninterview/transcript_named.json \
   ~/CouncilScribe/meetings/2026-04-01-ca-courier-stevehiltoninterview/transcript_named.json.precontinuous.bak
```

- [ ] **Step 2: Rewind the meeting to re-run transcription**

The meeting is already DIARIZED; rewinding to re-run from TRANSCRIBED uses existing `diarization.json`/`embeddings.json`. Use the project's rewind mechanism (`src/checkpoint.py` `PipelineState.rewind_to` / the `--from` or `--force` flag in `run_local.py` — confirm the exact flag with `.venv/bin/python run_local.py --help`). Re-run stages 3→4 for this meeting id.

Run (adjust flag names to match `--help`):
`.venv/bin/python run_local.py --meeting 2026-04-01-ca-courier-stevehiltoninterview --from transcribed`
Expected: transcription completes in one whole-audio pass; identification re-runs.

- [ ] **Step 3: Verify "abortion" and "other" are now Steve's**

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
D = Path.home()/ "CouncilScribe/meetings/2026-04-01-ca-courier-stevehiltoninterview"
segs = json.load(open(D/"transcript_named.json"))["segments"]
def owner(tok):
    for s in segs:
        if any(w["word"].strip(".,").lower()==tok for w in s.get("words",[])):
            return s.get("speaker_name")
    return None
print("abortion ->", owner("abortion"))
print("other    ->", owner("other"))
PY
```
Expected: both print `Steve Hilton`.

- [ ] **Step 4: Spot-check the transcript reads coherently around 17:09**

Reuse the dump from `scratchpad/diag_align.py` style (print named segments 1026–1036). Expected: Steve's line reads "...described as abortion tourism where our taxpayer money is being spent on ads in other states...".

- [ ] **Step 5: Commit a short validation note (no data committed)**

```bash
git add docs/superpowers/plans/2026-06-29-continuous-transcription-word-assignment.md
git commit -m "docs: record Hilton-clip validation result for continuous transcription"
```

---

### Task 8: Long-audio + roll-call regression on a council meeting

Whole-audio transcription must work on a long meeting (faster-whisper internally windows >30s audio) and must NOT regress council roll-call attribution.

**Files:**
- Use an existing long meeting, e.g. `~/CouncilScribe/meetings/2026-02-04-council` (1264 segments).

- [ ] **Step 1: Back up and rewind one council meeting**

```bash
cp ~/CouncilScribe/meetings/2026-02-04-council/transcript_named.json \
   ~/CouncilScribe/meetings/2026-02-04-council/transcript_named.json.precontinuous.bak
```
Then re-run transcription→identification for `2026-02-04-council` as in Task 7 Step 2.

- [ ] **Step 2: Confirm it completes and measure wall-clock + peak memory**

Time the run. Expected: completes without OOM. Record the duration in the plan doc. If it OOMs or is unacceptably slow, add a follow-up task to chunk the audio into ~10-minute windows with small overlaps and concatenate the word streams (note: chunk boundaries must overlap and dedup to avoid reintroducing drift).

- [ ] **Step 3: Diff roll-call attribution before/after**

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
D = Path.home()/ "CouncilScribe/meetings/2026-02-04-council"
new = json.load(open(D/"transcript_named.json"))["segments"]
old = json.load(open(D/"transcript_named.json.precontinuous.bak"))["segments"]
# Count short one-word turns ("Here.", "Yes.") per speaker as a roll-call proxy
def votes(segs):
    out={}
    for s in segs:
        t=s.get("text","").strip().lower().strip(".")
        if t in {"here","present","yes","no","aye","nay","second"}:
            out[s.get("speaker_name")]=out.get(s.get("speaker_name"),0)+1
    return out
print("old votes:", votes(old))
print("new votes:", votes(new))
PY
```
Expected: roll-call/vote responses remain attributed to the members (counts comparable; short-turn guard keeps midpoint-contained backchannels with their speaker). Investigate any disappearance before proceeding.

- [ ] **Step 4: Decide rollout & document**

If both validations pass, record in the plan doc that existing meetings need stages 3→4 re-run to benefit (a `republish_all.sh`-style sweep). Note this is a reprocessing cost, not a code change.

```bash
git add docs/superpowers/plans/2026-06-29-continuous-transcription-word-assignment.md
git commit -m "docs: long-audio + roll-call validation results"
```

---

### Task 9 (OPTIONAL): Recover faint backchannels via targeted slice transcription

Whole-audio Whisper locks onto the dominant voice and drops faint backchannels (Hailey's "yeah"), leaving short turns empty. Optionally recover them by transcribing only the orphaned turn's slice — the one place the old per-slice method is still useful.

**Files:**
- Modify: `src/transcribe.py`
- Test: `tests/test_transcribe.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_transcribe.py
from src.transcribe import recover_orphan_turns


def test_recover_orphan_turns_fills_empty_short_turn(monkeypatch, tmp_path):
    import src.transcribe as t
    import numpy as np
    monkeypatch.setattr(t, "load_wav", lambda p: (np.zeros(16000 * 40), 16000))
    monkeypatch.setattr(t, "slice_audio", lambda samples, sr, a, b: np.zeros(int((b - a) * sr)))
    model = _FakeModel([_FakeSeg([_FakeWord(" yeah", 0.1, 0.4)])])
    segs = [Segment(1, 1029.322, 1029.777, "SPEAKER_01")]  # empty short turn
    segs[0].words = []

    recover_orphan_turns(model, tmp_path / "audio.wav", segs)

    assert [w.word for w in segs[0].words] == ["yeah"]
    assert segs[0].text == "yeah"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py::test_recover_orphan_turns_fills_empty_short_turn -v`
Expected: FAIL with `ImportError: cannot import name 'recover_orphan_turns'`

- [ ] **Step 3: Implement `recover_orphan_turns`**

```python
def recover_orphan_turns(
    model,
    wav_path: str | Path,
    segments: list[Segment],
    min_seconds: float = 0.1,
) -> list[Segment]:
    """Fill turns left wordless by whole-audio assignment via a targeted slice.

    Whole-audio Whisper drops faint backchannels. For each empty turn long
    enough to embed, transcribe just its slice and attach the result so a
    listener's "yeah" is not lost. Words are rebased to the turn's start.
    """
    samples, sr = load_wav(wav_path)
    for seg in segments:
        if seg.words:
            continue
        if seg.end_time - seg.start_time < min_seconds:
            continue
        audio_slice = slice_audio(samples, sr, seg.start_time, seg.end_time)
        result_segments, _ = model.transcribe(
            audio_slice, word_timestamps=True, language="en"
        )
        words: list[Word] = []
        for rs in result_segments:
            for w in (rs.words or []):
                words.append(
                    Word(word=w.word.strip(),
                         start=round(seg.start_time + w.start, 3),
                         end=round(seg.start_time + w.end, 3))
                )
        seg.words = words
        seg.text = " ".join(w.word for w in words)
    return segments
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_transcribe.py -v`
Expected: PASS

- [ ] **Step 5: Wire into `transcribe_and_assign`**

In `transcribe_and_assign`, after `assign_words_to_segments(words, segments)`, add:

```python
    recover_orphan_turns(model, wav_path, segments)
```

Re-run `tests/test_transcribe.py` — the `transcribe_and_assign` test still expects Hailey empty because its `_FakeModel` yields the same words again; update that fake to return no words for the orphan slice, or assert Hailey now contains only a genuine backchannel. Keep the assertion that `abortion`/`tourism` stay with Steve.

- [ ] **Step 6: Commit**

```bash
git add src/transcribe.py tests/test_transcribe.py
git commit -m "feat: recover faint backchannels in orphan turns via targeted slice"
```

---

### Task 10: Mirror the change in the Modal GPU path

**Files:**
- Modify: `bench/modal_app.py` (`pipeline_transcribe`, ~`:1471-1531`)

- [ ] **Step 1: Replace per-segment slicing with whole-audio + assignment**

In `bench/modal_app.py::pipeline_transcribe`, the loop currently slices per segment and rebases. Replace it with the same two-step flow: one `model.transcribe(full_audio, word_timestamps=True, language="en")` pass into a flat `list[Word]`, then `assign_words_to_segments(words, segments)` (import from `src.word_assign`), then optionally `recover_orphan_turns`. Keep the function's inputs/outputs (segment dicts in, segment dicts with words out) identical so `run_local.py`'s modal branch is unaffected.

- [ ] **Step 2: Verify parity against local on the Hilton clip**

If a Modal run is available, transcribe the Hilton clip via `--compute modal` and confirm `abortion`/`other` attribute to Steve, matching the local result. If Modal is not runnable in this environment, mark this task as verified-by-inspection and note that local and Modal now call identical `word_assign` logic.

- [ ] **Step 3: Commit**

```bash
git add bench/modal_app.py
git commit -m "feat: Modal GPU transcription uses whole-audio + shared word assignment"
```

---

## Self-Review

**Spec coverage:**
- Whole-audio transcription → Tasks 4, 6, 10. ✓
- Timestamp-based word→speaker assignment → Tasks 1, 2 (reuses vtt_align strategy). ✓
- Short-turn handling (the "saying" leak) → Task 3. ✓
- Faint-backchannel recovery (Hailey's "yeah") → Task 9 (optional). ✓
- Validate against the clip before pipeline-wide change → Task 7. ✓
- Long-audio feasibility + roll-call non-regression → Task 8. ✓
- Both transcription paths updated → Tasks 6 (local) + 10 (Modal). ✓
- Words preserved for `identify.py`/`clip.py` → maintained (words live on segments throughout). ✓

**Open risks (carry into execution):**
1. Long/multi-hour meetings: if Task 8 shows OOM or unacceptable runtime, add a chunked-with-overlap transcription task (chunk dedup must not reintroduce drift).
2. `condition_on_previous_text` (faster-whisper default True) can cause repetition loops on long audio; if Task 8 shows hallucinated repeats, add a task to set it False and re-validate the Hilton clip.
3. Existing meetings must re-run stages 3→4 to benefit — reprocessing cost, flagged in Task 8 Step 4.
4. `SHORT_TURN_SECONDS = 0.8` is a tunable; Task 8's roll-call diff is the calibration check (roll-call "Here." turns are typically 0.3–0.6s and rely on midpoint-containment, which the guard does not touch).

# Federal Adapter — Slice 2 (per-vote timestamps via ASR anchoring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp each CREC roll-call vote with the transcript timestamp at which its result was announced, by matching the ASR "the yeas are X, the nays are Y" announcement to the vote's CREC tally.

**Architecture:** A pure module `src/crec_timing.py` that (1) extracts vote announcements — tally + word-level timestamp — from a timestamped transcript, and (2) monotonically matches them to Slice-1 `RollCallVote` objects by tally (tolerant of small ASR mis-hears), then attaches the timestamp to each vote. No network, no new heavy deps; consumes the `transcript_named.json` the pipeline already produces and the `RollCallVote` list from `src/crec_votes.py`.

**Tech Stack:** Python 3, `pytest`, stdlib `re`/`json`/`dataclasses`. Use `.venv/bin/pytest` and `.venv/bin/python`.

**Grounding:** This mechanism was de-risked 2026-07-19 on a real House-floor capture (`~/CouncilScribe/meetings/2019-07-11-house-floor-ndaa/transcript_named.json`): rolls 438–442 each produced a clean announcement whose tally uniquely matched the CREC roll, with one ASR off-by-one (440: heard 230, CREC 231) — which is exactly why matching must tolerate a small delta. See `docs/superpowers/specs/2026-07-18-federal-crec-spike-findings.md`.

**Scope note — this plan delivers:** `attach_vote_timestamps(rolls, transcript_segments) -> list[VoteTiming]` that populates `RollCallVote.timestamp`. Explicitly OUT of scope (later work): mapping transcript-relative time to absolute source-video time (the pipeline already tracks `clip_start_seconds`); timing for non-recorded (voice) votes, bill-consideration items, and one-minute/attention speeches; the House caption-text reference transcript; wiring timestamps into the publish/web output.

---

## File Structure

- Modify `src/crec_votes.py` — add an optional `timestamp` field to `RollCallVote` (backward-compatible; default `None`).
- Create `src/crec_timing.py` — `VoteAnnouncement`, `extract_announcements`, `VoteTiming`, `match_rolls_to_announcements`, `attach_vote_timestamps`.
- Create fixture `tests/fixtures/timing/house_vote_announcements.json` — three real announcement segments from the 2019-07-11 capture.
- Create test `tests/test_crec_timing.py`.

---

## Task 1: Ground-truth announcement fixture

Real segments (text + word-level timestamps) captured from the 2019-07-11 House run.

**Files:**
- Create: `tests/fixtures/timing/house_vote_announcements.json`

- [ ] **Step 1: Create the fixture directory and file**

Create `tests/fixtures/timing/house_vote_announcements.json` with exactly (real values — rolls 438, 439, 440):

```json
[
  {
    "speaker_label": "SPEAKER_00",
    "start_time": 102.16,
    "end_time": 123.57,
    "text": "this vote, the yeas are 236, the nays are 193. The amendment is adopted.",
    "words": [
      {"word": "this", "start": 102.12}, {"word": "vote,", "start": 102.28},
      {"word": "the", "start": 102.58}, {"word": "yeas", "start": 102.64},
      {"word": "are", "start": 102.94}, {"word": "236,", "start": 103.06},
      {"word": "the", "start": 104.06}, {"word": "nays", "start": 104.22},
      {"word": "are", "start": 104.54}, {"word": "193.", "start": 104.84}
    ]
  },
  {
    "speaker_label": "SPEAKER_00",
    "start_time": 451.81,
    "end_time": 456.30,
    "text": "On this vote, the yeas are 242, the nays are 187. The amendment is adopted.",
    "words": [
      {"word": "On", "start": 451.42}, {"word": "this", "start": 451.82},
      {"word": "vote,", "start": 452.00}, {"word": "the", "start": 452.36},
      {"word": "yeas", "start": 452.46}, {"word": "are", "start": 452.84},
      {"word": "242,", "start": 452.96}, {"word": "the", "start": 453.82},
      {"word": "nays", "start": 453.92}, {"word": "are", "start": 454.16},
      {"word": "187.", "start": 454.30}
    ]
  },
  {
    "speaker_label": "SPEAKER_00",
    "start_time": 731.70,
    "end_time": 735.00,
    "text": "this vote, the yeas are 230, the nays are 199, the amendment is adopted.",
    "words": [
      {"word": "this", "start": 731.50}, {"word": "vote,", "start": 731.92},
      {"word": "the", "start": 732.22}, {"word": "yeas", "start": 732.28},
      {"word": "are", "start": 732.52}, {"word": "230,", "start": 732.62},
      {"word": "the", "start": 733.24}, {"word": "nays", "start": 733.36},
      {"word": "are", "start": 733.54}, {"word": "199,", "start": 733.68}
    ]
  }
]
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/timing/house_vote_announcements.json
git commit -m "test(crec): add ground-truth House vote-announcement fixture"
```

---

## Task 2: Add `timestamp` field to `RollCallVote`

**Files:**
- Modify: `src/crec_votes.py`
- Test: `tests/test_crec_votes.py` (add one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crec_votes.py`:

```python
def test_rollcallvote_timestamp_defaults_none_and_is_settable():
    v = RollCallVote(438, "q", {"YEA": ["Adams"]})
    assert v.timestamp is None            # backward-compatible default
    v.timestamp = 102.64
    assert v.timestamp == 102.64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_votes.py::test_rollcallvote_timestamp_defaults_none_and_is_settable -v`
Expected: FAIL — `TypeError` (unexpected keyword) or `AttributeError` on `.timestamp` (field doesn't exist yet).

- [ ] **Step 3: Add the field**

In `src/crec_votes.py`, change the `RollCallVote` dataclass to add a trailing optional field (keep the existing fields and order):

```python
@dataclass
class RollCallVote:
    roll_number: int
    question: str
    positions: dict = field(default_factory=dict)  # "YEA"/"NAY"/"PRESENT"/"NOT_VOTING" -> [surname]
    timestamp: Optional[float] = None               # transcript-relative time of the result announcement (Slice 2)
```

(`Optional` is already imported in `crec_votes.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_crec_votes.py -v`
Expected: PASS (all prior tests still pass — the new field is optional; the positional `RollCallVote(438, "q", {...})` construction is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/crec_votes.py tests/test_crec_votes.py
git commit -m "feat(crec): add optional timestamp field to RollCallVote"
```

---

## Task 3: `extract_announcements` — parse tally + timestamp from the transcript

**Files:**
- Create: `src/crec_timing.py`
- Test: `tests/test_crec_timing.py`

- [ ] **Step 1: Write the failing test**

`tests/test_crec_timing.py`:

```python
import json
from pathlib import Path
from src.crec_timing import VoteAnnouncement, extract_announcements

FIX = Path(__file__).parent / "fixtures" / "timing"


def test_extract_announcements_from_real_segments():
    segs = json.loads((FIX / "house_vote_announcements.json").read_text())
    anns = extract_announcements(segs)
    assert len(anns) == 3
    assert all(isinstance(a, VoteAnnouncement) for a in anns)
    assert (anns[0].yea, anns[0].nay) == (236, 193)
    assert (anns[1].yea, anns[1].nay) == (242, 187)
    assert (anns[2].yea, anns[2].nay) == (230, 199)   # real ASR value (CREC roll 440 is 231)
    # timestamp = word-level start of the "yeas"/"ayes" token
    assert anns[0].timestamp == 102.64
    assert anns[1].timestamp == 452.46
    assert anns[2].timestamp == 732.28


def test_extract_handles_ayes_and_and_variants():
    segs = [
        {"start_time": 5.0, "text": "On this vote, the ayes are 243. The nays are 187.",
         "words": [{"word": "ayes", "start": 5.5}]},
        {"start_time": 9.0, "text": "the yeas are 225 and the nays are 205, the amendment is adopted.",
         "words": [{"word": "yeas", "start": 9.2}]},
    ]
    anns = extract_announcements(segs)
    assert [(a.yea, a.nay) for a in anns] == [(243, 187), (225, 205)]
    assert anns[0].timestamp == 5.5


def test_extract_skips_non_vote_segments_and_falls_back_to_start_time():
    segs = [
        {"start_time": 1.0, "text": "The gentleman is recognized for five minutes.", "words": []},
        {"start_time": 2.0, "text": "the yeas are 100, the nays are 50.", "words": []},  # no word ts
    ]
    anns = extract_announcements(segs)
    assert len(anns) == 1
    assert (anns[0].yea, anns[0].nay, anns[0].timestamp) == (100, 50, 2.0)  # fallback to start_time
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_timing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.crec_timing'`.

- [ ] **Step 3: Write the implementation**

Create `src/crec_timing.py`:

```python
"""Attach transcript timestamps to CREC roll-call votes (Slice 2, Federal adapter).

The presiding officer announces each recorded vote's result verbatim — "the yeas
are 236, the nays are 193, the amendment is adopted" — and the pipeline's
transcript carries word-level timestamps. We extract those announcements (tally +
timestamp) and monotonically match them to the Slice-1 RollCallVote objects by
tally. ASR mis-hears a digit occasionally (observed: 230 vs the true 231), so the
match tolerates a small delta and relies on chronological order. Pure; no network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .crec_votes import RollCallVote

# "the yeas/ayes are N ... the nays are M" (case-insensitive; tolerates "N, the",
# "N. The", "N and the"). Digits may carry trailing punctuation in the text.
_ANNOUNCE_RE = re.compile(
    r"(?:yeas|ayes)\s+are\s+(\d+)[,.\s]+(?:and\s+)?the\s+nays\s+are\s+(\d+)", re.I)
_ANCHOR_WORD_RE = re.compile(r"^(yeas|ayes)\b", re.I)


@dataclass
class VoteAnnouncement:
    yea: int
    nay: int
    timestamp: float
    text: str


def _announcement_timestamp(segment: dict) -> float:
    """Word-level start of the 'yeas'/'ayes' token; falls back to the segment start."""
    for w in segment.get("words") or []:
        token = str(w.get("word", "")).strip().lower()
        if _ANCHOR_WORD_RE.match(token) and isinstance(w.get("start"), (int, float)):
            return float(w["start"])
    return float(segment.get("start_time") or 0.0)


def extract_announcements(segments: list) -> list[VoteAnnouncement]:
    """Vote-result announcements (tally + timestamp) from transcript segments, in order."""
    out: list[VoteAnnouncement] = []
    for seg in segments:
        m = _ANNOUNCE_RE.search(seg.get("text") or "")
        if not m:
            continue
        out.append(VoteAnnouncement(
            yea=int(m.group(1)),
            nay=int(m.group(2)),
            timestamp=_announcement_timestamp(seg),
            text=(seg.get("text") or "").strip(),
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_timing.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_timing.py tests/test_crec_timing.py
git commit -m "feat(crec): extract vote-result announcements from transcript"
```

---

## Task 4: `match_rolls_to_announcements` — monotonic tally match

**Files:**
- Modify: `src/crec_timing.py`
- Test: `tests/test_crec_timing.py` (add tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crec_timing.py`:

```python
from src.crec_votes import RollCallVote
from src.crec_timing import VoteTiming, match_rolls_to_announcements


def _roll(n, yea, nay):
    # positions carry the surname lists; the tally is their length
    return RollCallVote(n, "q", {"YEA": ["x"] * yea, "NAY": ["y"] * nay})


def test_match_exact_and_off_by_one():
    rolls = [_roll(438, 236, 193), _roll(439, 242, 187), _roll(440, 231, 199)]
    anns = [
        VoteAnnouncement(236, 193, 102.64, ""),
        VoteAnnouncement(242, 187, 452.46, ""),
        VoteAnnouncement(230, 199, 732.28, ""),   # off by one vs roll 440 (231)
    ]
    timings = match_rolls_to_announcements(rolls, anns)
    assert [(t.roll_number, t.timestamp, t.matched) for t in timings] == [
        (438, 102.64, True),
        (439, 452.46, True),
        (440, 732.28, True),
    ]
    assert timings[0].tally_delta == 0
    assert timings[2].tally_delta == 1            # 231 -> 230


def test_match_skips_spurious_announcement_and_preserves_order():
    rolls = [_roll(438, 236, 193)]
    anns = [VoteAnnouncement(999, 1, 10.0, ""), VoteAnnouncement(236, 193, 102.64, "")]
    timings = match_rolls_to_announcements(rolls, anns)
    assert (timings[0].roll_number, timings[0].timestamp) == (438, 102.64)


def test_match_unmatched_roll_gets_none():
    rolls = [_roll(500, 300, 100)]                # no announcement near this tally
    anns = [VoteAnnouncement(236, 193, 102.64, "")]
    timings = match_rolls_to_announcements(rolls, anns)
    assert timings[0].matched is False
    assert timings[0].timestamp is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_timing.py -v`
Expected: FAIL — `ImportError: cannot import name 'VoteTiming'` / `match_rolls_to_announcements`.

- [ ] **Step 3: Write the implementation**

Append to `src/crec_timing.py`:

```python
@dataclass
class VoteTiming:
    roll_number: int
    timestamp: Optional[float]
    tally_delta: Optional[int]   # |Δyea|+|Δnay| of the matched announcement; None if unmatched
    matched: bool


def _expected_tally(rc: RollCallVote) -> tuple[int, int]:
    return len(rc.positions.get("YEA", [])), len(rc.positions.get("NAY", []))


def match_rolls_to_announcements(
    rolls: list, announcements: list, *, tol: int = 3
) -> list:
    """Match each roll (in roll order) to the next chronological announcement whose
    tally is within `tol` (|Δyea|+|Δnay|). Monotonic: once an announcement is
    consumed, later rolls only see later announcements — so a spurious announcement
    is skipped and a missing one leaves its roll unmatched. Tolerance absorbs ASR
    digit mis-hears (e.g. 230 vs 231).
    """
    results: list = []
    ai = 0
    for rc in rolls:
        ey, en = _expected_tally(rc)
        matched = None
        j = ai
        while j < len(announcements):
            a = announcements[j]
            delta = abs(a.yea - ey) + abs(a.nay - en)
            if delta <= tol:
                matched = (j, a, delta)
                break
            j += 1
        if matched is not None:
            j, a, delta = matched
            results.append(VoteTiming(rc.roll_number, a.timestamp, delta, True))
            ai = j + 1
        else:
            results.append(VoteTiming(rc.roll_number, None, None, False))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_timing.py -v`
Expected: PASS (6 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/crec_timing.py tests/test_crec_timing.py
git commit -m "feat(crec): monotonic tally match of rolls to vote announcements"
```

---

## Task 5: `attach_vote_timestamps` — orchestration

**Files:**
- Modify: `src/crec_timing.py`
- Test: `tests/test_crec_timing.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crec_timing.py`:

```python
from src.crec_timing import attach_vote_timestamps


def test_attach_sets_timestamp_on_matched_rolls():
    segs = json.loads((FIX / "house_vote_announcements.json").read_text())
    rolls = [_roll(438, 236, 193), _roll(439, 242, 187), _roll(440, 231, 199)]
    timings = attach_vote_timestamps(rolls, segs)
    # returns timings AND mutates the RollCallVote objects in place
    assert [t.roll_number for t in timings] == [438, 439, 440]
    assert rolls[0].timestamp == 102.64
    assert rolls[1].timestamp == 452.46
    assert rolls[2].timestamp == 732.28   # off-by-one tolerated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_timing.py -v`
Expected: FAIL — `ImportError: cannot import name 'attach_vote_timestamps'`.

- [ ] **Step 3: Write the implementation**

Append to `src/crec_timing.py`:

```python
def attach_vote_timestamps(rolls: list, transcript_segments: list, *, tol: int = 3) -> list:
    """Extract announcements from the transcript, match them to `rolls`, and set
    each matched RollCallVote.timestamp in place. Returns the list of VoteTiming.

    `rolls` is a flat list of RollCallVote in chronological (roll-number) order —
    from a Slice-1 FloorStructure, flatten with:
        [rc for gv in floor_structure.votes for rc in gv.votes]
    """
    announcements = extract_announcements(transcript_segments)
    timings = match_rolls_to_announcements(rolls, announcements, tol=tol)
    for rc, timing in zip(rolls, timings):
        rc.timestamp = timing.timestamp
    return timings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_timing.py tests/test_crec_votes.py -v`
Expected: PASS (all green — Slice-1 vote tests still pass with the new optional field).

- [ ] **Step 5: Commit**

```bash
git add src/crec_timing.py tests/test_crec_timing.py
git commit -m "feat(crec): attach_vote_timestamps orchestration (Slice 2)"
```

---

## Task 6: Live end-to-end validation (manual, uses the captured transcript + GOVINFO_API_KEY)

**Files:** none (validation against the retained 2019-07-11 capture).

- [ ] **Step 1: Run Slice 1 + Slice 2 against real data and eyeball the timestamps**

```bash
.venv/bin/python -c "
import os, json
from pathlib import Path
for line in Path('.env.local').read_text().splitlines():
    if line.startswith('GOVINFO_API_KEY'):
        os.environ['GOVINFO_API_KEY'] = line.split('=',1)[1].strip().strip('\"').strip(\"'\")
from src.crec_floor import extract_floor_structure
from src.crec_timing import attach_vote_timestamps
fs = extract_floor_structure('2019-07-11', 'house', max_granules=90)
rolls = [rc for gv in fs.votes for rc in gv.votes]
segs = json.loads((Path.home()/'CouncilScribe/meetings/2019-07-11-house-floor-ndaa/transcript_named.json').read_text())['segments']
timings = attach_vote_timestamps(rolls, segs)
stamped = [(t.roll_number, round(t.timestamp,1), t.tally_delta) for t in timings if t.matched]
print('stamped rolls:', stamped)
print('total rolls:', len(rolls), 'stamped:', sum(1 for t in timings if t.matched))
"
```
Expected (the clip covers rolls 438–442): rolls **438≈102.6s, 439≈452.5s, 440≈732.3s, 441≈1032.9s, 442≈1303.4s** appear with small `tally_delta` (0 or 1). Rolls outside the clip window (443–458) stay unmatched — that is correct, the clip only covers five votes. Record the output in the PR description. **This is a sanity eyeball, not an assertion.**

- [ ] **Step 2: Note follow-ons in the PR description (do not implement here)**
  - Absolute video-time mapping (transcript-relative → source seconds) via the pipeline's `clip_start_seconds`.
  - Non-recorded (voice) vote + bill-consideration item timing; one-minute/attention-speech timing.
  - Wiring `RollCallVote.timestamp` into the meeting publish / web-player output.
  - Capturing a full-session (not clip) transcript so all 21 rolls can be stamped in one pass.

---

## Self-Review

**Spec coverage (against the de-risked Slice-2 mechanism):**
- Extract announcements (tally + word timestamp) → Task 3 ✓
- Monotonic tally match tolerant of ASR off-by-one → Task 4 ✓
- Attach timestamp to the vote → Tasks 2 + 5 ✓
- Real-data validation (rolls 438–442) → Task 6 ✓
- Absolute-time mapping, voice/item/attention timing, publish wiring → explicitly deferred (Task 6 notes) ✓

**Placeholder scan:** none — every step has runnable code/commands and real fixture values.

**Type consistency:** `RollCallVote.timestamp` (Task 2) is set by `attach_vote_timestamps` (Task 5). `VoteAnnouncement`/`extract_announcements` (Task 3) feed `match_rolls_to_announcements`/`VoteTiming` (Task 4), which `attach_vote_timestamps` (Task 5) orchestrates. `VoteTiming` fields (`roll_number`, `timestamp`, `tally_delta`, `matched`) are used consistently in the Task 4 and Task 6 assertions. `_expected_tally` reads `positions["YEA"]/["NAY"]` — the same structure `crec_votes.parse_votes` produces.

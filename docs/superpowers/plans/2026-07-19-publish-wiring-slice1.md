# Publish Wiring — Slice 1 (produce + persist floor votes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run CREC floor-structure extraction during processing and persist the recorded votes (with transcript timestamps) onto the meeting artifact — so votes exist in the pipeline output, ready for a later publish slice.

**Architecture:** Add a slim, serializable `FloorVote` to the `Meeting` model. In `run_local.py`, for Congressional-Record runs, after speaker identification finalizes `meeting.segments`, call the (already-shipped, standalone) `crec_floor.extract_floor_structure` + `crec_timing.attach_vote_timestamps` via a new `crec_floor.build_floor_votes` helper, set `meeting.floor_votes`, and let the existing `transcript_named.json` save persist it. Timestamps stay clip-local (absolutized at publish per ADR-0001 — a later slice).

**Tech Stack:** Python 3, `pytest`. Use `.venv/bin/pytest` / `.venv/bin/python`. Builds on merged PRs #95–#97 (`crec_floor`, `crec_timing`, `RollCallVote.timestamp`, `absolutize_vote_timestamps`).

**Scope (confirmed with the user):** In-repo pipeline + persist only. OUT of scope (later slices): writing votes to the `meetings.*` Postgres DB (decided storage = embed in the existing `summary` JSON), the ev-accounts API, the web click-to-seek UI, absolutizing floor votes at the publish boundary, and recording `clip_start_seconds` via full-source `--clip` ingestion.

---

## File Structure

- Modify `src/models.py` — new `FloorVote` dataclass + `Meeting.floor_votes` field (to_dict/from_dict).
- Modify `src/crec_floor.py` — new `build_floor_votes(floor_structure, transcript_segments)` helper.
- Modify `run_local.py` — insert the CREC floor-structure step in the Stage-4 block (between segment-merge and the `transcript_named.json` save).
- Modify `tests/test_crec_floor.py` — test `build_floor_votes`.
- Create/append tests for the model round-trip (in `tests/test_crec_floor.py` or a model test file).

---

## Task 1: `FloorVote` model + `Meeting.floor_votes`

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_crec_floor.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crec_floor.py`:

```python
def test_floorvote_and_meeting_roundtrip():
    from src.models import FloorVote, Meeting
    fv = FloorVote(roll_number=438, question="On the Smith amendment", yea=236, nay=193,
                   present=0, not_voting=9, timestamp=102.6, tally_delta=0, matched=True)
    assert FloorVote.from_dict(fv.to_dict()) == fv
    m = Meeting(meeting_id="m1", city=None, date="2019-07-11", floor_votes=[fv])
    m2 = Meeting.from_dict(m.to_dict())
    assert m2.floor_votes == [fv]
    # backward-compatible: a meeting dict with no floor_votes yields an empty list
    assert Meeting.from_dict({"meeting_id": "m2", "date": "2019-07-11"}).floor_votes == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_floor.py::test_floorvote_and_meeting_roundtrip -v`
Expected: FAIL — `ImportError: cannot import name 'FloorVote'`.

- [ ] **Step 3: Add the dataclass and field**

In `src/models.py`, add the `FloorVote` dataclass immediately before the `Meeting` class (`Optional` is already imported):

```python
@dataclass
class FloorVote:
    """A slim, published projection of a CREC roll-call vote with its transcript
    timing. No member lists — meant to ride in the meeting artifact (and later the
    published summary JSON). `timestamp` is clip-local; absolutized at publish."""
    roll_number: int
    question: str
    yea: int
    nay: int
    present: int
    not_voting: int
    timestamp: Optional[float]
    tally_delta: Optional[int]
    matched: bool

    def to_dict(self) -> dict:
        return {
            "roll_number": self.roll_number, "question": self.question,
            "yea": self.yea, "nay": self.nay, "present": self.present,
            "not_voting": self.not_voting, "timestamp": self.timestamp,
            "tally_delta": self.tally_delta, "matched": self.matched,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FloorVote":
        return cls(
            roll_number=d["roll_number"], question=d.get("question", ""),
            yea=d.get("yea", 0), nay=d.get("nay", 0),
            present=d.get("present", 0), not_voting=d.get("not_voting", 0),
            timestamp=d.get("timestamp"), tally_delta=d.get("tally_delta"),
            matched=d.get("matched", False),
        )
```

In the `Meeting` dataclass, add the field right after `thumbnail_url: Optional[str] = None`:

```python
    floor_votes: list[FloorVote] = field(default_factory=list)
```

In `Meeting.to_dict`, add before `return d` (only serialize when present, keeping other artifacts unchanged):

```python
        if self.floor_votes:
            d["floor_votes"] = [v.to_dict() for v in self.floor_votes]
```

In `Meeting.from_dict`, add this keyword argument to the `cls(...)` call (alongside `event_orgs=...`):

```python
            floor_votes=[FloorVote.from_dict(v) for v in d.get("floor_votes", [])],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_floor.py -v`
Expected: PASS (existing crec_floor tests + the new round-trip test).

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_crec_floor.py
git commit -m "feat(crec): FloorVote model + Meeting.floor_votes field"
```

---

## Task 2: `build_floor_votes` helper

**Files:**
- Modify: `src/crec_floor.py`
- Test: `tests/test_crec_floor.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_crec_floor.py`:

```python
def test_build_floor_votes_projects_and_timestamps():
    import json
    from pathlib import Path
    from src.crec_votes import RollCallVote
    from src.crec_floor import GranuleVotes, build_floor_votes

    FIX = Path(__file__).parent / "fixtures" / "timing"
    segs = json.loads((FIX / "house_vote_announcements.json").read_text())

    # rolls 438/439/440 with tallies matching the fixture announcements
    r438 = RollCallVote(438, "Smith amdt", {"YEA": ["x"] * 236, "NAY": ["y"] * 193})
    r439 = RollCallVote(439, "Speier amdt", {"YEA": ["x"] * 242, "NAY": ["y"] * 187})
    r440 = RollCallVote(440, "Speier amdt", {"YEA": ["x"] * 231, "NAY": ["y"] * 199})
    from src.crec_structure import CrecGranule
    gv = GranuleVotes(granule=CrecGranule("g", "HOUSE", "NDAA", ""),
                      votes=[r438, r439, r440], members=[])

    class _FS:  # minimal stand-in for FloorStructure.votes
        votes = [gv]

    fvs = build_floor_votes(_FS(), segs)
    assert [v.roll_number for v in fvs] == [438, 439, 440]
    assert (fvs[0].yea, fvs[0].nay) == (236, 193)
    assert fvs[0].timestamp == 102.64 and fvs[0].matched is True
    assert fvs[2].timestamp == 732.28 and fvs[2].tally_delta == 1   # off-by-one tolerated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_crec_floor.py::test_build_floor_votes_projects_and_timestamps -v`
Expected: FAIL — `ImportError: cannot import name 'build_floor_votes'`.

- [ ] **Step 3: Add the helper**

Append to `src/crec_floor.py`:

```python
def build_floor_votes(floor_structure, transcript_segments):
    """Project a FloorStructure's roll-call votes into slim, timestamped FloorVote
    records (models.FloorVote) for persistence/publish. Attaches clip-local
    transcript timestamps via crec_timing.attach_vote_timestamps.

    `transcript_segments` is a list of segment dicts (Segment.to_dict): each has
    `text`, `start_time`, and `words` [{word, start}].
    """
    from .models import FloorVote
    from .crec_timing import attach_vote_timestamps

    rolls = [rc for gv in floor_structure.votes for rc in gv.votes]
    timings = attach_vote_timestamps(rolls, transcript_segments)
    out = []
    for rc, timing in zip(rolls, timings):
        p = rc.positions
        out.append(FloorVote(
            roll_number=rc.roll_number,
            question=rc.question,
            yea=len(p.get("YEA", [])),
            nay=len(p.get("NAY", [])),
            present=len(p.get("PRESENT", [])),
            not_voting=len(p.get("NOT_VOTING", [])),
            timestamp=rc.timestamp,
            tally_delta=timing.tally_delta,
            matched=timing.matched,
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_crec_floor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/crec_floor.py tests/test_crec_floor.py
git commit -m "feat(crec): build_floor_votes — project FloorStructure votes to timestamped FloorVotes"
```

---

## Task 3: Wire the floor-structure step into the pipeline

**Files:**
- Modify: `run_local.py` (insert in the Stage-4 identification block)

- [ ] **Step 1: Locate the insertion point**

In `run_local.py`, find (in `run_pipeline`, the Stage-4 block that runs when identification is not already complete) these two adjacent statements:

```python
        from src.identify import merge_adjacent_segments
        meeting.segments = merge_adjacent_segments(meeting.segments)

        with open(named_transcript_path, "w") as f:
            json.dump(meeting.to_dict(), f, indent=2)
        state.mark_complete(PipelineStage.IDENTIFIED)
```

- [ ] **Step 2: Insert the step between the merge and the save**

Insert this block immediately after `meeting.segments = merge_adjacent_segments(meeting.segments)` and immediately before `with open(named_transcript_path, "w") as f:` (so the votes are persisted by the existing save). `crec_request`, `_crec_date`, and `_crec_chamber` are already in scope from the CREC speaker-mapping block above:

```python
        # Federal floor structure: derive recorded votes + transcript timestamps
        # from the Congressional Record and persist them on the meeting artifact.
        # (Timestamps stay clip-local; absolutized at publish — a later slice.)
        if crec_request:
            from src.crec_floor import extract_floor_structure, build_floor_votes
            _floor = extract_floor_structure(_crec_date, _crec_chamber)
            if _floor:
                meeting.floor_votes = build_floor_votes(
                    _floor, [s.to_dict() for s in meeting.segments])
                _stamped = sum(1 for v in meeting.floor_votes if v.matched)
                print(f"  Floor structure: {len(meeting.floor_votes)} recorded vote(s), "
                      f"{_stamped} timestamped from the transcript")
```

- [ ] **Step 3: Verify existing tests still pass and the module imports**

Run: `.venv/bin/python -c "import run_local"` (expect no error)
Run: `.venv/bin/pytest tests/ -q` (expect the full suite green — no regression)

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat(crec): persist floor votes on CREC runs (pipeline wiring)"
```

---

## Task 4: Live validation on the retained 2019-07-11 capture

**Files:** none (validation).

- [ ] **Step 1: Re-run Stage 4 on the captured meeting to trigger the new step**

The meeting `2019-07-11-house-floor-ndaa` already has diarization + transcription checkpoints. Re-run identification (which now includes the floor-structure step); `--no-review` keeps it non-interactive, `--redo identify` rewinds to re-run Stage 4:

```bash
.venv/bin/python run_local.py --resume 2019-07-11-house-floor-ndaa \
  --congressional-record 2019-07-11 house --redo identify --skip-summary --no-review
```

- [ ] **Step 2: Confirm floor votes persisted with timestamps**

```bash
.venv/bin/python -c "
import json
from pathlib import Path
d = json.loads((Path.home()/'CouncilScribe/meetings/2019-07-11-house-floor-ndaa/transcript_named.json').read_text())
fv = d.get('floor_votes', [])
print('floor_votes persisted:', len(fv))
for v in fv:
    if v.get('matched'):
        print(f\"  roll {v['roll_number']}: {v['yea']}-{v['nay']} @ {v['timestamp']}s (delta {v['tally_delta']})\")
"
```
Expected: `floor_votes` present; rolls 438–442 matched with timestamps ~102.6/452.5/732.3/1032.9/1303.4 s (roll 440 `tally_delta 1`); unmatched rolls (443–458) present but with `timestamp: null`, `matched: false`. Record the output in the PR description.

- [ ] **Step 3: Note follow-ons in the PR description (do not implement here)**
  - Publish slice: embed `floor_votes` in the published `summary` JSON (`publish.py`), and extend `clip.absolutize_meeting_times` to shift `floor_votes` timestamps at the publish boundary.
  - ev-accounts API + web click-to-seek UI.
  - Record `clip_start_seconds` on captures via full-source `--clip` ingestion (needs the House-CDN ingestion adapter).
  - Optional: share the single CREC fetch between speaker-ID (`crec_identify`) and structure (`extract_floor_structure`) instead of fetching twice.

---

## Self-Review

**Spec coverage:** produce floor structure during CREC processing (Task 3) ✓; persist votes on the artifact (Tasks 1+3) ✓; timestamps attached (Task 2, reuses shipped `attach_vote_timestamps`) ✓; live validation (Task 4) ✓; DB/API/web/absolutize-at-publish/clip-offset all explicitly deferred ✓.

**Placeholder scan:** none — exact insertion anchors, complete code, runnable commands.

**Type consistency:** `FloorVote` (Task 1, `src/models.py`) is produced by `build_floor_votes` (Task 2, `src/crec_floor.py`) from `RollCallVote`/`GranuleVotes` (Slice 1) + `attach_vote_timestamps` (Slice 2), set on `Meeting.floor_votes` (Task 1) and persisted by the Task-3 pipeline step; field names (`roll_number`, `yea`, `nay`, `present`, `not_voting`, `timestamp`, `tally_delta`, `matched`) match across the model, the builder, and the Task-4 assertions.

**Note on resume:** the step runs inside the not-yet-identified Stage-4 branch, so on a normal `--resume` (identification already complete) `floor_votes` is loaded from the persisted artifact rather than recomputed — correct and network-free. Task 4 uses `--redo identify` specifically to force recomputation for validation.

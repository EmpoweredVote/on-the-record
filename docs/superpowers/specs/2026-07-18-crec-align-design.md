# CREC ↔ diarization alignment (Phase 3) — design

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Parent:** `docs/superpowers/specs/2026-07-18-congressional-record-speaker-oracle-design.md` (Phase 3 of 5)
**Depends on:** Phase 1 `src/govinfo.py` (`CrecTurn`), Phase 2 `src/crec_normalize.py` (`annotate_turns`, `ResolvedSpeaker`) + `src/congress_roster.py` (`CongressMember`) — both shipped.

## Goal

Attach the Congressional Record's speaker identities onto the pipeline's **anonymous diarized speaker labels**. Given the diarized segments (verbatim ASR/caption text + timestamps, but nameless `speaker_label`s) and the CREC turns (identity-annotated by Phase 2, but non-verbatim and timeless), align the two ordered sequences by **text overlap under a monotonic constraint**, then aggregate per label to a resolved identity. This is the payoff of the whole oracle: it turns diarization's `SPEAKER_00` into "Ms. Baldwin" using the Record as ground truth for *who spoke, in what order*.

## The core idea

- **Timestamps + verbatim words** stay owned by ASR/captions on the `Segment`s (preserves ADR-0001 exact-moment linking — untouched here).
- **Identity + order** come from CREC. We align the two sequences and transfer CREC identities onto the diarized labels.
- Output is per-`speaker_label`, ready for Phase 4 to convert into `SpeakerMapping` inside `identify_speakers`.

## Scope decisions (locked during brainstorming)

- **Match strategy:** text-overlap anchoring + monotonic ordering. Robust to CREC being non-verbatim ("revise and extend", light editing) — matches on shared content words, not exact wording. Dependency-free.
- **One-to-one alignment** (LCS-style DP), not many-to-one. Diarization split/merge residue is absorbed by per-label aggregation. Many-to-one (affine/banded) is a deferred enhancement.
- **Per-label aggregation with confidence gating** — majority-vote member identity per label; low-confidence / split-vote → `needs_review`, never a confident wrong label.

## Non-goals

- No Stage-4 `identify.py` wiring / `SpeakerMapping` construction / CLI-GUI plumbing — that's Phase 4.
- No essentials `politician_id` resolution (Phase 2 already deferred it; alignment carries the `CongressMember` / bioguide).
- No many-to-one alignment; no embedding-based similarity.
- No changes to diarization, transcription, or word-assignment.

## Inputs / output

```python
def align_crec_to_diarization(
    segments: list[Segment],                              # diarized, time-ordered, text-bearing
    annotated_turns: list[tuple[CrecTurn, ResolvedSpeaker]],  # Phase 2 annotate_turns output
    *,
    min_confidence: float = 0.5,
) -> dict[str, LabelResolution]                           # speaker_label -> resolution
```

`Segment` (existing, `src/models.py`): `segment_id, start_time, end_time, speaker_label, text, words, ...`. This phase reads `speaker_label` and `text` (ASR/caption transcript, present after word-assignment).

```python
@dataclass
class LabelResolution:
    speaker_label: str
    member: Optional[CongressMember]   # majority member identity, when confident
    role: Optional[str]                # e.g. 'presiding_officer' when role-dominant
    confidence: float                  # 0..1
    method: str                        # 'congressional_record' | 'ambiguous' | 'unresolved'
    needs_review: bool
    matched_turns: int                 # how many CREC turns backed this label
    total_turns: int                   # how many diarized runs the label has
```

## Pipeline (inside `src/crec_align.py`)

### 1. Build diarized turns — `_build_diarized_turns(segments) -> list[DiarizedTurn]`
Group **consecutive** segments sharing a `speaker_label` into maximal runs. Each `DiarizedTurn` carries `{speaker_label, text (segments' text joined with spaces), index (position in the run list)}`. This is the unit that corresponds to one CREC turn (one continuous speech), collapsing diarization's many-small-segments. Segment timestamps/indices are intentionally not carried — Phase 3 attaches identity only, never touches timing.

```python
@dataclass
class DiarizedTurn:
    speaker_label: str
    text: str
    index: int          # position in the ordered run list
```

### 2. Tokenize — `_content_tokens(text) -> set[str]`
Lowercase, strip punctuation, split on whitespace, drop a small English stopword set and tokens shorter than 3 chars. Returns a set of content words. Pure.

### 3. Similarity — `_overlap(a: set, b: set) -> float`
Overlap coefficient: `len(a & b) / min(len(a), len(b))` (0.0 when either is empty). Chosen over Jaccard because CREC turns are often longer than the spoken run; containment rewards the shorter side's words appearing in the longer, so CREC's added/edited material does not tank the score.

### 4. Monotonic alignment — `_align(d_turns, c_turns) -> list[tuple[int, int]]`
LCS-style DP maximizing total matched similarity, order-preserving and non-crossing, with free gaps on both sides:

```
DP[i][j] = max(
    DP[i-1][j],                          # skip diarized turn i (interjection not in CREC)
    DP[i][j-1],                          # skip CREC turn j (revise-and-extend, never spoken)
    DP[i-1][j-1] + sim(d_i, c_j),        # match
)
```

Backtrack to the matched `(d_index, c_index)` pairs. To avoid spurious matches, a matched pair is kept only if its `sim` exceeds a small floor (`_MATCH_FLOOR`, e.g. 0.1); pairs at/below the floor are treated as gaps. Complexity O(m·n) with m diarized runs, n CREC turns — both small for a session.

### 5. Aggregate per label — `_aggregate(d_turns, annotated_turns, pairs, min_confidence) -> dict`
For each `speaker_label`:
- Collect the `ResolvedSpeaker`s of the CREC turns its runs matched.
- **Majority vote among member identities** (keyed on `member.bioguide`). Procedural-role-only matches yield a `role` resolution, not a member; a member vote outweighs role matches.
- `matched_turns` = number of this label's runs that matched a member-bearing CREC turn; `total_turns` = number of runs for the label.
- `confidence` = `(matched_turns / total_turns)` × `(votes_for_winner / matched_turns)` × `mean_overlap_of_winner_matches`. (All three factors in 0..1; product in 0..1.)
- `method`/`needs_review`:
  - confident unique member (`confidence >= min_confidence`, single clear winner) → `method='congressional_record'`, `needs_review=False`.
  - a member surfaced but confidence below gate, or a tie between members → `method='ambiguous'`, `needs_review=True`.
  - only role matches → `role=<slug>`, `method='congressional_record'`, `needs_review=False`.
  - nothing matched → `method='unresolved'`, `needs_review=False` (nothing to review; falls through to existing layers in Phase 4).

## Data-flow guarantees

- **ADR-0001 preserved:** timestamps/words are never touched; only identity is attached to labels.
- **Identity-collision guard:** a tie or sub-threshold member → `needs_review`, never a confident wrong assignment. (Phase 4 will additionally reconcile against the existing `_dedupe_identities` so two labels can't silently become the same member.)
- **Graceful degradation:** empty CREC turns, empty segments, or all-below-floor overlaps → every label `unresolved`; never raises.

## Testing (offline, synthetic)

Hand-built `Segment` lists + `annotated_turns` (identities drawn from the Phase-2 fixture roster) covering:
- clean 1:1 conversation between two members → each label resolves to the right member, `confidence` high.
- **over-segmentation:** one member's speech split into two runs by a one-word interjection → the label still resolves (aggregation absorbs the unmatched run).
- **revise-and-extend:** a CREC turn with no spoken counterpart → free gap, no misalignment, other labels unaffected.
- **procedural interjection:** `The PRESIDING OFFICER. Without objection.` between two member turns → its label resolves to `role='presiding_officer'`.
- **split vote:** a label whose runs match two different members → `needs_review`, `method='ambiguous'`.
- **low overlap / empty:** unrelated text or empty inputs → `unresolved`, no crash.
- unit tests for `_content_tokens`, `_overlap` (incl. empty sets), `_build_diarized_turns` (contiguous grouping), and `_align` (matched-pair indices on a small hand-traced case).

## Known limits (carried forward)

- **One-to-one only** — heavy many-to-one splitting could leave some runs unmatched; per-label aggregation mitigates, and true many-to-one is a deferred enhancement.
- **Role-dominant labels** resolve to a role, not a specific member (Phase 2 gap: bare leadership roles / non-parenthetical presiding officers).
- Relies on `Segment.text` being populated (true post word-assignment); segments with empty text contribute no tokens and simply don't anchor.

## Files

- Create: `src/crec_align.py`
- Create: `tests/test_crec_align.py`
- (Reuses the existing `tests/fixtures/congress/legislators-current.sample.json` for member identities.)

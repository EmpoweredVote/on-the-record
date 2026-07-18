# CREC ↔ Diarization Alignment (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/crec_align.py` — align identity-annotated CREC turns (Phase 2) onto anonymous diarized speaker labels by content-word overlap under a monotonic constraint, then aggregate per label to a confidence-gated identity (`dict[speaker_label -> LabelResolution]`).

**Architecture:** Pure module, no network. Pipeline: `_build_diarized_turns` (group consecutive same-label segments) → `_content_tokens`/`_overlap` (similarity) → `_align` (LCS-style monotonic DP) → `_aggregate` (per-label majority vote + `_confidence` gating). Attaches identity only; never touches timestamps/words. The `_confidence()` function is deliberately isolated so the formula can be retuned after real-data testing without touching alignment logic.

**Tech Stack:** Python 3.14 (`.venv/bin/python`), pytest, stdlib only (`re`, `collections`). No new deps.

**Spec:** `docs/superpowers/specs/2026-07-18-crec-align-design.md`.

**Consumes (shipped):** `src.models.Segment` (`speaker_label`, `text`); `src.govinfo.CrecTurn`; `src.crec_normalize.annotate_turns` / `ResolvedSpeaker`; `src.congress_roster.CongressMember` / `build_roster`.

**Contract note:** a `LabelResolution` carries `member` only when confident; ambiguous (tie OR below `min_confidence`) → `member=None, method='ambiguous', needs_review=True`.

---

## File Structure

- Create: `src/crec_align.py` — the whole alignment module.
- Create: `tests/test_crec_align.py` — unit + end-to-end tests (reuses `tests/fixtures/congress/legislators-current.sample.json` for identities).

---

### Task 1: Tokenization + similarity — `_content_tokens`, `_overlap`

**Files:**
- Create: `src/crec_align.py`
- Test: `tests/test_crec_align.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_crec_align.py`:

```python
# tests/test_crec_align.py
from __future__ import annotations

from src.crec_align import _content_tokens, _overlap


def test_content_tokens_drops_stopwords_punct_and_short():
    toks = _content_tokens("The Senator moved to proceed, on the BILL!")
    assert toks == {"senator", "moved", "proceed", "bill"}


def test_content_tokens_empty():
    assert _content_tokens("") == set()
    assert _content_tokens(None) == set()


def test_overlap_coefficient():
    assert _overlap({"a", "b", "c"}, {"a", "b"}) == 1.0        # containment of smaller
    assert _overlap({"a", "b", "c", "d"}, {"a", "b"}) == 1.0
    assert _overlap({"a", "b"}, {"b", "x"}) == 0.5
    assert _overlap(set(), {"a"}) == 0.0
    assert _overlap({"a"}, set()) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.crec_align'`.

- [ ] **Step 3: Write minimal implementation** — create `src/crec_align.py`:

```python
# src/crec_align.py
"""Phase 3: align identity-annotated CREC turns onto anonymous diarized labels.

Given diarized segments (verbatim ASR/caption text + nameless speaker_labels)
and CREC turns annotated with identities (Phase 2 annotate_turns), align the two
ordered sequences by content-word overlap under a monotonic (order-preserving)
constraint, then aggregate per speaker_label to a resolved identity. Attaches
identity only — never touches timestamps/words (ADR-0001). Pure; no network.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .congress_roster import CongressMember

_STOPWORDS = frozenset("""
the a an and or but of to in on at for with as is are was were be been being
i you he she it we they that this these those my your his her its our their
will would shall should can could may might must do does did have has had not
""".split())

# A backtracked diagonal only counts as a real match above this overlap floor.
_MATCH_FLOOR = 0.1


def _content_tokens(text: str) -> set[str]:
    """Lowercased content-word set: drop punctuation, stopwords, tokens < 3 chars."""
    toks = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def _overlap(a: set, b: set) -> float:
    """Overlap coefficient: |a∩b| / min(|a|,|b|); 0.0 if either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_align.py tests/test_crec_align.py
git commit -m "feat(crec_align): content tokenization + overlap coefficient"
```

---

### Task 2: Diarized turns — `DiarizedTurn`, `_build_diarized_turns`

**Files:**
- Modify: `src/crec_align.py`
- Test: `tests/test_crec_align.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_align.py`:

```python
# add to tests/test_crec_align.py
from src.models import Segment
from src.crec_align import DiarizedTurn, _build_diarized_turns


def _seg(i, label, text):
    return Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                   speaker_label=label, text=text)


def test_build_diarized_turns_groups_consecutive_same_label():
    segs = [
        _seg(0, "SPEAKER_00", "hello there"),
        _seg(1, "SPEAKER_00", "friends"),
        _seg(2, "SPEAKER_01", "hi"),
        _seg(3, "SPEAKER_00", "again"),
    ]
    turns = _build_diarized_turns(segs)
    assert [(t.speaker_label, t.text, t.index) for t in turns] == [
        ("SPEAKER_00", "hello there friends", 0),
        ("SPEAKER_01", "hi", 1),
        ("SPEAKER_00", "again", 2),
    ]


def test_build_diarized_turns_empty():
    assert _build_diarized_turns([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -k build_diarized -v`
Expected: FAIL — `ImportError: cannot import name 'DiarizedTurn'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_align.py`:

```python
@dataclass
class DiarizedTurn:
    speaker_label: str
    text: str
    index: int


def _build_diarized_turns(segments) -> list[DiarizedTurn]:
    """Group consecutive segments sharing a speaker_label into maximal runs.

    Each run's text is its segments' text joined with spaces. Segment timestamps
    are intentionally not carried — Phase 3 attaches identity only.
    """
    turns: list[DiarizedTurn] = []
    for seg in segments:
        txt = (seg.text or "").strip()
        if turns and turns[-1].speaker_label == seg.speaker_label:
            turns[-1].text = f"{turns[-1].text} {txt}".strip()
        else:
            turns.append(DiarizedTurn(speaker_label=seg.speaker_label, text=txt, index=len(turns)))
    return turns
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_align.py tests/test_crec_align.py
git commit -m "feat(crec_align): build diarized turns from segment runs"
```

---

### Task 3: Monotonic alignment — `_align`

**Files:**
- Modify: `src/crec_align.py`
- Test: `tests/test_crec_align.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_align.py`:

```python
# add to tests/test_crec_align.py
from src.crec_align import _align


def test_align_clean_one_to_one():
    d = [{"apple", "pear"}, {"river", "bank"}]
    c = [{"apple", "pear"}, {"river", "bank"}]
    assert _align(d, c) == [(0, 0), (1, 1)]


def test_align_skips_unmatched_crec_turn_as_gap():
    # a CREC turn with no diarized counterpart is a free gap (revise-and-extend)
    d = [{"apple", "pear"}, {"river", "bank"}]
    c = [{"apple", "pear"}, {"zzz", "qqq"}, {"river", "bank"}]
    assert _align(d, c) == [(0, 0), (1, 2)]


def test_align_skips_unmatched_diarized_turn_as_gap():
    d = [{"apple", "pear"}, {"noise", "cough"}, {"river", "bank"}]
    c = [{"apple", "pear"}, {"river", "bank"}]
    assert _align(d, c) == [(0, 0), (2, 1)]


def test_align_below_floor_not_matched():
    # near-zero overlap must not produce a pair
    d = [{"apple", "pear", "cat", "dog", "fish"}]
    c = [{"apple", "zzz", "qqq", "www", "eee"}]   # overlap 1/5 = 0.2 > floor -> matched
    assert _align(d, c) == [(0, 0)]
    d2 = [{"a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "a10"}]
    c2 = [{"a1", "z2", "z3", "z4", "z5", "z6", "z7", "z8", "z9", "z10"}]  # 1/10 = 0.1, not > floor
    assert _align(d2, c2) == []


def test_align_empty():
    assert _align([], [{"a"}]) == []
    assert _align([{"a"}], []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -k align -v`
Expected: FAIL — `ImportError: cannot import name '_align'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_align.py`:

```python
def _align(d_tokens: list[set], c_tokens: list[set]) -> list[tuple[int, int]]:
    """Monotonic LCS-style alignment of two token-set sequences.

    Maximizes total matched overlap, order-preserving and non-crossing, with free
    gaps on both sides. Returns matched (d_index, c_index) pairs whose overlap
    exceeds `_MATCH_FLOOR`.
    """
    m, n = len(d_tokens), len(c_tokens)
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            diag = dp[i - 1][j - 1] + _overlap(d_tokens[i - 1], c_tokens[j - 1])
            dp[i][j] = max(dp[i - 1][j], dp[i][j - 1], diag)

    pairs: list[tuple[int, int]] = []
    i, j = m, n
    while i > 0 and j > 0:
        sim = _overlap(d_tokens[i - 1], c_tokens[j - 1])
        diag = dp[i - 1][j - 1] + sim
        if diag >= dp[i - 1][j] and diag >= dp[i][j - 1]:
            if sim > _MATCH_FLOOR:
                pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_align.py tests/test_crec_align.py
git commit -m "feat(crec_align): monotonic LCS-style sequence alignment"
```

---

### Task 4: Aggregation — `LabelResolution`, `_confidence`, `_aggregate`

**Files:**
- Modify: `src/crec_align.py`
- Test: `tests/test_crec_align.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_align.py`:

```python
# add to tests/test_crec_align.py
from src.crec_align import LabelResolution, _confidence, _aggregate
from src.congress_roster import CongressMember
from src.crec_normalize import ResolvedSpeaker


def _member(bio, last):
    return CongressMember(bio, f"First {last}", last, "XX", None, "senate", "Democrat")


def _rs_member(bio, last):
    return ResolvedSpeaker(member=_member(bio, last), method="surname", confidence=1.0)


def _rs_role(role):
    return ResolvedSpeaker(role=role, method="role", confidence=1.0)


def _dturn(label, idx):
    return DiarizedTurn(speaker_label=label, text="", index=idx)


def test_confidence_is_product_of_factors():
    assert _confidence(1.0, 1.0, 1.0) == 1.0
    assert _confidence(0.5, 1.0, 0.8) == 0.4
    assert _confidence(1.0, 0.5, 0.5) == 0.25


def test_aggregate_confident_member():
    d_turns = [_dturn("S0", 0), _dturn("S1", 1)]
    matches = [
        ("S0", _rs_member("B1", "Baldwin"), 0.9),
        ("S1", _rs_member("M1", "McConnell"), 0.8),
    ]
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member.bioguide == "B1"
    assert out["S0"].method == "congressional_record"
    assert out["S0"].needs_review is False
    assert out["S1"].member.bioguide == "M1"


def test_aggregate_split_vote_is_ambiguous():
    # one label's two runs match two different members -> tie -> ambiguous
    d_turns = [_dturn("S0", 0), _dturn("S0", 1)]
    matches = [
        ("S0", _rs_member("B1", "Baldwin"), 0.9),
        ("S0", _rs_member("M1", "McConnell"), 0.9),
    ]
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member is None
    assert out["S0"].method == "ambiguous"
    assert out["S0"].needs_review is True


def test_aggregate_below_gate_is_ambiguous_member_none():
    # a member surfaced but confidence below the gate
    d_turns = [_dturn("S0", 0), _dturn("S0", 1), _dturn("S0", 2), _dturn("S0", 3)]
    matches = [("S0", _rs_member("B1", "Baldwin"), 0.6)]   # match_fraction 1/4 -> conf 0.15
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member is None
    assert out["S0"].method == "ambiguous"
    assert out["S0"].needs_review is True
    assert out["S0"].matched_turns == 1
    assert out["S0"].total_turns == 4


def test_aggregate_role_dominant():
    d_turns = [_dturn("S0", 0)]
    matches = [("S0", _rs_role("presiding_officer"), 0.4)]
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member is None
    assert out["S0"].role == "presiding_officer"
    assert out["S0"].method == "congressional_record"
    assert out["S0"].needs_review is False


def test_aggregate_unresolved_when_no_matches():
    d_turns = [_dturn("S0", 0)]
    out = _aggregate(d_turns, [], min_confidence=0.5)
    assert out["S0"].method == "unresolved"
    assert out["S0"].member is None
    assert out["S0"].total_turns == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -k "confidence or aggregate" -v`
Expected: FAIL — `ImportError: cannot import name 'LabelResolution'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_align.py`:

```python
@dataclass
class LabelResolution:
    speaker_label: str
    member: Optional[CongressMember] = None
    role: Optional[str] = None
    confidence: float = 0.0
    method: str = "unresolved"   # congressional_record | ambiguous | unresolved
    needs_review: bool = False
    matched_turns: int = 0
    total_turns: int = 0


def _confidence(match_fraction: float, vote_fraction: float, mean_overlap: float) -> float:
    """Product of the three 0..1 support factors.

    Isolated on purpose: this formula is expected to be retuned after real-data
    testing, without touching the alignment logic.
    """
    return match_fraction * vote_fraction * mean_overlap


def _aggregate(d_turns, matches, min_confidence: float) -> dict:
    """Aggregate per-label from matched (label, ResolvedSpeaker, overlap) records.

    `matches` is a list of (speaker_label, ResolvedSpeaker, overlap). Majority-vote
    among member identities per label; role-only labels resolve to a role; no
    matches -> unresolved. A tie or sub-gate confidence -> ambiguous/needs_review
    with member=None (member is set only when confident).
    """
    total_by_label = Counter(t.speaker_label for t in d_turns)
    by_label: dict[str, list] = defaultdict(list)
    for label, resolved, ov in matches:
        by_label[label].append((resolved, ov))

    out: dict[str, LabelResolution] = {}
    for label, total in total_by_label.items():
        recs = by_label.get(label, [])
        member_recs = [(r, ov) for r, ov in recs if r.member is not None]
        role_recs = [(r, ov) for r, ov in recs if r.member is None and r.role is not None]

        if member_recs:
            votes = Counter(r.member.bioguide for r, _ in member_recs)
            ranked = votes.most_common()
            winner_bio, winner_votes = ranked[0]
            tie = len(ranked) > 1 and ranked[1][1] == winner_votes
            winner_ovs = [ov for r, ov in member_recs if r.member.bioguide == winner_bio]
            mean_ov = sum(winner_ovs) / len(winner_ovs)
            matched = len(member_recs)
            conf = _confidence(matched / total, winner_votes / matched, mean_ov)
            if not tie and conf >= min_confidence:
                winner_member = next(
                    r.member for r, _ in member_recs if r.member.bioguide == winner_bio)
                out[label] = LabelResolution(
                    speaker_label=label, member=winner_member, confidence=conf,
                    method="congressional_record", needs_review=False,
                    matched_turns=matched, total_turns=total)
            else:
                out[label] = LabelResolution(
                    speaker_label=label, member=None, confidence=conf,
                    method="ambiguous", needs_review=True,
                    matched_turns=matched, total_turns=total)
        elif role_recs:
            role = Counter(r.role for r, _ in role_recs).most_common(1)[0][0]
            out[label] = LabelResolution(
                speaker_label=label, role=role, confidence=len(role_recs) / total,
                method="congressional_record", needs_review=False,
                matched_turns=len(role_recs), total_turns=total)
        else:
            out[label] = LabelResolution(
                speaker_label=label, method="unresolved",
                matched_turns=0, total_turns=total)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -v`
Expected: PASS (18 passed).

- [ ] **Step 5: Commit**

```bash
git add src/crec_align.py tests/test_crec_align.py
git commit -m "feat(crec_align): per-label aggregation with confidence gating"
```

---

### Task 5: Orchestration — `align_crec_to_diarization` + end-to-end tests

**Files:**
- Modify: `src/crec_align.py`
- Test: `tests/test_crec_align.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_crec_align.py` (uses the real Phase-1/2 pipeline for identities):

```python
# add to tests/test_crec_align.py
import json
from pathlib import Path

from src.congress_roster import build_roster
from src.govinfo import CrecTurn
from src.crec_normalize import annotate_turns
from src.crec_align import align_crec_to_diarization

_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"


def _senate_roster():
    return build_roster(json.loads(_FIX.read_text(encoding="utf-8")), "senate")


def test_align_clean_two_members():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "I move to proceed to the healthcare funding bill", "g", 0),
        CrecTurn("Ms. BALDWIN of Wisconsin", "I rise in strong support of the healthcare measure", "g", 1),
    ], _senate_roster())
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill today"),
        _seg(1, "SPEAKER_01", "I rise in strong support of this healthcare measure"),
    ]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    assert out["SPEAKER_00"].member.last_name == "McConnell"
    assert out["SPEAKER_00"].method == "congressional_record"
    assert out["SPEAKER_01"].member.last_name == "Baldwin"


def test_align_role_interjection():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "I move to proceed to the healthcare funding bill", "g", 0),
        CrecTurn("The PRESIDING OFFICER", "Without objection it is so ordered", "g", 1),
        CrecTurn("Ms. BALDWIN of Wisconsin", "I rise in strong support of the healthcare measure", "g", 2),
    ], _senate_roster())
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill"),
        _seg(1, "SPEAKER_09", "without objection it is so ordered"),
        _seg(2, "SPEAKER_01", "I rise in strong support of the healthcare measure"),
    ]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    assert out["SPEAKER_00"].member.last_name == "McConnell"
    assert out["SPEAKER_09"].role == "presiding_officer"
    assert out["SPEAKER_01"].member.last_name == "Baldwin"


def test_align_revise_and_extend_gap_does_not_break_others():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "I move to proceed to the healthcare funding bill", "g", 0),
        CrecTurn("Ms. BALDWIN of Wisconsin", "submitted remarks about unrelated agriculture policy subsidies", "g", 1),
        CrecTurn("Mr. McCONNELL", "I yield the floor on the healthcare funding bill", "g", 2),
    ], _senate_roster())
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill"),
        _seg(1, "SPEAKER_00", "I yield the floor on the healthcare funding bill"),
    ]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    # the Baldwin "revise and extend" CREC turn was never spoken -> free gap;
    # SPEAKER_00 still resolves to McConnell.
    assert out["SPEAKER_00"].member.last_name == "McConnell"


def test_align_unresolved_when_no_overlap():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "healthcare funding appropriations markup", "g", 0),
    ], _senate_roster())
    segs = [_seg(0, "SPEAKER_00", "completely unrelated words about weather sports music")]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    assert out["SPEAKER_00"].method == "unresolved"


def test_align_empty_inputs():
    assert align_crec_to_diarization([], [], min_confidence=0.4) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -k "align_clean_two or role_interjection or revise or unresolved_when or empty_inputs" -v`
Expected: FAIL — `ImportError: cannot import name 'align_crec_to_diarization'`.

- [ ] **Step 3: Write minimal implementation** — append to `src/crec_align.py`:

```python
def align_crec_to_diarization(
    segments,
    annotated_turns,
    *,
    min_confidence: float = 0.5,
) -> dict:
    """Resolve each diarized speaker_label to a CREC identity.

    `segments`: diarized, time-ordered Segments (speaker_label + ASR/caption text).
    `annotated_turns`: [(CrecTurn, ResolvedSpeaker), ...] from Phase 2 annotate_turns.
    Returns {speaker_label: LabelResolution}. Attaches identity only.
    """
    d_turns = _build_diarized_turns(segments)
    d_tokens = [_content_tokens(t.text) for t in d_turns]
    c_tokens = [_content_tokens(ct.text) for ct, _ in annotated_turns]

    pairs = _align(d_tokens, c_tokens)
    matches = []
    for d_idx, c_idx in pairs:
        label = d_turns[d_idx].speaker_label
        resolved = annotated_turns[c_idx][1]
        matches.append((label, resolved, _overlap(d_tokens[d_idx], c_tokens[c_idx])))

    return _aggregate(d_turns, matches, min_confidence)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_crec_align.py -v`
Expected: PASS (23 passed). Then the full suite: `.venv/bin/python -m pytest -q` — confirm no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/crec_align.py tests/test_crec_align.py
git commit -m "feat(crec_align): align_crec_to_diarization orchestration"
```

---

## Self-Review

**Spec coverage:**
- `_build_diarized_turns` (contiguous grouping) — Task 2.
- `_content_tokens` / `_overlap` (overlap coefficient) — Task 1.
- `_align` (monotonic LCS DP, free gaps, match floor) — Task 3.
- `_aggregate` (per-label majority vote, member/role/unresolved, tie & sub-gate → ambiguous/needs_review) + isolated `_confidence` — Task 4.
- `align_crec_to_diarization` orchestration + end-to-end scenarios (clean 1:1, role interjection, revise-and-extend gap, unresolved, empty) — Task 5.
- Deferred per spec (own later plans): Stage-4 `identify.py` wiring + `SpeakerMapping` (Phase 4); many-to-one alignment; essentials linkage. Not in this plan by design.

**Placeholder scan:** No TBD/TODO; every code and test step is complete.

**Type consistency:** `DiarizedTurn(speaker_label, text, index)` used identically across Tasks 2/4/5. `LabelResolution(speaker_label, member, role, confidence, method, needs_review, matched_turns, total_turns)` matches between Task 4 definition and all assertions. `_align(d_tokens, c_tokens) -> list[(int,int)]`, `_aggregate(d_turns, matches, min_confidence)` (matches = list of `(label, ResolvedSpeaker, overlap)`), and `align_crec_to_diarization(segments, annotated_turns, *, min_confidence)` signatures are consistent between definition and call sites. `_confidence(match_fraction, vote_fraction, mean_overlap)` consistent.

**Behavioral check:** `_aggregate` sets `member` only on the confident branch; tie and sub-gate both yield `member=None, method='ambiguous', needs_review=True` (matches the spec's "member only when confident" contract). `_align` float backtracking compares the actual `diag` value against neighbors (no brittle equality on `dp[i][j]`), and the `_MATCH_FLOOR` gate is applied at record time so near-zero overlaps never become pairs.

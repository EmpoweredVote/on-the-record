# CREC Vote Outcome (pass/fail) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture each CREC roll-call vote's real outcome (agreed to / rejected / passed …) and surface it in the published `result` string as `"Agreed to · 236–193"`, falling back to the current `"Yea X, Nay Y"` tally when no outcome parses.

**Architecture:** CREC granule text announces every roll's outcome as a sentence near the end of the roll block — canonically `So the amendment was agreed to.` Parse that phrase in `crec_votes.parse_votes`, normalize it to a display phrase + a `passed` bool on `RollCallVote`, thread both through `models.FloorVote` and `crec_floor.build_floor_votes`, and format the `result` string in `publish._replace_votes`. No DB/schema change — it flows through the existing `meetings.votes.result` column (on-the-record is the sole writer). The web Votes panel renders `result` verbatim, so no `web/` change is needed.

**Tech Stack:** Python 3 (`.venv/bin/python`), pytest, stdlib `re`, dataclasses. Run tests with `.venv/bin/python -m pytest`.

**Grounding (verified 2026-07-19):**
- `tests/fixtures/govinfo/granule_vote_block.txt` is a real House roll block ending in `  So the amendment was agreed to.` then `  The result of the vote was announced as above recorded.`
- `RollCallVote` (`src/crec_votes.py`): `roll_number, question, positions, timestamp`. `parse_votes(text)` splits on `[Roll No. N]` markers and iterates lines within each block `text[start:end]`.
- `FloorVote` (`src/models.py:255`): 9 required fields `roll_number, question, yea, nay, present, not_voting, timestamp, tally_delta, matched` + `to_dict`/`from_dict`. **The existing publish test constructs it POSITIONALLY with 9 args**, so new fields MUST be added last with defaults.
- `build_floor_votes` (`src/crec_floor.py`) builds each `FloorVote(...)` from an `rc` (RollCallVote) with keyword args.
- `_replace_votes` (`src/publish.py:504`) maps `result = f"Yea {fv.yea}, Nay {fv.nay}"`.
- Outcome glyphs chosen by the user: middle dot `·` (U+00B7) and en-dash `–` (U+2013). Files are UTF-8; use `·` / `–` in code for unambiguity.

---

## File Structure

- Modify `src/crec_votes.py` — `RollCallVote` gains `outcome`/`passed`; add `_OUTCOME_RE`, verb sets, `_outcome_of`; wire into `parse_votes`.
- Modify `src/models.py` — `FloorVote` gains `outcome`/`passed` (last, defaulted) + `to_dict`/`from_dict`.
- Modify `src/crec_floor.py` — `build_floor_votes` passes `outcome`/`passed` through.
- Modify `src/publish.py` — `_replace_votes` result string + docstring.
- Tests: `tests/test_crec_votes.py`, `tests/test_crec_floor.py`, `tests/test_publish.py` (and a FloorVote round-trip test — add to `tests/test_crec_floor.py` if no `test_models.py` exists for it).

---

## Task 1: Parse the outcome in `crec_votes`

**Files:**
- Modify: `src/crec_votes.py`
- Test: `tests/test_crec_votes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crec_votes.py`:

```python
def test_parse_outcome_agreed_to_from_fixture():
    text = (FIX / "granule_vote_block.txt").read_text()
    v = parse_votes(text)[0]
    assert v.outcome == "Agreed to"
    assert v.passed is True


def _block(outcome_line: str) -> str:
    return (
        "The question is on agreeing to the amendment.\n"
        "                             [Roll No. 200]\n"
        "                               AYES--1\n"
        "     Adams\n"
        f"  {outcome_line}\n"
        "  The result of the vote was announced as above recorded.\n"
    )


def test_parse_outcome_rejected():
    v = parse_votes(_block("So the amendment was rejected."))[0]
    assert v.outcome == "Rejected"
    assert v.passed is False


def test_parse_outcome_passed():
    v = parse_votes(_block("So the bill was passed."))[0]
    assert v.outcome == "Passed"
    assert v.passed is True


def test_parse_outcome_negated_is_fail():
    v = parse_votes(_block("So the motion was not agreed to."))[0]
    assert v.outcome == "Not agreed to"
    assert v.passed is False


def test_parse_outcome_plural_were_agreed_to():
    v = parse_votes(_block("So the amendments were agreed to."))[0]
    assert v.outcome == "Agreed to"
    assert v.passed is True


def test_parse_outcome_suspend_and_pass_takes_final_verb():
    line = "So (two-thirds being in the affirmative) the rules were suspended and the bill was passed."
    v = parse_votes(_block(line))[0]
    assert v.outcome == "Passed"
    assert v.passed is True


def test_parse_outcome_absent_is_none():
    v = parse_votes(_block("The Clerk announced the tally."))[0]
    assert v.outcome is None
    assert v.passed is None


def test_rollcallvote_outcome_defaults_none():
    v = RollCallVote(1, "q", {"YEA": ["Adams"]})
    assert v.outcome is None and v.passed is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_crec_votes.py -q`
Expected: FAIL — `RollCallVote` has no `outcome`/`passed` attribute.

- [ ] **Step 3: Add fields + outcome parsing**

In `src/crec_votes.py`:

1. Add `outcome`/`passed` to the dataclass (after `timestamp`):

```python
@dataclass
class RollCallVote:
    roll_number: int
    question: str
    positions: dict = field(default_factory=dict)  # "YEA"/"NAY"/"PRESENT"/"NOT_VOTING" -> [surname]
    timestamp: Optional[float] = None               # transcript-relative time of the result announcement (Slice 2)
    outcome: Optional[str] = None                   # display phrase, e.g. "Agreed to"/"Rejected"/"Not agreed to"
    passed: Optional[bool] = None                   # normalized pass/fail; None when no outcome line parses
```

2. Add the outcome matcher + verb sets + `_outcome_of` (place after `_QUESTION_RE`):

```python
# CREC announces each roll's outcome as "So the <subject> was/were [not] <verb>."
# The subject varies (amendment/bill/motion/resolution/…) so we anchor on the verb.
# "So (two-thirds …) the rules were suspended and the bill was passed." → final verb.
_OUTCOME_RE = re.compile(
    r"\b(?:was|were)\s+(not\s+)?"
    r"(agreed to|rejected|passed|adopted|confirmed|ordered|sustained|failed|lost)\b",
    re.I)
_PASS_VERBS = {"agreed to", "passed", "adopted", "confirmed", "ordered", "sustained"}
_FAIL_VERBS = {"rejected", "failed", "lost"}


def _outcome_of(block: str):
    """(display_phrase, passed) from a roll block, or (None, None). Prefers the
    canonical line beginning 'So '; among matches on that line, the last verb wins
    (handles 'the rules were suspended and the bill was passed')."""
    best = None  # (is_so_line, match) — prefer 'So …' lines; otherwise latest match wins
    for line in block.splitlines():
        s = line.strip()
        is_so = s.startswith("So ")
        for m in _OUTCOME_RE.finditer(s):
            if best is None or is_so >= best[0]:  # never let a non-'So' line override a 'So' line
                best = (is_so, m)
    if best is None:
        return None, None
    negated = bool(best[1].group(1))
    verb = best[1].group(2).lower()
    passed = (verb in _PASS_VERBS) != negated  # XOR: negation flips pass/fail
    phrase = ("not " + verb) if negated else verb
    return phrase[0].upper() + phrase[1:], passed
```

3. In `parse_votes`, compute the outcome from the block and set it on the vote. Change the block handling so it captures the block text, and the final append:

```python
    for i, (start, roll) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        block = text[start:end]
        positions: dict = {}
        current: Optional[str] = None
        for line in block.splitlines():
            tm = _TALLY_RE.match(line)
            if tm and _position_of(tm.group(1)):
                current = _position_of(tm.group(1))
                positions.setdefault(current, [])
                continue
            stripped = line.strip()
            if stripped.lower().startswith("the result"):  # end of this tally
                current = None
                continue
            if current and _is_name_line(stripped):
                positions[current].append(stripped)
        outcome, passed = _outcome_of(block)
        votes.append(RollCallVote(
            roll, _question_before(text, start), positions,
            outcome=outcome, passed=passed))
```

> NOTE: iterate over `block.splitlines()` (not `text[start:end].splitlines()` recomputed) so the same `block` feeds `_outcome_of`. Everything else in the loop is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_crec_votes.py -q`
Expected: PASS (existing position/roll tests + the 8 new outcome tests).

- [ ] **Step 5: Commit**

```bash
git add src/crec_votes.py tests/test_crec_votes.py
git commit -m "feat(crec): parse roll-call vote outcome (pass/fail) from granule text"
```

---

## Task 2: Thread outcome through `FloorVote` + `build_floor_votes`

**Files:**
- Modify: `src/models.py`, `src/crec_floor.py`
- Test: `tests/test_crec_floor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crec_floor.py`:

```python
def test_floorvote_outcome_roundtrips():
    from src.models import FloorVote
    fv = FloorVote(438, "q", 236, 193, 0, 9, 102.6, 0, True,
                   outcome="Agreed to", passed=True)
    d = fv.to_dict()
    assert d["outcome"] == "Agreed to" and d["passed"] is True
    back = FloorVote.from_dict(d)
    assert back.outcome == "Agreed to" and back.passed is True


def test_floorvote_outcome_defaults_none_for_positional_construction():
    from src.models import FloorVote
    fv = FloorVote(1, "q", 1, 0, 0, 0, None, None, False)  # 9 positional, no outcome
    assert fv.outcome is None and fv.passed is None
    assert FloorVote.from_dict(fv.to_dict()).outcome is None


def test_build_floor_votes_carries_outcome():
    from types import SimpleNamespace
    from src.crec_votes import RollCallVote
    from src.crec_floor import build_floor_votes, FloorStructure, GranuleVotes
    rc = RollCallVote(438, "On the Smith amendment",
                      {"YEA": ["Adams"], "NAY": []},
                      outcome="Agreed to", passed=True)
    fs = FloorStructure(date="2019-07-11", chamber="house")
    fs.votes = [GranuleVotes(granule=SimpleNamespace(text=""), votes=[rc], members=[])]
    out = build_floor_votes(fs, [])  # no segments -> no timestamp, outcome still carried
    assert out[0].outcome == "Agreed to" and out[0].passed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_crec_floor.py -q`
Expected: FAIL — `FloorVote` has no `outcome`/`passed`; `to_dict` lacks the keys.

- [ ] **Step 3: Add fields to `FloorVote`**

In `src/models.py`, update the `FloorVote` dataclass (add the two fields LAST, with defaults) and its `to_dict`/`from_dict`:

```python
    timestamp: Optional[float]
    tally_delta: Optional[int]
    matched: bool
    outcome: Optional[str] = None   # display phrase, e.g. "Agreed to"; None if unparsed
    passed: Optional[bool] = None   # normalized pass/fail; None if unparsed

    def to_dict(self) -> dict:
        return {
            "roll_number": self.roll_number, "question": self.question,
            "yea": self.yea, "nay": self.nay, "present": self.present,
            "not_voting": self.not_voting, "timestamp": self.timestamp,
            "tally_delta": self.tally_delta, "matched": self.matched,
            "outcome": self.outcome, "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FloorVote":
        return cls(
            roll_number=d["roll_number"], question=d.get("question", ""),
            yea=d.get("yea", 0), nay=d.get("nay", 0),
            present=d.get("present", 0), not_voting=d.get("not_voting", 0),
            timestamp=d.get("timestamp"), tally_delta=d.get("tally_delta"),
            matched=d.get("matched", False),
            outcome=d.get("outcome"), passed=d.get("passed"),
        )
```

- [ ] **Step 4: Pass outcome through `build_floor_votes`**

In `src/crec_floor.py`, in the `FloorVote(...)` construction inside `build_floor_votes`, add the two kwargs (right after `matched=timing.matched,`):

```python
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
            outcome=rc.outcome,
            passed=rc.passed,
        ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_crec_floor.py tests/test_crec_votes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/models.py src/crec_floor.py tests/test_crec_floor.py
git commit -m "feat(crec): carry vote outcome through FloorVote and build_floor_votes"
```

---

## Task 3: Format the `result` string in publish

**Files:**
- Modify: `src/publish.py`
- Test: `tests/test_publish.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_publish.py` (near the existing `_replace_votes` test). It mirrors that test's harness — reuse the same `monkeypatch` fake `execute_values` pattern already in the file:

```python
def test_replace_votes_result_uses_outcome_when_present(monkeypatch):
    from src import publish
    from src.models import Meeting, FloorVote

    captured = {}
    def fake_execute_values(cur, sql, rows):
        captured["rows"] = rows
    monkeypatch.setattr(publish.psycopg2.extras, "execute_values", fake_execute_values)

    class _Cur:
        def __init__(self): self.executes = []
        def execute(self, sql, params=None): self.executes.append((sql, params))

    m = Meeting(meeting_id="m", city=None, date="2019-07-11", floor_votes=[
        FloorVote(438, "On the Smith amendment", 236, 193, 0, 9, 102.6, 0, True,
                  outcome="Agreed to", passed=True),
        FloorVote(500, "On the Jones amendment", 300, 100, 0, 5, None, None, False),  # no outcome
    ])
    publish._replace_votes(_Cur(), m, "uuid-1")
    assert captured["rows"][0][3] == "Agreed to · 236–193"   # outcome · yea–nay
    assert captured["rows"][1][3] == "Yea 300, Nay 100"                # fallback tally
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_publish.py -q -k replace_votes`
Expected: the new test FAILS (result is still `"Yea 236, Nay 193"`); the existing `test_replace_votes*` still PASS.

- [ ] **Step 3: Format the result string**

In `src/publish.py` `_replace_votes`, replace the `result` mapping. Change the row tuple's third element:

```python
    for fv in meeting.floor_votes:
        result = (f"{fv.outcome} · {fv.yea}–{fv.nay}"   # "Agreed to · 236–193"
                  if fv.outcome else f"Yea {fv.yea}, Nay {fv.nay}")
        rows.append((
            meeting_uuid,
            f"Roll No. {fv.roll_number}",        # resolution
            fv.question,                          # description
            result,                               # result (NOT NULL): outcome+tally, else tally
            "recorded",                           # vote_type
            fv.timestamp,                         # numeric seconds (absolutized), NULL if unmatched
        ))
```

Also update the docstring line `store the tally string (the official pass/fail outcome is a later follow-on).` to:

```python
    store the parsed outcome + tally ("Agreed to · 236–193"), falling back to the
    bare tally ("Yea X, Nay Y") when CREC has no parseable outcome line.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_publish.py -q -k replace_votes`
Expected: PASS — the new outcome test and the existing fallback tests (roll 438/500 with `outcome=None` → `"Yea 236, Nay 193"`).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest tests/test_crec_votes.py tests/test_crec_floor.py tests/test_publish.py -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/publish.py tests/test_publish.py
git commit -m "feat(publish): show real vote outcome + tally in meetings.votes.result"
```

---

## Self-Review

**Spec coverage:** parse outcome from CREC text (Task 1) ✓; normalize pass/fail (`passed`) (Task 1) ✓; thread through model + builder (Task 2) ✓; `result` = `"Agreed to · 236–193"` with tally fallback (Task 3) ✓; no DB/schema/web change (renders via existing `result`) ✓.

**Placeholder scan:** none — real fixture, exact anchors, complete code, runnable commands.

**Type consistency:** `RollCallVote.outcome: Optional[str]` / `passed: Optional[bool]` (Task 1) match `FloorVote.outcome`/`passed` (Task 2) and the `rc.outcome`/`rc.passed` passthrough (Task 2) and `fv.outcome`/`fv.yea`/`fv.nay` use (Task 3). New `FloorVote` fields are appended with defaults so the existing positional-construction test (`FloorVote(438, …, True)`, 9 args) still builds and its `outcome=None` → fallback `"Yea 236, Nay 193"` assertion still holds. Outcome glyphs are `·` (·) and `–` (–) consistently in code and test.

**Edge cases covered:** plural `were agreed to`; negation `not agreed to` → fail; suspend-and-pass takes the final verb; absent outcome → `(None, None)` → tally fallback.

# Speaker Name Suggestions — Slice 1 (evidence + candidates + eval) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn spoken self-introductions, chair calls and thank-backs into per-speaker name candidates with tiers, and measure them against Chris's hand-labeled meetings — before any lookup or review change.

**Architecture:** Two pure modules — `src/name_evidence.py` (segments → `Evidence` records, rules E1–E4 with exclusions X1–X3) and `src/name_candidates.py` (`Evidence` → one `Candidate` per speaker label: tier, conflicts X4–X5, title/X6 flag, role guess). A pure scoring module `src/name_suggestion_eval.py` plus a thin CLI `scripts/eval_name_suggestions.py` score candidates against `human_review` gold labels, reusing `src/speaker_id_eval.classify`.

**Tech Stack:** Python 3 (repo `.venv`), `re`, `dataclasses`, pytest. No new dependencies. No network, no DB, no LLM.

**Spec:** `docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md`

## Global Constraints

- Pure modules: `name_evidence`, `name_candidates`, `name_suggestion_eval` import nothing that touches network, DB, files or LLMs.
- No change to `src/identify.py`, review, publish or the pipeline in this slice.
- Always run Python as `.venv/bin/python` (system python lacks deps). In a git worktree the venv lives only in the main checkout: use `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`.
- Tiers: **strong** = E1 and (E2 or E3); **medium** = E1 alone, or E2 and E3; **weak** = E2 alone or E3 alone. E4 never creates a candidate.
- Pre-fill bar per tier: precision ≥ 0.95 and (wrong + hallucination) / predicted ≤ 0.02, over labels where that tier produced a pre-fill name.
- A first-name-only name ("Nitya") is `partial` and is never a pre-fill name.
- A candidate with a conflict is never a pre-fill name.
- Gold = segments with `id_method == "human_review"`; gold names with digits, parentheses, "unknown", "speaker_" or "candidate N" count as no-name.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `src/name_evidence.py` (create) | `Turn`, `Evidence`, `build_turns`, `extract_evidence`; rules E1–E4, X1–X3 |
| `src/name_candidates.py` (create) | `Candidate`, `build_candidates`; tiers, X4, X5, X6 flag, role guess |
| `src/name_suggestion_eval.py` (create) | Gold extraction, name stripping, per-label scoring, summaries, pre-fill bar |
| `scripts/eval_name_suggestions.py` (create) | CLI over `~/CouncilScribe/meetings/*/transcript_named.json` |
| `tests/test_name_evidence.py` (create) | Unit tests for every E and X rule |
| `tests/test_name_candidates.py` (create) | Tiers, conflicts, partial, roles |
| `tests/test_name_suggestion_eval.py` (create) | Scoring, misspelled, junk gold, bar |
| `tests/fixtures/name_suggestions/*.json` (create) | Real excerpts: IGA Jan 14 2026, Bloomington 2026-05-06 |
| `tests/test_name_suggestions_real.py` (create) | End-to-end on the real excerpts |

---

### Task 1: Turns, Evidence type, and E1 self-introductions (with X1, X3)

**Files:**
- Create: `src/name_evidence.py`
- Test: `tests/test_name_evidence.py`

**Interfaces:**
- Consumes: `src.models.Segment` (`segment_id: int, start_time: float, end_time: float, speaker_label: str, text: str`).
- Produces:
  - `Turn(label: str, segment_ids: list[int], words: list[str])` with property `text -> str`.
  - `Evidence(kind: str, label: str, name: str, title: Optional[str], affiliation: Optional[str], quote: str, segment_id: int)` (frozen) with property `partial -> bool` (fewer than 2 name tokens).
  - `build_turns(segments: list[Segment]) -> list[Turn]`
  - `find_self_intros(turns: list[Turn]) -> list[Evidence]` (kind `"E1"`)
  - constants `OFFICE_TITLES: tuple[str, ...]`, `COURTESY_TITLES: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_evidence.py
from __future__ import annotations

from src.models import Segment
from src.name_evidence import build_turns, find_self_intros


def seg(i: int, label: str, text: str) -> Segment:
    return Segment(segment_id=i, start_time=float(i * 10), end_time=float(i * 10 + 9),
                   speaker_label=label, text=text)


FILLER = "and I want to thank the committee for hearing this bill today and for the time."


def test_build_turns_groups_consecutive_labels_and_skips_empty():
    turns = build_turns([seg(0, "A", "Hello there."), seg(1, "A", "More words."),
                         seg(2, "B", ""), seg(3, "B", "Hi."), seg(4, "A", "Back.")])
    assert [t.label for t in turns] == ["A", "B", "A"]
    assert turns[0].segment_ids == [0, 1]
    assert turns[0].text == "Hello there. More words."


def test_my_name_is_with_title_and_affiliation():
    ev = find_self_intros(build_turns([seg(0, "W", "Good afternoon. My name is Rabbi Aaron Spiegel, "
                                                  "I'm with the Indy Multi-Faith Alliance. " + FILLER)]))
    assert len(ev) == 1
    e = ev[0]
    assert (e.kind, e.label, e.name, e.title) == ("E1", "W", "Aaron Spiegel", "Rabbi")
    assert e.affiliation == "the Indy Multi-Faith Alliance"
    assert e.segment_id == 0 and "Spiegel" in e.quote


def test_im_full_name_counts_but_im_sorry_does_not():
    ok = find_self_intros(build_turns([seg(0, "H", "Good evening. I'm Ted Simons. Tonight we continue " + FILLER)]))
    assert [e.name for e in ok] == ["Ted Simons"]
    bad = find_self_intros(build_turns([seg(0, "H", "Oh, I'm sorry. I'm Just going to say " + FILLER)]))
    assert bad == []


def test_im_with_office_title_single_surname():
    ev = find_self_intros(build_turns([seg(0, "S", "I'm Senator Brown, and I represent district fifteen " + FILLER)]))
    assert [(e.name, e.title) for e in ev] == [("Brown", "Senator")]


def test_first_name_only_is_partial():
    ev = find_self_intros(build_turns([seg(0, "W", "Yeah, so my name is Nitya. I'm a council member " + FILLER)]))
    assert ev[0].name == "Nitya" and ev[0].partial


def test_x3_only_first_intro_in_turn_counts():
    ev = find_self_intros(build_turns([seg(0, "W", "My name is Sophie McGowan and I'm here with "
                                                  "Jackson Franklin from the coalition. " + FILLER)]))
    assert [e.name for e in ev] == ["Sophie McGowan"]


def test_x1_mention_of_others_is_ignored():
    ev = find_self_intros(build_turns([seg(0, "W", "As my colleague said, I'm Glad Tidings Lane "
                                                  "is not a name here " + FILLER)]))
    assert ev == []
    ev2 = find_self_intros(build_turns([seg(0, "W", "My wife told me my name is Mud with this crowd " + FILLER)]))
    assert ev2 == []


def test_x2_intro_outside_window_is_ignored():
    long_lead = " ".join(["word"] * 70)
    ev = find_self_intros(build_turns([seg(0, "W", long_lead + " my name is Late Person.")]))
    assert ev == []


def test_short_first_turn_is_skipped_for_first_substantial_turn():
    turns = build_turns([seg(0, "W", "Thank you."), seg(1, "C", "Go ahead."),
                         seg(2, "W", "My name is Lauren Murphy, from Fishers. " + FILLER)])
    assert [e.name for e in find_self_intros(turns)] == ["Lauren Murphy"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_evidence'`

- [ ] **Step 3: Write the implementation**

```python
# src/name_evidence.py
"""Evidence that a spoken name belongs to a diarized speaker.

Pure: segments in, Evidence out. Implements rules E1-E4 and exclusions X1-X3
of docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import Segment

INTRO_WORDS = 60          # E1 window: first N words of the first substantial turn
MIN_TURN_WORDS = 10       # a turn shorter than this is not "substantial"
CALL_WINDOW_WORDS = 25    # E2 window: last N words before a turn change
THANK_WINDOW_WORDS = 10   # E3 window: first N words of the next speaker's turn

OFFICE_TITLES = (
    "Lieutenant Governor", "Attorney General", "Council Member", "Congresswoman",
    "Congressman", "Councilmember", "Councilwoman", "Councilman", "Representative",
    "Commissioner", "Prosecutor", "Treasurer", "Governor", "Senator", "Auditor",
    "Justice", "Sheriff", "Trustee", "Mayor", "Judge", "Rep.", "Sen.",
)
COURTESY_TITLES = (
    "Professor", "Reverend", "Doctor", "Pastor", "Father", "Rabbi", "Chief", "Imam",
    "Miss", "Mrs.", "Rev.", "Mr.", "Ms.", "Dr.",
)
_TITLE = "|".join(re.escape(t) for t in sorted(OFFICE_TITLES + COURTESY_TITLES, key=len, reverse=True))
_TOKEN = r"[A-Z][a-zA-Z'\u2019\-]+"
_NAME_1_3 = rf"{_TOKEN}(?:\s+{_TOKEN}){{0,2}}"
_NAME_2_3 = rf"{_TOKEN}(?:\s+{_TOKEN}){{1,2}}"

# Capitalized words that are not names (sentence starts, courtesies, fillers).
NOT_NAME = {
    "i", "sorry", "just", "not", "going", "gonna", "here", "very", "so", "really", "glad",
    "happy", "proud", "honored", "thrilled", "excited", "running", "sure", "also", "still",
    "actually", "currently", "pleased", "a", "an", "the", "madam", "chair", "chairman",
    "chairwoman", "members", "member", "committee", "everyone", "everybody", "all", "sir",
    "senators", "folks", "yes", "no", "okay", "ok", "thank", "thanks", "good", "morning",
    "afternoon", "evening", "today", "now", "well", "speaker", "president", "colleagues",
    "absolutely", "great", "right", "mud", "tonight", "again", "please", "welcome",
}
# Words just before a name that mean "this is someone else" (X1).
MENTION_CUES = {
    "colleague", "colleagues", "said", "says", "asked", "told", "by", "author", "sponsor",
    "son", "daughter", "wife", "husband", "father", "mother", "brother", "sister", "friend",
}

_R_MY_NAME = re.compile(rf"(?i:\bmy\s+name(?:'s|\s+is)\s+)(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})")
_R_IM_TITLED = re.compile(rf"(?i:\b(?:I'm|I\s+am)\s+)(?P<title>{_TITLE})\s+(?P<name>{_NAME_1_3})")
_R_IM = re.compile(
    rf"(?i:\b(?:I'm|I\s+am)\s+)(?P<name>{_NAME_2_3})"
    r"(?=\s*(?:[.,;!?]|$|(?i:and|with|from|of|on|representing|here|a|an|the)\b))"
)
_R_AFFIL = re.compile(
    r"(?i:\b(?:I'm\s+with|I\s+am\s+with|with|from|representing|on\s+behalf\s+of|here\s+for))\s+"
    r"(?P<org>(?:(?i:the)\s+)?[A-Z][^.,;!?]{1,80})"
)


@dataclass
class Turn:
    label: str
    segment_ids: list[int] = field(default_factory=list)
    words: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass(frozen=True)
class Evidence:
    kind: str                     # "E1" | "E2" | "E3" | "E4"
    label: str                    # speaker label the name is attributed to
    name: str                     # as spoken, title removed, e.g. "Aaron Spiegel"
    title: Optional[str]          # "Rabbi", "Senator", ... or None
    affiliation: Optional[str]    # stated organisation / place, E1 only
    quote: str                    # the exact words the name came from
    segment_id: int               # segment where the quote starts

    @property
    def partial(self) -> bool:
        return len(self.name.split()) < 2


def build_turns(segments: Iterable[Segment]) -> list[Turn]:
    """Consecutive same-label, non-empty segments merged into turns (time order)."""
    turns: list[Turn] = []
    for s in sorted(segments, key=lambda s: s.start_time):
        words = (s.text or "").split()
        if not words:
            continue
        if turns and turns[-1].label == s.speaker_label:
            turns[-1].segment_ids.append(s.segment_id)
            turns[-1].words.extend(words)
        else:
            turns.append(Turn(label=s.speaker_label, segment_ids=[s.segment_id], words=list(words)))
    return turns


def clean_name(raw: str) -> Optional[str]:
    """Trim non-name trailing tokens; None if nothing name-like is left."""
    toks = raw.split()
    while toks and toks[-1].lower().strip("'\u2019") in NOT_NAME:
        toks.pop()
    if not toks or len(toks) > 3 or len(" ".join(toks)) > 40:
        return None
    if any(t.lower() in NOT_NAME for t in toks):
        return None
    return " ".join(toks)


def is_mention(text: str, start: int) -> bool:
    """X1: the 4 words before position `start` mark a mention of someone else."""
    before = re.findall(r"[a-z']+", text[:start].lower())[-4:]
    return any(w in MENTION_CUES for w in before)


def _affiliation_after(text: str, end: int) -> Optional[str]:
    m = _R_AFFIL.search(" ".join(text[end:].split()[:30]))
    return m.group("org").strip() if m else None


def find_self_intros(turns: list[Turn]) -> list[Evidence]:
    """E1: the first self-introduction in each label's first substantial turn."""
    out: list[Evidence] = []
    seen: set[str] = set()
    for turn in turns:
        if turn.label in seen or len(turn.words) < MIN_TURN_WORDS:
            continue
        seen.add(turn.label)
        window = " ".join(turn.words[:INTRO_WORDS])
        matches = []
        for rx in (_R_MY_NAME, _R_IM_TITLED, _R_IM):
            for m in rx.finditer(window):
                name = clean_name(m.group("name"))
                if name and not is_mention(window, m.start()):
                    matches.append((m.start(), m, name))
        if not matches:
            continue
        start, m, name = min(matches, key=lambda x: x[0])  # X3: first intro only
        out.append(Evidence(
            kind="E1", label=turn.label, name=name, title=m.groupdict().get("title"),
            affiliation=_affiliation_after(window, m.end()),
            quote=window[start:m.end() + 60].strip(), segment_id=turn.segment_ids[0],
        ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_evidence.py tests/test_name_evidence.py
git commit -m "feat(names): E1 self-introduction evidence with X1/X3 guards

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: E2 chair calls and E3 thank-backs

**Files:**
- Modify: `src/name_evidence.py` (append)
- Test: `tests/test_name_evidence.py` (append)

**Interfaces:**
- Consumes: `Turn`, `Evidence`, `clean_name`, `is_mention`, `_TITLE`, `_NAME_1_3`, `CALL_WINDOW_WORDS`, `THANK_WINDOW_WORDS` from Task 1.
- Produces: `find_chair_calls(turns: list[Turn]) -> list[Evidence]` (kind `"E2"`, label = the next different speaker), `find_thank_backs(turns: list[Turn]) -> list[Evidence]` (kind `"E3"`, label = the speaker who just finished).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_name_evidence.py
from src.name_evidence import find_chair_calls, find_thank_backs


def test_e2_yes_senator_names_next_speaker():
    turns = build_turns([seg(0, "CHAIR", "Thank you, Senator Garten. Any questions? Yes, Senator Brown."),
                         seg(1, "B", "Thank you, Madam Chair. I support the bill.")])
    ev = find_chair_calls(turns)
    assert [(e.kind, e.label, e.name, e.title) for e in ev] == [("E2", "B", "Brown", "Senator")]


def test_e2_name_then_invitation():
    turns = build_turns([seg(0, "CHAIR", "All right. Senator Garten, would you like to close?"),
                         seg(1, "G", "Yes, thank you.")])
    assert [(e.label, e.name) for e in find_chair_calls(turns)] == [("G", "Garten")]


def test_e2_next_we_hear_from():
    turns = build_turns([seg(0, "CHAIR", "Okay, next we will hear from Rabbi Aaron Spiegel."),
                         seg(1, "W", "Thank you.")])
    assert [(e.label, e.name, e.title) for e in find_chair_calls(turns)] == [("W", "Aaron Spiegel", "Rabbi")]


def test_e2_thank_you_name_is_not_a_call():
    turns = build_turns([seg(0, "CHAIR", "Thank you, Senator Garten."), seg(1, "B", "I have a question.")])
    assert find_chair_calls(turns) == []


def test_e2_x1_bill_by_senator_is_not_a_call():
    # "Senator Koch, please" would be a call, but "by" marks a mention of the author (X1).
    turns = build_turns([seg(0, "CHAIR", "We are hearing the bill by Senator Koch, please hold questions."),
                         seg(1, "W", "Hi.")])
    assert find_chair_calls(turns) == []


def test_e2_only_window_before_turn_change():
    early = "Yes, Senator Brown. " + " ".join(["filler"] * 40)
    turns = build_turns([seg(0, "CHAIR", early), seg(1, "B", "Hello.")])
    assert find_chair_calls(turns) == []


def test_e3_thank_back_names_previous_speaker():
    turns = build_turns([seg(0, "W", "That concludes my testimony on this bill."),
                         seg(1, "CHAIR", "Thank you, Ms. Sample. Any questions for Ms. Sample?")])
    ev = find_thank_backs(turns)
    assert [(e.kind, e.label, e.name, e.title) for e in ev] == [("E3", "W", "Sample", "Ms.")]


def test_e3_madam_chair_is_not_a_name():
    turns = build_turns([seg(0, "CHAIR", "Go ahead."), seg(1, "W", "Thank you, Madam Chair, members.")])
    assert find_thank_backs(turns) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: FAIL — `ImportError: cannot import name 'find_chair_calls'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/name_evidence.py
_CALL_PATTERNS = (
    re.compile(rf"(?i:\b(?:yes|okay|ok|all\s+right|alright|go\s+ahead|please)\b,?\s+)"
               rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})\s*[.?!,]"),
    re.compile(rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3}),\s+"
               r"(?i:would\s+you|you're\s+recognized|you\s+are\s+recognized|please|go\s+ahead|"
               r"the\s+floor\s+is\s+yours|you're\s+up|come\s+on\s+up|welcome)"),
    re.compile(rf"(?i:\b(?:next|now)\b[^.?!]{{0,30}}?\b(?:hear\s+from|have|call(?:\s+up)?|invite|"
               rf"recognize|welcome|is)\s+)(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})"),
    re.compile(rf"(?i:\b(?:welcome|recognize|calling|call\s+up|I\s+see)\s+)"
               rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})"),
)
_R_THANK = re.compile(rf"(?i:\bthank(?:s|\s+you)(?:\s+(?:so|very)\s+much)?,?\s+)"
                      rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})")


def _preceded_by_thanks(text: str, start: int) -> bool:
    return any(w.startswith("thank") for w in re.findall(r"[a-z]+", text[:start].lower())[-3:])


def find_chair_calls(turns: list[Turn]) -> list[Evidence]:
    """E2: the last name called in the words just before a turn change."""
    out: list[Evidence] = []
    for prev, nxt in zip(turns, turns[1:]):
        if prev.label == nxt.label:
            continue
        window = " ".join(prev.words[-CALL_WINDOW_WORDS:])
        best = None
        for rx in _CALL_PATTERNS:
            for m in rx.finditer(window):
                name = clean_name(m.group("name"))
                if not name or is_mention(window, m.start()) or _preceded_by_thanks(window, m.start()):
                    continue
                if best is None or m.start() > best[0]:
                    best = (m.start(), m, name)
        if best:
            start, m, name = best
            out.append(Evidence(kind="E2", label=nxt.label, name=name, title=m.groupdict().get("title"),
                                affiliation=None, quote=window[start:m.end()].strip(),
                                segment_id=prev.segment_ids[-1]))
    return out


def find_thank_backs(turns: list[Turn]) -> list[Evidence]:
    """E3: "thank you, <name>" at the start of the next speaker's turn."""
    out: list[Evidence] = []
    for prev, nxt in zip(turns, turns[1:]):
        if prev.label == nxt.label:
            continue
        window = " ".join(nxt.words[:THANK_WINDOW_WORDS])
        m = _R_THANK.search(window)
        if not m:
            continue
        name = clean_name(m.group("name"))
        if name:
            out.append(Evidence(kind="E3", label=prev.label, name=name, title=m.group("title"),
                                affiliation=None, quote=window[m.start():m.end()].strip(),
                                segment_id=nxt.segment_ids[0]))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_evidence.py tests/test_name_evidence.py
git commit -m "feat(names): E2 chair-call and E3 thank-back evidence

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: E4 caption corroboration and `extract_evidence`

**Files:**
- Modify: `src/name_evidence.py` (append)
- Test: `tests/test_name_evidence.py` (append)

**Interfaces:**
- Consumes: `build_turns`, `find_self_intros`, `find_chair_calls`, `find_thank_backs`.
- Produces: `extract_evidence(segments: list[Segment], captions_text: Optional[str] = None) -> list[Evidence]` — all E1–E3 evidence, plus one `"E4"` record for each distinct (label, full name) whose full name (≥ 2 tokens) appears in `captions_text`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_name_evidence.py
from src.name_evidence import extract_evidence


def test_extract_evidence_combines_rules():
    segs = [seg(0, "CHAIR", "Next we will hear from Ms. Rachel Sample."),
            seg(1, "W", "Thank you. My name is Rachel Sample, I'm with Hoosier Families. " + FILLER),
            seg(2, "CHAIR", "Thank you, Ms. Sample.")]
    kinds = sorted((e.kind, e.label) for e in extract_evidence(segs))
    assert kinds == [("E1", "W"), ("E2", "W"), ("E3", "W")]


def test_e4_from_captions_full_name_only():
    segs = [seg(0, "W", "My name is Rachel Sample, I'm with Hoosier Families. " + FILLER)]
    ev = extract_evidence(segs, captions_text=">> MY NAME IS RACHEL SAMPLE.")
    assert sorted(e.kind for e in ev) == ["E1", "E4"]
    assert extract_evidence(segs, captions_text=">> SOMETHING ELSE.")[0].kind == "E1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: FAIL — `ImportError: cannot import name 'extract_evidence'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/name_evidence.py
def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s.lower()).split())


def extract_evidence(segments: list[Segment], captions_text: Optional[str] = None) -> list[Evidence]:
    """All E1-E3 evidence, plus E4 where source captions contain the full name."""
    turns = build_turns(segments)
    found = find_self_intros(turns) + find_chair_calls(turns) + find_thank_backs(turns)
    if captions_text:
        caps = f" {_norm(captions_text)} "
        seen: set[tuple[str, str]] = set()
        for e in list(found):
            key = (e.label, _norm(e.name))
            if e.partial or key in seen or f" {key[1]} " not in caps:
                continue
            seen.add(key)
            found.append(Evidence(kind="E4", label=e.label, name=e.name, title=e.title,
                                  affiliation=None, quote="(source captions)", segment_id=e.segment_id))
    return found
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py -q`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_evidence.py tests/test_name_evidence.py
git commit -m "feat(names): extract_evidence + E4 caption corroboration

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Candidates — tiers, conflicts, partial names, roles

**Files:**
- Create: `src/name_candidates.py`
- Test: `tests/test_name_candidates.py`

**Interfaces:**
- Consumes: `Evidence`, `OFFICE_TITLES` from `src.name_evidence`; `significant_tokens(name: str) -> list[str]` and `normalize(text: str) -> str` from `src.name_matching`.
- Produces:
  - `Candidate(label: str, name: Optional[str], tier: Optional[str], role: Optional[str], titled: bool, partial: bool, affiliation: Optional[str], evidence: list[Evidence], conflict: Optional[str] = None)` with property `prefill_name -> Optional[str]` (None when conflict, partial or no name).
  - `build_candidates(evidence: list[Evidence], event_kind: Optional[str] = None) -> dict[str, Candidate]`
  - constants `TIER_RANK = {"strong": 3, "medium": 2, "weak": 1}`, `CONFLICT_TWO_LABELS = "name_on_two_labels"`, `CONFLICT_TWO_NAMES = "two_names_one_label"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_candidates.py
from __future__ import annotations

from src.name_candidates import CONFLICT_TWO_LABELS, CONFLICT_TWO_NAMES, build_candidates
from src.name_evidence import Evidence


def ev(kind, label, name, title=None, affiliation=None, quote="q"):
    return Evidence(kind=kind, label=label, name=name, title=title, affiliation=affiliation,
                    quote=quote, segment_id=0)


def test_tiers():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E3", "A", "Sample", "Ms.")])
    assert c["A"].tier == "strong" and c["A"].prefill_name == "Rachel Sample"
    assert build_candidates([ev("E1", "A", "Rachel Sample")])["A"].tier == "medium"
    assert build_candidates([ev("E2", "A", "Sample"), ev("E3", "A", "Sample")])["A"].tier == "medium"
    assert build_candidates([ev("E2", "A", "Rachel Sample")])["A"].tier == "weak"
    assert build_candidates([ev("E3", "A", "Rachel Sample")])["A"].tier == "weak"


def test_e4_alone_makes_no_candidate():
    assert build_candidates([ev("E4", "A", "Rachel Sample")]) == {}


def test_full_name_preferred_over_surname_only():
    c = build_candidates([ev("E2", "A", "Spiegel", "Rabbi"), ev("E1", "A", "Aaron Spiegel", "Rabbi")])
    assert c["A"].name == "Aaron Spiegel" and not c["A"].partial


def test_partial_name_is_not_prefilled():
    c = build_candidates([ev("E1", "A", "Nitya")])
    assert c["A"].partial and c["A"].prefill_name is None and c["A"].name == "Nitya"


def test_x4_same_name_on_two_labels_is_conflict():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E1", "B", "Rachel Sample")])
    assert c["A"].conflict == c["B"].conflict == CONFLICT_TWO_LABELS
    assert c["A"].prefill_name is None


def test_x5_two_strong_names_on_one_label_is_conflict():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E2", "A", "Paul Webster"),
                          ev("E3", "A", "Webster", "Mr.")])
    assert c["A"].conflict == CONFLICT_TWO_NAMES and c["A"].prefill_name is None


def test_x5_weak_second_name_does_not_conflict():
    c = build_candidates([ev("E1", "A", "Rachel Sample"), ev("E3", "A", "Paul Webster")])
    assert c["A"].conflict is None and c["A"].name == "Rachel Sample"


def test_roles():
    assert build_candidates([ev("E2", "A", "Brown", "Senator")])["A"].role == "official"
    assert build_candidates([ev("E2", "A", "Brown", "Senator")])["A"].titled is True
    assert build_candidates([ev("E1", "A", "Ann Lee", affiliation="the Legislative Services Agency")])["A"].role == "staff"
    assert build_candidates([ev("E1", "A", "Ann Lee", affiliation="Hoosier Families")])["A"].role == "presenter"
    assert build_candidates([ev("E1", "A", "Ann Lee")])["A"].role == "public_comment"
    assert build_candidates([ev("E1", "A", "Ann Lee")], event_kind="debate")["A"].role is None


def test_courtesy_title_is_not_office():
    c = build_candidates([ev("E1", "A", "Aaron Spiegel", "Rabbi")])
    assert c["A"].titled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_name_candidates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_candidates'`

- [ ] **Step 3: Write the implementation**

```python
# src/name_candidates.py
"""Per-speaker name candidates from Evidence (tiers, conflicts, role guess).

Pure. Implements tiers, X4, X5 and the X6 "titled" flag of
docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .name_evidence import OFFICE_TITLES, Evidence
from .name_matching import normalize, significant_tokens

TIER_RANK = {"strong": 3, "medium": 2, "weak": 1}
CONFLICT_TWO_LABELS = "name_on_two_labels"
CONFLICT_TWO_NAMES = "two_names_one_label"
CAMPAIGN_KINDS = {"debate", "forum"}
STAFF_CUES = (
    "legislative services agency", "committee counsel", "committee attorney", "fiscal analyst",
    "city staff", "staff attorney", "clerk's office", "legal counsel", "nonpartisan staff",
)


@dataclass
class Candidate:
    label: str
    name: Optional[str]
    tier: Optional[str]
    role: Optional[str]
    titled: bool
    partial: bool
    affiliation: Optional[str]
    evidence: list[Evidence] = field(default_factory=list)
    conflict: Optional[str] = None

    @property
    def prefill_name(self) -> Optional[str]:
        """The name review/eval may pre-fill; None for conflicts and partial names."""
        if self.conflict or self.partial or not self.name:
            return None
        return self.name


def _surname_key(name: str) -> str:
    toks = significant_tokens(name)
    return toks[-1] if toks else normalize(name)


def _tier(kinds: set[str]) -> Optional[str]:
    if "E1" in kinds and ({"E2", "E3"} & kinds):
        return "strong"
    if "E1" in kinds or {"E2", "E3"} <= kinds:
        return "medium"
    if {"E2", "E3"} & kinds:
        return "weak"
    return None


def _best_name(items: list[Evidence]) -> Evidence:
    """Most tokens wins; E1 breaks ties."""
    return max(items, key=lambda e: (len(e.name.split()), e.kind == "E1"))


def _role(titled: bool, affiliation: Optional[str], quotes: str, event_kind: Optional[str]) -> Optional[str]:
    if event_kind in CAMPAIGN_KINDS:
        return None
    if titled:
        return "official"
    hay = f"{affiliation or ''} {quotes}".lower()
    if any(cue in hay for cue in STAFF_CUES):
        return "staff"
    if affiliation:
        return "presenter"
    return "public_comment"


def build_candidates(evidence: list[Evidence], event_kind: Optional[str] = None) -> dict[str, Candidate]:
    """One Candidate per speaker label that has E1-E3 evidence."""
    by_label: dict[str, dict[str, list[Evidence]]] = defaultdict(lambda: defaultdict(list))
    for e in evidence:
        by_label[e.label][_surname_key(e.name)].append(e)

    out: dict[str, Candidate] = {}
    for label, groups in by_label.items():
        scored = []
        for key, items in groups.items():
            tier = _tier({e.kind for e in items})
            if tier:
                scored.append((TIER_RANK[tier], len(items), key, tier, items))
        if not scored:
            continue
        scored.sort(reverse=True)
        _, _, _, tier, items = scored[0]
        best = _best_name(items)
        title = next((e.title for e in items if e.title), None)
        affiliation = next((e.affiliation for e in items if e.kind == "E1" and e.affiliation), None)
        titled = title in OFFICE_TITLES
        cand = Candidate(
            label=label, name=best.name, tier=tier,
            role=_role(titled, affiliation, " ".join(e.quote for e in items), event_kind),
            titled=titled, partial=best.partial, affiliation=affiliation,
            evidence=[e for g in groups.values() for e in g],
        )
        strong_or_medium = [s for s in scored if s[0] >= TIER_RANK["medium"]]
        if len(strong_or_medium) >= 2:  # X5
            cand.conflict = CONFLICT_TWO_NAMES
        out[label] = cand

    by_name: dict[str, list[str]] = defaultdict(list)  # X4
    for label, cand in out.items():
        if cand.name and not cand.partial:
            by_name[normalize(cand.name)].append(label)
    for labels in by_name.values():
        if len(labels) > 1:
            for label in labels:
                out[label].conflict = out[label].conflict or CONFLICT_TWO_LABELS
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_name_candidates.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_candidates.py tests/test_name_candidates.py
git commit -m "feat(names): per-speaker candidates with tiers, X4/X5 conflicts, roles

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Eval scoring module

**Files:**
- Create: `src/name_suggestion_eval.py`
- Test: `tests/test_name_suggestion_eval.py`

**Interfaces:**
- Consumes: `classify(gold_name, predicted_name) -> str` from `src.speaker_id_eval` (returns `correct | safe_null | hallucination | miss | wrong`); `Candidate` from `src.name_candidates`; `normalize` from `src.name_matching`; `Segment` from `src.models`.
- Produces:
  - `gold_labels(meeting: dict) -> dict[str, Optional[str]]` — label → gold name for `human_review` segments (first seen); junk gold → `None`.
  - `strip_names(meeting: dict) -> list[Segment]` — segments with `speaker_name`, `id_method`, `confidence` cleared.
  - `score_meeting(gold: dict[str, Optional[str]], candidates: dict[str, Candidate], event_kind: Optional[str]) -> list[dict]` — one row per gold label: `{label, gold, predicted, hint, tier, outcome, exact, conflict, partial, event_kind}`; `outcome` adds `"misspelled"` (classify says wrong but first names match); `tier` is the candidate tier when a pre-fill name exists, `"hint"` when only a non-pre-fill name exists, else `"none"`.
  - `summarize(rows: list[dict], key: str) -> dict[str, dict]` — per group: counts of the six outcomes, `predicted`, `precision`, `bad_rate`, `misspell_rate`, `exact_rate`, `passes_prefill_bar`.
  - constants `PREFILL_MIN_PRECISION = 0.95`, `PREFILL_MAX_BAD = 0.02`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_name_suggestion_eval.py
from __future__ import annotations

from src.name_candidates import Candidate
from src.name_suggestion_eval import gold_labels, score_meeting, strip_names, summarize


def cand(label, name, tier="medium", partial=False, conflict=None):
    return Candidate(label=label, name=name, tier=tier, role=None, titled=False,
                     partial=partial, affiliation=None, evidence=[], conflict=conflict)


MEETING = {"segments": [
    {"segment_id": 0, "start_time": 0, "end_time": 1, "speaker_label": "A", "text": "hi",
     "speaker_name": "Rachel Sample", "id_method": "human_review"},
    {"segment_id": 1, "start_time": 1, "end_time": 2, "speaker_label": "B", "text": "yo",
     "speaker_name": "Host (Unknown - CRG)", "id_method": "human_review"},
    {"segment_id": 2, "start_time": 2, "end_time": 3, "speaker_label": "C", "text": "x",
     "speaker_name": "Voice Match", "id_method": "voice_profile"},
]}


def test_gold_labels_human_review_only_and_junk_is_none():
    assert gold_labels(MEETING) == {"A": "Rachel Sample", "B": None}


def test_strip_names_clears_identity():
    segs = strip_names(MEETING)
    assert all(s.speaker_name is None and s.id_method is None for s in segs)
    assert [s.text for s in segs] == ["hi", "yo", "x"]


def test_score_outcomes():
    gold = {"A": "Rachel Sample", "B": None, "C": "Peter Berezin", "D": "Ann Lee", "E": "Jo Fox"}
    cands = {"A": cand("A", "Rachel Sample", "strong"), "B": cand("B", "Ted Simons", "weak"),
             "C": cand("C", "Peter Pearson"), "E": cand("E", "Jo", partial=True)}
    rows = {r["label"]: r for r in score_meeting(gold, cands, "council")}
    assert rows["A"]["outcome"] == "correct" and rows["A"]["exact"] and rows["A"]["tier"] == "strong"
    assert rows["B"]["outcome"] == "hallucination"
    assert rows["C"]["outcome"] == "misspelled"
    assert rows["D"]["outcome"] == "miss" and rows["D"]["tier"] == "none"
    assert rows["E"]["outcome"] == "miss" and rows["E"]["tier"] == "hint" and rows["E"]["hint"] == "Jo"


def test_summarize_and_prefill_bar():
    rows = ([{"tier": "strong", "outcome": "correct", "exact": True}] * 99
            + [{"tier": "strong", "outcome": "wrong", "exact": False}])
    s = summarize(rows, "tier")["strong"]
    assert s["predicted"] == 100 and s["precision"] == 0.99 and s["bad_rate"] == 0.01
    assert s["passes_prefill_bar"] is True
    rows.append({"tier": "strong", "outcome": "hallucination", "exact": False})
    rows += [{"tier": "strong", "outcome": "wrong", "exact": False}] * 2
    assert summarize(rows, "tier")["strong"]["passes_prefill_bar"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_name_suggestion_eval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.name_suggestion_eval'`

- [ ] **Step 3: Write the implementation**

```python
# src/name_suggestion_eval.py
"""Score name candidates against human_review gold labels (pure).

Attribution and spelling are kept apart: "misspelled" = right speaker and
first name, different surname spelling (fixed by lookup in slice 2), and it
does not count against the attribution pre-fill bar.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from .models import Segment
from .name_candidates import Candidate
from .name_matching import normalize
from .speaker_id_eval import classify

PREFILL_MIN_PRECISION = 0.95
PREFILL_MAX_BAD = 0.02
OUTCOMES = ("correct", "misspelled", "wrong", "hallucination", "miss", "safe_null")
_GOLD_JUNK = re.compile(r"\d|\(|\bunknown\b|^speaker[_ ]|^candidate\s*\d", re.I)


def gold_labels(meeting: dict) -> dict[str, Optional[str]]:
    gold: dict[str, Optional[str]] = {}
    for s in meeting.get("segments", []):
        if s.get("id_method") != "human_review" or not s.get("speaker_label"):
            continue
        name = s.get("speaker_name")
        gold.setdefault(s["speaker_label"], None if (not name or _GOLD_JUNK.search(name)) else name)
    return gold


def strip_names(meeting: dict) -> list[Segment]:
    segs = [Segment.from_dict(s) for s in meeting.get("segments", [])]
    for s in segs:
        s.speaker_name = None
        s.id_method = None
        s.confidence = None
    return segs


def _first(name: str) -> str:
    toks = normalize(name).split()
    return toks[0] if toks else ""


def score_meeting(gold: dict[str, Optional[str]], candidates: dict[str, Candidate],
                  event_kind: Optional[str]) -> list[dict]:
    rows = []
    for label, gold_name in gold.items():
        cand = candidates.get(label)
        predicted = cand.prefill_name if cand else None
        outcome = classify(gold_name, predicted)
        if outcome == "wrong" and gold_name and predicted and _first(gold_name) == _first(predicted):
            outcome = "misspelled"
        if predicted:
            tier = cand.tier
        elif cand and cand.name:
            tier = "hint"
        else:
            tier = "none"
        rows.append({
            "label": label, "gold": gold_name, "predicted": predicted,
            "hint": cand.name if cand and not predicted else None, "tier": tier,
            "outcome": outcome,
            "exact": bool(outcome == "correct" and normalize(gold_name or "") == normalize(predicted or "")),
            "conflict": cand.conflict if cand else None, "partial": cand.partial if cand else False,
            "event_kind": event_kind,
        })
    return rows


def summarize(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    out = {}
    for name, items in sorted(groups.items()):
        counts = {o: sum(1 for r in items if r["outcome"] == o) for o in OUTCOMES}
        predicted = counts["correct"] + counts["misspelled"] + counts["wrong"] + counts["hallucination"]
        precision = (counts["correct"] + counts["misspelled"]) / predicted if predicted else 0.0
        bad = (counts["wrong"] + counts["hallucination"]) / predicted if predicted else 0.0
        out[name] = {
            "n": len(items), **counts, "predicted": predicted,
            "precision": round(precision, 3), "bad_rate": round(bad, 3),
            "misspell_rate": round(counts["misspelled"] / predicted, 3) if predicted else 0.0,
            "exact_rate": round(sum(1 for r in items if r.get("exact")) / predicted, 3) if predicted else 0.0,
            "passes_prefill_bar": bool(predicted and precision >= PREFILL_MIN_PRECISION
                                       and bad <= PREFILL_MAX_BAD),
        }
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_name_suggestion_eval.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/name_suggestion_eval.py tests/test_name_suggestion_eval.py
git commit -m "feat(names): eval scoring with misspelled outcome and pre-fill bar

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Real-meeting fixtures and end-to-end test

**Files:**
- Create: `tests/fixtures/name_suggestions/in_senate_judiciary_2026_01_14.json`
- Create: `tests/fixtures/name_suggestions/bloomington_2026_05_06.json`
- Test: `tests/test_name_suggestions_real.py`

**Interfaces:**
- Consumes: `extract_evidence`, `build_candidates`, `gold_labels`, `strip_names`, `score_meeting`.
- Produces: fixture files shaped `{"meeting_id": str, "event_kind": str, "segments": [segment dicts incl. human_review speaker_name for gold labels]}`.

- [ ] **Step 1: Build the fixtures from the real meetings**

Each fixture keeps, for each target witness, the witness's first turn plus the turns just before and after it. Gold names stay in the fixture (`speaker_name` + `id_method`) because the test strips them before predicting. These are published public-hearing transcripts.

```bash
.venv/bin/python - <<'EOF'
import json
from pathlib import Path
M = Path.home() / "CouncilScribe/meetings"
OUT = Path("tests/fixtures/name_suggestions"); OUT.mkdir(parents=True, exist_ok=True)
JOBS = {
    "in_senate_judiciary_2026_01_14": ("in-senate-judiciary-2026-01-14",
                                       ["Rachel Sample", "Lauren Murphy", "Aaron Spiegel"]),
    "bloomington_2026_05_06": ("bloomington-city-council-2026-05-06",
                               ["Jordan Evans", "Michael Brahms", "Doug Horne"]),
}
KEEP = ("segment_id", "start_time", "end_time", "speaker_label", "text", "speaker_name", "id_method")
for out_name, (mid, targets) in JOBS.items():
    d = json.load(open(M / mid / "transcript_named.json"))
    segs = sorted(d["segments"], key=lambda s: s["start_time"])
    turns = []
    for s in segs:
        if turns and turns[-1][0] == s["speaker_label"]:
            turns[-1][1].append(s)
        else:
            turns.append((s["speaker_label"], [s]))
    keep_idx = set()
    for target in targets:
        # the witness's first substantial turn (>= 10 words), where E1 looks
        idx = next(i for i, (_, ss) in enumerate(turns)
                   if any(target.split()[-1] in (x.get("speaker_name") or "") for x in ss)
                   and sum(len((x.get("text") or "").split()) for x in ss) >= 10)
        keep_idx |= {idx - 1, idx, idx + 1}
    keep = [{k: s.get(k) for k in KEEP} for i in sorted(keep_idx) if 0 <= i < len(turns) for s in turns[i][1]]
    json.dump({"meeting_id": mid, "event_kind": d.get("event_kind"), "segments": keep},
              open(OUT / f"{out_name}.json", "w"), indent=1, ensure_ascii=False)
    print(out_name, len(keep), "segments")
EOF
```

Expected: two lines, each with a segment count greater than 0. If a `StopIteration` is raised, that witness's gold name is not in the meeting under that surname: print the meeting's `human_review` names, pick the matching spelling, and update `targets`.

- [ ] **Step 2: Write the test**

```python
# tests/test_name_suggestions_real.py
"""End-to-end on real excerpts: Indiana Senate Judiciary 2026-01-14 + Bloomington 2026-05-06."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.name_candidates import build_candidates
from src.name_evidence import extract_evidence
from src.name_suggestion_eval import gold_labels, score_meeting, strip_names

FIX = Path(__file__).parent / "fixtures" / "name_suggestions"
TARGETS = {
    "in_senate_judiciary_2026_01_14.json": ["Rachel Sample", "Lauren Murphy", "Aaron Spiegel"],
    "bloomington_2026_05_06.json": ["Jordan Evans", "Michael Brahms", "Doug Horne"],
}


@pytest.mark.parametrize("fixture", sorted(TARGETS))
def test_witnesses_are_found_on_the_right_label(fixture):
    meeting = json.loads((FIX / fixture).read_text())
    cands = build_candidates(extract_evidence(strip_names(meeting)), meeting["event_kind"])
    rows = {r["gold"]: r for r in score_meeting(gold_labels(meeting), cands, meeting["event_kind"])}
    for target in TARGETS[fixture]:
        gold = next(g for g in rows if g and g.split()[-1] == target.split()[-1])
        assert rows[gold]["outcome"] in ("correct", "misspelled"), rows[gold]


@pytest.mark.parametrize("fixture", sorted(TARGETS))
def test_no_confident_wrong_names_in_excerpt(fixture):
    meeting = json.loads((FIX / fixture).read_text())
    cands = build_candidates(extract_evidence(strip_names(meeting)), meeting["event_kind"])
    rows = score_meeting(gold_labels(meeting), cands, meeting["event_kind"])
    bad = [r for r in rows if r["outcome"] in ("wrong", "hallucination") and r["tier"] in ("strong", "medium")]
    assert bad == []
```

- [ ] **Step 3: Run the test**

Run: `.venv/bin/python -m pytest tests/test_name_suggestions_real.py -q`
Expected: 4 passed. If a target fails, print its turn text and the evidence list: `extract_evidence(...)`. Then fix the **rule** in `src/name_evidence.py` with a new unit test in `tests/test_name_evidence.py` that reproduces the phrase. Never change the target list to make the test pass.

- [ ] **Step 4: Run all name tests**

Run: `.venv/bin/python -m pytest tests/test_name_evidence.py tests/test_name_candidates.py tests/test_name_suggestion_eval.py tests/test_name_suggestions_real.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/name_suggestions tests/test_name_suggestions_real.py src/name_evidence.py tests/test_name_evidence.py
git commit -m "test(names): real-meeting excerpts (IGA Jan 14, Bloomington May 6)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Eval CLI, gold run, and PR

**Files:**
- Create: `scripts/eval_name_suggestions.py`
- Test: `tests/test_eval_name_suggestions_cli.py`

**Interfaces:**
- Consumes: `extract_evidence`, `build_candidates`, `gold_labels`, `strip_names`, `score_meeting`, `summarize`.
- Produces: CLI `scripts/eval_name_suggestions.py [--meetings-dir DIR] [--kinds K ...] [--show-wrong N] [--json PATH]`; function `run(meetings_dir: Path, kinds: Optional[list[str]]) -> list[dict]` (all rows).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_name_suggestions_cli.py
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "name_suggestions"
SCRIPT = Path(__file__).parent.parent / "scripts" / "eval_name_suggestions.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_name_suggestions", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_scores_meetings_in_a_directory(tmp_path):
    for f in FIX.glob("*.json"):
        d = tmp_path / f.stem
        d.mkdir()
        shutil.copy(f, d / "transcript_named.json")
    rows = _load().run(tmp_path, kinds=None)
    assert rows and {"label", "gold", "outcome", "tier", "event_kind"} <= set(rows[0])
    assert _load().run(tmp_path, kinds=["podcast"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_name_suggestions_cli.py -q`
Expected: FAIL — `FileNotFoundError` for `scripts/eval_name_suggestions.py`

- [ ] **Step 3: Write the CLI**

```python
#!/usr/bin/env python
"""Score speaker name suggestions against human_review gold labels.

Spec: docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md

Usage:
  .venv/bin/python scripts/eval_name_suggestions.py
  .venv/bin/python scripts/eval_name_suggestions.py --kinds council forum --show-wrong 20
  .venv/bin/python scripts/eval_name_suggestions.py --json /tmp/name_eval.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.name_candidates import build_candidates  # noqa: E402
from src.name_evidence import extract_evidence  # noqa: E402
from src.name_suggestion_eval import gold_labels, score_meeting, strip_names, summarize  # noqa: E402

_VTT_TIME = re.compile(r"^\d\d:\d\d:\d\d\.\d+ --> .*$", re.M)


def _captions(meeting_dir: Path) -> Optional[str]:
    for name in ("source_captions.vtt", "captions.vtt"):
        p = meeting_dir / name
        if p.exists():
            return _VTT_TIME.sub("", p.read_text(encoding="utf-8", errors="ignore"))
    return None


def run(meetings_dir: Path, kinds: Optional[list[str]]) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(str(meetings_dir / "*" / "transcript_named.json"))):
        try:
            meeting = json.load(open(path))
        except (json.JSONDecodeError, OSError):
            continue
        kind = meeting.get("event_kind")
        if kinds and kind not in kinds:
            continue
        gold = gold_labels(meeting)
        if not gold:
            continue
        mdir = Path(path).parent
        cands = build_candidates(extract_evidence(strip_names(meeting), _captions(mdir)), kind)
        for r in score_meeting(gold, cands, kind):
            r["meeting"] = mdir.name
            c = cands.get(r["label"])
            r["quotes"] = [f"{e.kind}: {e.quote}" for e in (c.evidence if c else [])][:3]
            rows.append(r)
    return rows


def _table(title: str, summary: dict[str, dict]) -> None:
    print(f"\n{title}")
    cols = ("n", "predicted", "correct", "misspelled", "wrong", "hallucination", "miss",
            "safe_null", "precision", "bad_rate", "exact_rate", "passes_prefill_bar")
    print("  " + f"{'group':<18}" + "".join(f"{c[:10]:>11}" for c in cols))
    for group, s in summary.items():
        print("  " + f"{group:<18}" + "".join(f"{str(s[c]):>11}" for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings-dir", default=os.path.expanduser("~/CouncilScribe/meetings"))
    ap.add_argument("--kinds", nargs="*", default=None, help="limit to these event kinds")
    ap.add_argument("--show-wrong", type=int, default=10, help="print N wrong/hallucination rows")
    ap.add_argument("--json", default=None, help="write all rows to this path")
    args = ap.parse_args()

    rows = run(Path(args.meetings_dir), args.kinds)
    print(f"Scored {len(rows)} gold speaker labels from "
          f"{len({r['meeting'] for r in rows})} meetings")
    _table("By tier (pre-fill bar applies to strong/medium/weak):", summarize(rows, "tier"))
    _table("By event kind:", summarize(rows, "event_kind"))

    bad = [r for r in rows if r["outcome"] in ("wrong", "hallucination")]
    print(f"\nWrong / hallucination examples ({len(bad)} total):")
    for r in bad[: args.show_wrong]:
        print(f"  [{r['tier']}] {r['meeting']} {r['label']}: gold={r['gold']!r} predicted={r['predicted']!r}")
        for q in r["quotes"]:
            print(f"      {q[:140]}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_eval_name_suggestions_cli.py -q`
Expected: 1 passed

- [ ] **Step 5: Run the eval on the full gold set and save the output**

Run:
```bash
.venv/bin/python scripts/eval_name_suggestions.py --show-wrong 25 --json /tmp/name_eval_slice1.json | tee /tmp/name_eval_slice1.txt
```
Expected: a "Scored N gold speaker labels from M meetings" line with M ≥ 150, two tables, and wrong examples. Do not tune rules toward this output in this task. Rule changes are a follow-up with their own unit tests, so the result stays an honest baseline.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: all previously passing tests still pass, plus the new ones (baseline before this slice: 3014 passed, 8 skipped).

- [ ] **Step 7: Commit, push, open the PR with the eval output**

```bash
git add scripts/eval_name_suggestions.py tests/test_eval_name_suggestions_cli.py
git commit -m "feat(names): eval CLI for speaker name suggestions vs human_review gold

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin HEAD
```

Open the PR to `main` with title `feat(names): speaker name suggestions — slice 1 (evidence, candidates, eval)`. The body must contain: a link to the spec; the two tables from `/tmp/name_eval_slice1.txt`, pasted in a code block; one line per tier saying whether it passes the pre-fill bar; the 10 most informative wrong examples; and the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Self-review notes (plan author)

- Spec coverage: E1–E4 → Tasks 1–3. X1, X2, X3 → Task 1 (+ X1 in Task 2). X4, X5, X6 flag, tiers, roles → Task 4. Evaluation (gold, two scores, per tier/kind, pre-fill bar) → Tasks 5, 7. Real fixtures (IGA Jan 14, Bloomington) → Task 6. Lookup, review and auto-apply are slices 2–4 and are out of scope here.
- Spec refinement made here: "weak" also covers E3 alone (the spec lists E2 alone). The spec is updated in the same branch to match.
- Partial (first-name-only) names and the "misspelled" outcome are additions the gold data showed are needed. Both are stated in Global Constraints.

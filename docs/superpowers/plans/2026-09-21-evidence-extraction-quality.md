# Evidence Extraction Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the evidence extractor capture only the candidate's literal words (first person incl. "we", or a direct quotation), pull quoted spans out of third-person sources, and capture a fuller contiguous passage + context — with no trimming or editorializing (that is a separate, deferred step).

**Architecture:** Prompt edits to two files — `src/evidence/extract.py` (`_INSTRUCTIONS`, `_SYSTEM`) and `src/evidence/crosscheck.py` (`_INSTRUCTIONS` own_words clause) — plus regression tests on the parse/verify contract. No schema change, no ev-accounts change, no disposition change.

**Tech Stack:** Python 3, pytest. LLMs via `src/llm_providers.get_provider` (OpenRouter).

**Approach note (read before executing):** This is prompt engineering. The *behavioral* change (the model returning `own_words=false` for third-person prose, capturing fuller passages, isolating quoted spans) is produced by an LLM and cannot be asserted in a deterministic unit test. So the unit tests here are **regression guards** on the deterministic parse/verify contract (they are not red-green), and the prompt's real behavior is validated in **Task 3, the human-gated Bass artifact run**. Do not invent unit tests that claim to assert LLM output; do not weaken existing tests.

## Global Constraints

- **Own-words rule (Chris, 2026-09-21):** a quote is acceptable only if it is the candidate's literal words — **first person (I / we / my / our / us)** OR a sentence **directly quoted** from the candidate (quotation marks, or "…," she said). Third-person description of the candidate ("Bass will…", "the Mayor has…") is **not** own-words. This is a **text-voice test, applied per quote — never a per-domain block.**
- **Capture only — no editorializing.** Capture the full contiguous verbatim passage as-is. Do **not** trim, shorten, cut, add "…", or reword. Condensation is a separate, later step governed by `docs/quote-curation/PRINCIPLES.md` + `EDITORIAL.md` and the `publish-quotes` skill.
- **VERBATIM.** The verbatim gate still verifies each quote is an exact substring of the full fetched page; the quote is always the candidate's exact words.
- **No schema change; no ev-accounts change; no change to `chunk_text`, `extract_quotes`, disposition, or the verbatim-gate logic.** Only the two prompt strings change (plus tests).
- LLM models are OpenRouter: extractor `haiku-or`, crosschecker `gemini-flash`, judge `deepseek`.
- Run tests + the runner with the MAIN checkout venv: `~/Documents/GitHub/on-the-record/.venv/bin/python`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/evidence/extract.py` — MODIFY `_INSTRUCTIONS` and `_SYSTEM` (prompt only). Do not touch `chunk_text`, `parse_extract`, `_to_candidate`, `extract_quotes`.
- `src/evidence/crosscheck.py` — MODIFY the `own_words` line in `_INSTRUCTIONS`. Do not touch parsing.
- `tests/test_evidence_extract.py` — EXTEND: parse round-trip for a fuller passage + paragraph-length context; two-quotes-from-one-passage; `is_own_words=false` round-trips. (Locate first; it exists.)
- Cross-check / verify tests — EXTEND the existing files if present (`tests/test_evidence_crosscheck.py`, `tests/test_evidence_verify.py`); otherwise add cases to the nearest existing evidence test file. Locate with `ls tests/ | grep -iE "crosscheck|verify"` before writing.

---

### Task 1: Extractor prompt — own-words, full capture, isolate quoted spans, fuller context, split

**Files:**
- Modify: `src/evidence/extract.py` (`_INSTRUCTIONS`, `_SYSTEM`)
- Test: `tests/test_evidence_extract.py`

**Interfaces:** No signature changes. `build_extract_prompt`, `parse_extract`, `extract_quotes` keep their contracts; only the instruction/system strings change.

- [ ] **Step 1: Add regression-guard tests (they pass before and after — they guard the shaping, not the prompt)**

```python
# tests/test_evidence_extract.py  (append; reuse the existing FakeProvider / imports)
import json
from src.evidence.extract import parse_extract

def test_parse_extract_keeps_full_passage_and_paragraph_context():
    long_text = ("When I'm mayor, we will build 40,000 units of housing by cutting permit "
                 "timelines, converting motels to housing, and funding bridge loans. We will "
                 "not let vouchers go unused.")
    ctx = ("At the forum she was asked about housing. " + long_text + " She took questions after.")
    raw = json.dumps({"quotes": [{"text": long_text, "context": ctx, "issue": "housing",
                                  "is_own_words": True, "is_primary_venue": True}]})
    out = parse_extract(raw)
    assert len(out) == 1
    assert out[0].text == long_text            # full passage kept intact, not trimmed
    assert out[0].context == ctx               # fuller paragraph context round-trips

def test_parse_extract_two_quotes_from_one_passage():
    raw = json.dumps({"quotes": [
        {"text": "We will build 40,000 units.", "issue": "housing", "is_own_words": True},
        {"text": "We will hire 250 more officers.", "issue": "policing", "is_own_words": True}]})
    out = parse_extract(raw)
    assert [c.issue for c in out] == ["housing", "policing"]

def test_parse_extract_carries_is_own_words_false():
    raw = json.dumps({"quotes": [{"text": "Bass will expand shelters.", "issue": "homelessness",
                                  "is_own_words": False}]})
    out = parse_extract(raw)
    assert out[0].is_own_words is False
```

- [ ] **Step 2: Run them (green now — they guard the contract)**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -v`
Expected: PASS (these lock the shaping the prompt edit relies on).

- [ ] **Step 3: Replace `_SYSTEM` and `_INSTRUCTIONS` in `src/evidence/extract.py`**

Replace `_SYSTEM` with:

```python
_SYSTEM = ("You extract a politician's OWN verbatim words — first person, or sentences "
           "directly quoted from them — that state a view on an issue. Never paraphrase, "
           "reword, or trim. Respond with ONLY the requested JSON.")
```

Replace `_INSTRUCTIONS` with:

```python
_INSTRUCTIONS = """From the SOURCE below, extract statements by {name} that express a view on a
policy issue. Rules:
- VERBATIM only — copy the exact words from the SOURCE; never summarize, reword, or paraphrase.
- OWN WORDS ONLY. Extract a statement only if it is in {name}'s own voice — FIRST PERSON
  (I, we, my, our, us) — OR a sentence directly quoted from {name} (in quotation marks, or
  attributed like "…," {name} said). A THIRD-PERSON description of {name} ("{name} will…",
  "the Mayor has…", "she believes…") is NOT {name}'s words: set is_own_words false for it.
- If the SOURCE is written about {name} in the third person but DIRECTLY QUOTES {name}, extract
  the QUOTED sentence(s) as `text` (that is own words) — not the surrounding paraphrase.
- Capture the FULL coherent CONTIGUOUS passage for ONE stance. Where {name} states HOW they would
  act (the mechanism/lever: e.g. build shelters, enforce encampment laws, expand services), include
  the adjacent sentences that carry it. Capture as many sentences as the complete stance takes.
  Do NOT trim, shorten, cut, abbreviate, or add "…" — copy the unbroken run of the SOURCE as-is.
  (Condensing to the essence is a separate later step; here, capture faithfully and in full.)
- Keep `text` CONTIGUOUS — one unbroken run of the SOURCE; never stitch together non-adjacent
  passages, and keep ONE stance per quote. A passage that covers two distinct stances becomes TWO
  separate quotes.
- issue = a short lowercase topic label (e.g. "housing", "homelessness", "policing").
- is_own_words: apply the OWN WORDS rule above.
- is_primary_venue: true if the SOURCE is {name}'s own venue (their site/official page/op-ed) or an
  outlet's OWN interview/Q&A with them; false if the SOURCE is reporting on a separate event where
  {name} spoke.
- If is_primary_venue is false and the SOURCE names a spoken event ({name} said X at a
  debate/town-hall/interview/podcast), set reported_event to "<event>, <date>" and, if the SOURCE
  links the primary (e.g. a YouTube URL), set primary_handle to it.
Return JSON: {{"quotes": [{{"text","context","issue","date","setting","is_own_words",
"is_primary_venue","reported_event","primary_handle"}}]}}.
context = the surrounding paragraph(s) from the SOURCE — enough that a reader can see the full
setting of the quote and vet it.

SOURCE:
{text}
"""
```

- [ ] **Step 4: Run the full evidence-extract suite**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_extract.py -v`
Expected: PASS (the new prompt still formats via `.format(name=…, text=…)`; all parse tests green). Confirm `build_extract_prompt` still substitutes `{name}` and `{text}` with no stray `{}` KeyError.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/extract.py tests/test_evidence_extract.py
git commit -m "feat(evidence): extractor captures own-words full passages, not third-person paraphrase

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Cross-checker own-words alignment + verify capture-faithfulness tests

**Files:**
- Modify: `src/evidence/crosscheck.py` (the `own_words` line in `_INSTRUCTIONS`)
- Test: `tests/test_evidence_crosscheck.py` and `tests/test_evidence_verify.py` (locate; extend or add cases)

**Interfaces:** No signature changes. `build_crosscheck_prompt`/`parse_crosscheck`/`crosscheck` keep their contracts; only the own_words instruction line changes.

- [ ] **Step 1: Locate the test files**

Run: `ls tests/ | grep -iE "crosscheck|verify"`
Extend whichever exist; if a verify test file is absent, add the verify cases to the nearest evidence test file.

- [ ] **Step 2: Add verify capture-faithfulness tests (guard the invariant)**

```python
# in the verify test file — import the real gate
from src.evidence.verify import verbatim_ok

def test_verbatim_ok_contiguous_full_passage_passes():
    page = ("Intro. When I'm mayor, we will build 40,000 units by cutting permit timelines "
            "and converting motels to housing. Later text.")
    quote = ("When I'm mayor, we will build 40,000 units by cutting permit timelines "
             "and converting motels to housing.")
    assert verbatim_ok(quote, page) is True

def test_verbatim_ok_reworded_quote_fails():
    page = "When I'm mayor, we will build 40,000 units by cutting permit timelines."
    reworded = "As mayor she plans to construct 40,000 homes by streamlining permits."
    assert verbatim_ok(reworded, page) is False
```

- [ ] **Step 3: Run them (green now — verify already enforces substring)**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/ -k "verify" -v`
Expected: PASS. If the contiguous-passage test unexpectedly FAILS (e.g. whitespace/smart-quote normalization), STOP and report — that is a real capture/gate issue to resolve, not a test to weaken.

- [ ] **Step 4: Edit the cross-checker `own_words` line**

In `src/evidence/crosscheck.py`, replace the `own_words` bullet in `_INSTRUCTIONS` with:

```
- own_words: are these literally {name}'s OWN words in the SOURCE — FIRST PERSON (I/we/my/our/us),
  or a sentence directly quoted from {name}? A third-person description of {name} ("{name} will…",
  "the Mayor has…", "she believes…") is NOT own words: answer false.
```

- [ ] **Step 5: Run the cross-check + verify suites**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/ -k "crosscheck or verify" -v`
Expected: PASS (parse contract unchanged; the edit is prompt text). Confirm `build_crosscheck_prompt` still formats with no stray `{}`.

- [ ] **Step 6: Commit**

```bash
git add src/evidence/crosscheck.py tests/
git commit -m "feat(evidence): cross-checker rejects third-person as own-words; verify locks faithful capture

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Human-gated validation — re-run Bass, before/after (no prod commit)

**This task is not TDD; it spends LLM + network and needs Chris. Do NOT run it automatically — present the results to Chris.**

**Files:** none changed (artifacts to a Bass subdir under `docs/superpowers/spikes/`).

- [ ] **Step 1: Re-run Bass with the new prompts (render on), artifacts only**

```bash
export OPENROUTER_API_KEY=$(grep -E '^OPENROUTER_API_KEY=' ~/Documents/GitHub/on-the-record/.env.local | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
  --candidate 21c9e711-fb18-4afb-884f-08acd2b598ba \
  --env-file ~/Documents/GitHub/ev-accounts/backend/.env \
  --out docs/superpowers/spikes/2026-09-19-evidence-trust-core/bass-v2
```

- [ ] **Step 2: Compare against the prior Bass run and report to Chris**

From `bass-v2/evidence_items.json`, report:
- Green count and whether any green item is third-person ("Bass will…") — expect **none** now.
- At least one first-person quote recovered from a **news/press** source (a domain other than ballotpedia/mayor.lacity.gov) — evidence the quoted-span isolation works.
- Passages are fuller / fewer fragments, each with a paragraph-length `context`.
- Anything that regressed (e.g. a real first-person quote wrongly dropped).

Do NOT commit to prod. Chris decides whether/when to re-commit Bass (which makes new rows — see the spec's deferred note) and when to start the separate editorializing step.

---

## Self-Review

**Spec coverage:**
- Own-words = first person (incl. "we") or direct quote; third-person rejected → Task 1 (`_INSTRUCTIONS` own-words rule + `_SYSTEM`) and Task 2 (cross-checker own_words line). ✓
- Isolate quoted spans in third-person sources → Task 1 (explicit rule). ✓
- Capture full contiguous passage, no trimming/ellipsis/rewording → Task 1 (removed the "trim filler / …" instruction; added "capture in full, do not trim/cut/add …"). ✓
- Fuller context → Task 1 (context = surrounding paragraph(s)). ✓
- Split by stance → Task 1 (one stance per quote; two stances → two quotes). ✓
- Editorializing deferred → not implemented anywhere; called out in Global Constraints. ✓
- Text test never a domain block → Global Constraints; no domain logic added. ✓
- Verbatim gate unchanged, capture faithful → Task 2 verify tests. ✓
- Validation artifact-based, no prod commit → Task 3. ✓

**Placeholder scan:** none — the two prompt strings are given verbatim; test code is complete; the size of "fuller context" is a bounded prompt instruction, not a magic number.

**Type consistency:** no signatures change; `parse_extract`/`parse_crosscheck`/`verbatim_ok` are used with their existing shapes.

**Honesty check:** the plan does not fake red-green for the LLM prompt; unit tests are labeled regression guards and the behavioral proof is the Task 3 live run. Global Constraints forbid weakening tests.

## Execution Handoff

Two execution options:
1. **Subagent-Driven (recommended)** — fresh subagent per task, review after each, broad final review.
2. **Inline Execution** — execute in this session with checkpoints.

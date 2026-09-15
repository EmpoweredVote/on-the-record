# VOTE411 pointer-only interim lane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make VOTE411 usable as a *lead* (never a cited source) inside the race-pipeline while an LWV data license is pending — by adding a machine guard that blocks VOTE411 URLs as quote sources, and documenting the human pointer procedure.

**Architecture:** No new services or schema. One new deterministic audit check (`pointer-only-source`) mirroring the existing `unquotable-source`/`scorecard-source` pattern, plus documentation edits to the `audit-quotes` and `race-pipeline` skills. One doctrine line lands in the separate `essentials` repo.

**Tech Stack:** Python 3 (on-the-record `.venv`), pytest. Pure-function checks in `scripts/checks.py`. Markdown skill docs.

**Design source:** `docs/superpowers/specs/2026-09-12-vote411-pointer-lane-design.md`.

## Global Constraints

Every task inherits these (the spec's compliance spine — §3):

- No agent, subagent, script, or scheduled job may fetch `vote411.org` or `*.thevoterguide.org`. VOTE411 is read only by a human in a normal browser. Do not add any automated fetch of these hosts.
- No VOTE411 answer text is stored anywhere (DB, notes, `why`, `editor_note`, code, tests, fixtures) — verbatim or paraphrased. Test fixtures use invented text, never real VOTE411 answers.
- `vote411.org` and `thevoterguide.org` are never a valid `source_url`. Remedy is *pointer-only* (source from the candidate's own materials), NOT "re-attribute to an original."
- Production DB: additive only. This plan writes no DB rows; it changes skills/tests only.
- Git hygiene (repo `chrisandrewsedu`): stage files by explicit path — never `git add -A`/`.`. Never stage `.env*` or secrets. Work stays on branch `docs/vote411-pointer-lane` (already created) for the on-the-record tasks; Task 5 is a SEPARATE repo + branch.

**Repo root (on-the-record):** `/Users/chrisandrews/Documents/GitHub/on-the-record` (referred to below as `$R`).
**Audit skill dir:** `$R/.claude/skills/audit-quotes` (referred to as `$A`).
**Run audit tests from `$A`** (the tests import `from scripts.checks import …`, so cwd must be `$A`):
`cd $A && $R/.venv/bin/python -m pytest tests/test_checks.py tests/test_verify_source.py -v`

---

### Task 1: `pointer-only-source` audit check

**Files:**
- Modify: `$A/scripts/checks.py` — add `POINTER_ONLY_SOURCE` regex (near the other source regexes, after line 33), add `check_pointer_only_source`, register it in `QUOTE_CHECKS` (line ~190).
- Test: `$A/tests/test_checks.py` — add cases using the existing `row(**kw)` helper.

**Interfaces:**
- Produces: `POINTER_ONLY_SOURCE` (compiled `re.Pattern`) and `check_pointer_only_source(r) -> Optional[Finding]`, both imported by Task 2. `Finding` fields match the existing checks (`check_id`, `level`, `severity`, `fix_class`, `principle`, `what`, `suggested_fix`).

- [ ] **Step 1: Write the failing tests**

Add to `$A/tests/test_checks.py`. First extend the import at the top of the file to include the new names:

```python
from scripts.checks import (
    check_note_quality, check_deid_present, check_trailing_ellipsis,
    check_partisan_tell_in_blind, check_source_tier, check_invalid_source,
    check_unquotable_source, check_scorecard_source, check_stance_label,
    check_pointer_only_source,
    topic_live_count, topic_min_candidates, STANCE_LABEL_MAX_WORDS,
)
```

Then append these tests:

```python
def test_pointer_only_source_vote411_flagged():
    f = check_pointer_only_source(row(source_url="https://www.vote411.org/ballot",
                                      source_name="www.vote411.org"))
    assert f is not None and f.check_id == "pointer-only-source"
    assert f.severity == "high" and f.fix_class == "decision-required"

def test_pointer_only_source_thevoterguide_flagged():
    for u in ("https://api.thevoterguide.org/v1/race?districtId=15",
              "https://onyourballot.vote411.org/x.do"):
        f = check_pointer_only_source(row(source_url=u))
        assert f is not None and f.check_id == "pointer-only-source", u

def test_pointer_only_source_is_pointer_not_reattribute():
    # Must NOT collapse into invalid-source (that remedy is "re-attribute"; this one is "pointer only").
    r = row(source_url="https://www.vote411.org/ballot")
    assert check_invalid_source(r) is None
    fix = check_pointer_only_source(r).suggested_fix.lower()
    assert "pointer" in fix or "own" in fix
    assert "do not paraphrase" in fix

def test_pointer_only_source_youtube_not_flagged():
    assert check_pointer_only_source(row(source_url="https://youtu.be/x?t=1s")) is None

def test_pointer_only_source_registered():
    from scripts.checks import QUOTE_CHECKS
    assert check_pointer_only_source in QUOTE_CHECKS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $A && $R/.venv/bin/python -m pytest tests/test_checks.py -k pointer_only -v`
Expected: FAIL — `ImportError: cannot import name 'check_pointer_only_source'`.

- [ ] **Step 3: Implement the regex and check**

In `$A/scripts/checks.py`, add after the `SCORECARD_SOURCE` block (after line 33):

```python
# VOTE411 / thevoterguide.org — the League of Women Voters' candidate-questionnaire platform.
# The answers are the candidate's own words (a real primary source), but LWV's terms bar
# reproduction and automated access without written permission (vote411.org/legal). Until a
# license exists, these are POINTER-ONLY: use them to find where a candidate stated a position,
# then cite the candidate's own materials. Unlike an aggregator there is often no other page to
# re-attribute to (the answer is original to VOTE411), so the remedy is not "re-attribute".
# See docs/superpowers/specs/2026-09-12-vote411-pointer-lane-design.md.
POINTER_ONLY_SOURCE = re.compile(r"vote411\.org|thevoterguide\.org", re.I)
```

Add the check function next to `check_scorecard_source` (after line 157):

```python
def check_pointer_only_source(r) -> Optional[Finding]:
    url = r.get("source_url") or ""
    if not POINTER_ONLY_SOURCE.search(url):
        return None
    return Finding(check_id="pointer-only-source", level="quote", quote_id=r["id"], topic_key=r["topic_key"],
                   race_id=r["race_id"], candidate=r["candidate"],
                   principle="VOTE411 answers are permission-gated: a pointer, never a cited source",
                   severity="high", fix_class="decision-required",
                   what=f"Source is VOTE411 / thevoterguide.org: {url}. LWV terms bar reproducing this without written permission, so it cannot be a cited source.",
                   suggested_fix="Pointer only: source the position from the candidate's OWN materials (campaign site, press release, their own post/video) and re-source to it; deselect from live until then. If the position appears only on VOTE411, the candidate is absent on this topic — do not paraphrase the VOTE411 answer.")
```

Register it by adding `check_pointer_only_source` to the `QUOTE_CHECKS` list (line ~190):

```python
QUOTE_CHECKS = [check_note_quality, check_deid_present, check_trailing_ellipsis,
                check_partisan_tell_in_blind, check_source_tier, check_invalid_source,
                check_unquotable_source, check_scorecard_source, check_pointer_only_source,
                check_stance_label]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $A && $R/.venv/bin/python -m pytest tests/test_checks.py -v`
Expected: PASS (new `pointer_only` tests green; all pre-existing tests still green).

- [ ] **Step 5: Commit**

```bash
git -C $R add .claude/skills/audit-quotes/scripts/checks.py .claude/skills/audit-quotes/tests/test_checks.py
git -C $R commit -m "feat(audit-quotes): pointer-only-source check blocks VOTE411 as a source_url

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: skip VOTE411 URLs in source verification

`verify_source.check_source` re-fetches `source_url` to confirm the quote string. It already returns `None` for aggregator/quiz URLs (nothing to fetch). VOTE411 URLs must be skipped too — the `pointer-only-source` check owns them, and we must never fetch these hosts (Global Constraints).

**Files:**
- Modify: `$A/scripts/verify_source.py` — import `POINTER_ONLY_SOURCE` (line 40) and add it to the skip condition (line ~581).
- Test: `$A/tests/test_verify_source.py` — extend the "does not fetch" test.

**Interfaces:**
- Consumes: `POINTER_ONLY_SOURCE` from `scripts.checks` (Task 1).

- [ ] **Step 1: Write the failing test**

In `$A/tests/test_verify_source.py`, extend `test_check_source_does_not_fetch_aggregator_sources` by adding these two assertions inside it (reuse the existing `boom` fetcher and `_written_row` helper):

```python
    assert check_source(None, _written_row("whatever", "https://www.vote411.org/ballot"),
                        fetch_page=boom) is None
    assert check_source(None, _written_row("whatever", "https://api.thevoterguide.org/v1/race"),
                        fetch_page=boom) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd $A && $R/.venv/bin/python -m pytest tests/test_verify_source.py::test_check_source_does_not_fetch_aggregator_sources -v`
Expected: FAIL — `AssertionError: should not have fetched https://www.vote411.org/ballot` (the code currently tries to fetch it).

- [ ] **Step 3: Implement the skip**

In `$A/scripts/verify_source.py`, change the import on line 40 from:

```python
from scripts.checks import AGGREGATOR_SOURCE, QUIZ_SOURCE
```
to:
```python
from scripts.checks import AGGREGATOR_SOURCE, QUIZ_SOURCE, POINTER_ONLY_SOURCE
```

Then change the skip condition (line ~581) from:

```python
    if AGGREGATOR_SOURCE.search(url) or QUIZ_SOURCE.search(url):
        return None                                  # invalid-source / unquotable-source own these
```
to:
```python
    if AGGREGATOR_SOURCE.search(url) or QUIZ_SOURCE.search(url) or POINTER_ONLY_SOURCE.search(url):
        return None                                  # invalid-source / unquotable-source / pointer-only-source own these
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $A && $R/.venv/bin/python -m pytest tests/test_verify_source.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C $R add .claude/skills/audit-quotes/scripts/verify_source.py .claude/skills/audit-quotes/tests/test_verify_source.py
git -C $R commit -m "feat(audit-quotes): never fetch VOTE411/thevoterguide during source verification

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: document the check in CHECKS.md

**Files:**
- Modify: `$A/CHECKS.md` — add a `pointer-only-source` row to the checks table and a short prose paragraph in the source-checks section (near the `invalid-source` / `unquotable-source` explanations, around lines 57–72).

- [ ] **Step 1: Add the table row**

In the checks table (the row for `invalid-source` is around line 57), add a sibling row:

```markdown
| `pointer-only-source` | quote | VOTE411 / thevoterguide.org answers are permission-gated — a pointer, never a cited source; **source from the candidate's own materials** | high | decision-required |
```

- [ ] **Step 2: Add the prose explanation**

In the section that distinguishes `invalid-source` / `unquotable-source` / `scorecard-source` (around lines 66–72), add:

```markdown
- **`pointer-only-source` — VOTE411 / thevoterguide.org; permission-gated, source elsewhere.**
  The answers are the candidate's own words, but the League of Women Voters' terms
  (vote411.org/legal) bar reproducing them without written permission. So VOTE411 is a
  *pointer*: read it (as a human, in a browser) to learn a candidate's position, then cite
  the candidate's own material. Unlike `invalid-source`, the fix is NOT "re-attribute" — the
  answer is usually original to VOTE411 with no other page to point to; if the position lives
  only on VOTE411, leave the candidate absent, and never paraphrase the VOTE411 answer. This
  guard lifts if/when a written League data license lands. See
  `docs/superpowers/specs/2026-09-12-vote411-pointer-lane-design.md`.
```

- [ ] **Step 3: Verify the doc mentions the check**

Run: `grep -n "pointer-only-source" $A/CHECKS.md`
Expected: at least two lines (table row + prose).

- [ ] **Step 4: Commit**

```bash
git -C $R add .claude/skills/audit-quotes/CHECKS.md
git -C $R commit -m "docs(audit-quotes): document pointer-only-source check

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: race-pipeline "VOTE411 as a pointer" subsection

**Files:**
- Modify: `$R/.claude/skills/race-pipeline/SKILL.md` — add a subsection under the sourcing guidance (after the `needs_quotes → quotes_staged` transition, before `### quotes_staged → published`).

- [ ] **Step 1: Add the subsection**

Insert:

```markdown
#### VOTE411 as a pointer (interim — no LWV license yet)

VOTE411 candidate-questionnaire answers are the candidate's own words (a tier-2 source
in principle), but LWV's terms bar reproducing them or fetching them programmatically
without written permission. Until a license lands, use VOTE411 as a **pointer only**:

- A **human** opens the race's VOTE411 guide in a normal browser. Do NOT delegate this to
  an agent/subagent and do NOT fetch `vote411.org` or `*.thevoterguide.org` from code.
- Capture only facts: confirm the ballot line-up (against the SOS list), read each
  candidate's own campaign URL from the guide's "Website" field into
  `race_candidates.website_url`, and note which Compass topics they address.
- **Store no VOTE411 answer text** — not in `notes`, `why`, or `editor_note`.
- Then source quotes from each candidate's OWN materials (campaign site, press release,
  their own post/video) per §5, and run publish-quotes → audit-quotes as normal.
- `vote411.org` / `thevoterguide.org` are never a `source_url` — the `pointer-only-source`
  audit check enforces this. If a position appears only on VOTE411, the candidate is absent
  on that topic; never paraphrase the VOTE411 answer to fill the gap.

Design: `docs/superpowers/specs/2026-09-12-vote411-pointer-lane-design.md`.
```

- [ ] **Step 2: Verify the subsection landed**

Run: `grep -n "VOTE411 as a pointer" $R/.claude/skills/race-pipeline/SKILL.md`
Expected: one line.

- [ ] **Step 3: Commit**

```bash
git -C $R add .claude/skills/race-pipeline/SKILL.md
git -C $R commit -m "docs(race-pipeline): add VOTE411 pointer-only interim procedure

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: doctrine line in QUOTE-CURATION-PRINCIPLES.md — SEPARATE REPO

> ⚠️ This file lives in the **`essentials` repo** (`/Users/chrisandrews/Documents/GitHub/essentials`), NOT on-the-record. It needs its own branch and its own PR. Do this only after confirming with the user, and do not mix it into the on-the-record branch.

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/essentials/docs/QUOTE-CURATION-PRINCIPLES.md` — add one line to the source-hierarchy / original-sources rule (§5, where aggregators like ontheissues are addressed).

- [ ] **Step 1: Locate the original-sources rule**

Run: `grep -niE "ontheissues|aggregator|original source" /Users/chrisandrews/Documents/GitHub/essentials/docs/QUOTE-CURATION-PRINCIPLES.md`
Read the surrounding paragraph so the new line matches its voice.

- [ ] **Step 2: Add the doctrine line**

Add, near that rule:

```markdown
- **VOTE411 (vote411.org / thevoterguide.org) is a pointer, never a cited source.** The
  answers are the candidate's own words, but LWV's terms bar reuse without written
  permission. Read it to find a position, then cite the candidate's own material; if the
  position exists only on VOTE411, leave the candidate absent. (Lifts if a League data
  license is granted.)
```

- [ ] **Step 3: Commit on an essentials branch**

```bash
E=/Users/chrisandrews/Documents/GitHub/essentials
git -C $E checkout -b docs/vote411-pointer-doctrine main
git -C $E add docs/QUOTE-CURATION-PRINCIPLES.md
git -C $E commit -m "docs: VOTE411 is a pointer, never a cited source (interim)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** §7.1 → Task 4. §7.2 → Tasks 1–3 (guard code + verify skip + doc). §7.3 → Task 5. Compliance spine (§3) → Global Constraints + Task 1 remedy text + Task 4 procedure. Success criteria (§8): the `--verify-sources` skip (Task 2) and the flag (Task 1) are both covered; "no VOTE411 text in repo" is enforced by Global Constraints + invented fixtures.
- **Placeholder scan:** none — all code, test, and run commands are literal.
- **Type consistency:** `POINTER_ONLY_SOURCE` and `check_pointer_only_source` are defined in Task 1 and consumed by Task 2 by the same names; `Finding` fields match the sibling checks.
- **Note on a deliberate spec deviation:** the spec §7.2 said "add to the `invalid-source` check." Implementation instead adds a *sibling* check `pointer-only-source`, matching the codebase's one-regex-one-check pattern (`unquotable-source`, `scorecard-source`) and preserving the distinct pointer-only remedy. Same effect, cleaner fit.

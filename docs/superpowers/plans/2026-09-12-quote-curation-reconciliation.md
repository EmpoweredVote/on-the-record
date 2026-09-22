# Quote-curation Skill Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one canonical quote-curation corpus in on-the-record the single source of truth, so `research-stances` (ev-accounts) consumes the real rules by injection instead of stale copies, and `build-and-check.mjs` can no longer drift from `audit-quotes/checks.py`.

**Architecture:** Author `PRINCIPLES.md` at a neutral on-the-record location; wrap the injectable rule spans in EDITORIAL.md/CHECKS.md with fence markers; add a small extractor so `research-stances` pastes current rule text into its research sub-agent prompt at runtime; pin the two mechanical-check implementations to one shared fixtures contract; repoint every reference to the real doc. Work spans two sibling repos and commits to a branch in each.

**Tech Stack:** Markdown docs; Python 3 + pytest (on-the-record, `checks.py`/`test_checks.py`); Node ESM (ev-accounts, `build-and-check.mjs`, `node:test`); git.

## Global Constraints

- **Two repos, two branches.** on-the-record work → branch `docs/quote-curation-reconciliation-spec` (already holds the spec). ev-accounts work → new branch `feat/quote-curation-reconcile`. Never commit ev-accounts code from the on-the-record tree or vice-versa.
- **Never blanket-commit.** No `git add -A`/`git add .`/`git commit -a`. Stage listed paths only. Never commit at the `~/Documents/GitHub` workspace root.
- **No production DB writes in this work.** Every change is docs/skills/scripts/tests. `build-and-check.mjs` opens a DB connection only to build its bundle (read-only); do not add writes.
- **Insert paths stay separate.** Do not merge `insert_quotes.py` and the research-stances inline inserts.
- **Monorepo-agnostic references.** Refer to the corpus by logical location ("on-the-record repo, `docs/quote-curation/PRINCIPLES.md`") plus a one-line sibling-checkout resolution note. No new hardcoded `../` depth beyond that note.
- **Runtimes:** on-the-record Python = `.venv/bin/python` (never system python3). Node resolves deps from `ev-accounts/backend/node_modules`.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Corpus scope = MINIMAL** (decided): only `PRINCIPLES.md` moves to the neutral home; `EDITORIAL.md`/`CHECKS.md` stay in their skill dirs.
- **Shared fixtures = single copy** at `on-the-record/docs/quote-curation/fixtures/mechanical-checks.json`; both the Python and Node parity tests read that one file (Node reads it cross-repo).

## File Structure

**on-the-record**
- Create: `docs/quote-curation/PRINCIPLES.md` — the canonical "why".
- Create: `docs/quote-curation/fixtures/mechanical-checks.json` — the shared check contract.
- Create: `.claude/skills/audit-quotes/tests/test_fixture_parity.py` — Python side of the parity pin.
- Modify: `.claude/skills/audit-quotes/CHECKS.md` — add inject fences; repoint doc ref.
- Modify: `.claude/skills/publish-quotes/EDITORIAL.md` — add inject fences; repoint doc ref.
- Modify: `.claude/skills/publish-quotes/{SKILL.md,REFERENCE.md}`, `.claude/skills/audit-quotes/SKILL.md` — repoint doc ref.

**ev-accounts** (branch `feat/quote-curation-reconcile`)
- Create: `.claude/skills/research-stances/scripts/extract-canonical-rules.mjs` — reads the fenced spans from the on-the-record checkout.
- Create: `.claude/skills/research-stances/scripts/tests/extract-canonical-rules.test.mjs` — injection test.
- Create: `.claude/skills/research-stances/scripts/tests/fixture-parity.test.mjs` — Node side of the parity pin.
- Modify: `.claude/skills/research-stances/scripts/build-and-check.mjs` — add the 4 missing checks, fix `note-too-long`.
- Modify: `.claude/skills/research-stances/SKILL.md` — remove inlined paraphrases; add the injection step; repoint doc ref.
- Modify: `.claude/skills/compass-topic-builder/SKILL.md` — repoint doc ref (coupling section).

---

## Task 1: Retire the three superseded legacy branches (on-the-record)

**Files:** none (git branch operations only).

**Interfaces:**
- Consumes: nothing.
- Produces: a clean branch namespace — later tasks assume `docs/quote-curation-*` topic branches no longer exist.

- [ ] **Step 1: Confirm each branch is superseded**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
for b in docs/quote-curation-punctuation docs/quote-curation-responsiveness docs/quote-curation-tier4-absent; do
  echo "== $b =="; git log --oneline main..$b; echo "-- unique file hunks vs main --"; git diff main...$b --stat; done
```
Expected: each branch has 1 commit; its content is already present on `main` via #64/#65/#66 (punctuation → #65, responsiveness → #66, tier4-absent → #66 + later tier work). If any branch shows a hunk with genuinely novel wording not on `main`, STOP and surface it to the user instead of deleting.

- [ ] **Step 2: Delete the three branches**

Run:
```bash
git branch -D docs/quote-curation-punctuation docs/quote-curation-responsiveness docs/quote-curation-tier4-absent
```

- [ ] **Step 3: Verify they are gone**

Run: `git branch --list 'docs/quote-curation-*'`
Expected: only `docs/quote-curation-reconciliation-spec` remains (the spec branch). No `-punctuation`/`-responsiveness`/`-tier4-absent`.

- [ ] **Step 4: No commit** — branch deletion needs none. Proceed on `docs/quote-curation-reconciliation-spec`.

---

## Task 2: Author `PRINCIPLES.md` at the neutral home (on-the-record)

**Files:**
- Create: `docs/quote-curation/PRINCIPLES.md`
- Read for source material: `.claude/skills/publish-quotes/EDITORIAL.md`, `.claude/skills/audit-quotes/CHECKS.md`, `.claude/skills/compass-topic-builder/SKILL.md` (coupling/season references), and any ADR the compass season model points at (`git log --oneline --all | grep -iE 'season|#196|#222'` then read the referenced docs).

**Interfaces:**
- Consumes: nothing in code.
- Produces: the logical path `on-the-record/docs/quote-curation/PRINCIPLES.md`, referenced by every skill in Tasks 4 and 8. Section anchors later tasks cite: `#coupling-model`, `#ranking-question`, `#sourcing`, `#differentiation`.

- [ ] **Step 1: Draft the document with all required sections**

Create `docs/quote-curation/PRINCIPLES.md` with these H2 sections, each authored from the mechanics files (do not leave any as a heading-only stub):
1. `## Selection philosophy`
2. `## Coupling model` (anchor `#coupling-model`) — the quote↔stance-value coupling, **aligned with the compass revision + season model** (#196/#222): a quote is coupled to a stance *value* under a topic's current season/revision, and a value change must move its public reasoning with it.
3. `## Ranking question` (anchor `#ranking-question`) — the per-race question a topic's quotes are ranked to answer (#78); the on-question gate resolves against it, not only the topic.
4. `## Anonymity and the blind card`
5. `## Accountability` — policy/office critique stays; personal attack goes.
6. `## Sourcing` (anchor `#sourcing`) — the questioner-independence tier ladder, the written-medium verbatim rule, and source verification (a quote must verify against its cited, ingested source; aggregators/quiz/scorecard pages are not sources).
7. `## Differentiation` (anchor `#differentiation`) — show the HOW/mechanism, not just a shared goal.
8. `## Responsiveness and absence` — off-question re-homing; a record-only candidate is absent, never laundered into a position.
9. `## Five chairs` — the five-position framing note.

At the top, add a one-line pointer: `> Mechanics that implement this: publish-quotes/EDITORIAL.md (editing/de-id) and audit-quotes/CHECKS.md (checks). If a mechanics file disagrees with this document, this document wins.`

- [ ] **Step 2: Verify structure and internal links**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
grep -cE '^## ' docs/quote-curation/PRINCIPLES.md            # expect 9
grep -nE '^## (Coupling model|Ranking question|Sourcing|Differentiation)' docs/quote-curation/PRINCIPLES.md   # expect 4 hits
```
Expected: 9 H2 sections; the four anchor-bearing sections present. No "TBD"/"TODO": `! grep -niE 'tbd|todo|fill in' docs/quote-curation/PRINCIPLES.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/quote-curation/PRINCIPLES.md
git commit -m "docs(quote-curation): author canonical PRINCIPLES.md (season-aligned)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Add injection-fence markers to the mechanics files (on-the-record)

**Files:**
- Modify: `.claude/skills/audit-quotes/CHECKS.md` (wrap the §4 "The rules (summarized …)" block, ~line 364)
- Modify: `.claude/skills/publish-quotes/EDITORIAL.md` (wrap "## Two layers: canonical vs. blind" and "## Editor note (required)")

**Interfaces:**
- Consumes: nothing.
- Produces: three named injectable spans the extractor in Task 5 reads by name: `gates` (CHECKS.md §4 rules block — covers the three gates, on-question/ranking-question, differentiation, is-attack, coupling), `deid` (EDITORIAL Two layers), `note` (EDITORIAL Editor note).

- [ ] **Step 1: Fence the CHECKS.md rules block**

In `.claude/skills/audit-quotes/CHECKS.md`, put immediately **before** the `## The rules (summarized — the full principles live in QUOTE-CURATION-PRINCIPLES.md)` heading:
```
<!-- inject:gates:start -->
```
and immediately **before** the next `## Your task` heading:
```
<!-- inject:gates:end -->
```
(The fences wrap the whole summarized-rules block so the extractor captures gates + ranking-question + differentiation as one span.)

- [ ] **Step 2: Fence the two EDITORIAL.md sections**

In `.claude/skills/publish-quotes/EDITORIAL.md`:
- Put `<!-- inject:deid:start -->` on the line before `## Two layers: canonical vs. blind (`, and `<!-- inject:deid:end -->` on the line before the following `## Before storing`.
- Put `<!-- inject:note:start -->` on the line before `## Editor note (required)`, and `<!-- inject:note:end -->` at end of file (that section is last).

- [ ] **Step 3: Verify all three span pairs balance**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
for n in gates deid note; do
  s=$(grep -c "inject:$n:start" .claude/skills/*/CHECKS.md .claude/skills/*/EDITORIAL.md 2>/dev/null | awk -F: '{x+=$2} END{print x}')
  e=$(grep -c "inject:$n:end"   .claude/skills/*/CHECKS.md .claude/skills/*/EDITORIAL.md 2>/dev/null | awk -F: '{x+=$2} END{print x}')
  echo "$n start=$s end=$e"; done
```
Expected: each of `gates`, `deid`, `note` prints `start=1 end=1`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/audit-quotes/CHECKS.md .claude/skills/publish-quotes/EDITORIAL.md
git commit -m "docs(quote-curation): add inject fences for gates/deid/note spans

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Repoint on-the-record references to `PRINCIPLES.md`

**Files:** Modify (as the grep finds them): `.claude/skills/publish-quotes/SKILL.md`, `.claude/skills/publish-quotes/EDITORIAL.md`, `.claude/skills/publish-quotes/REFERENCE.md`, `.claude/skills/audit-quotes/SKILL.md`, `.claude/skills/audit-quotes/CHECKS.md`.

**Interfaces:**
- Consumes: the logical path from Task 2.
- Produces: zero dangling `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` refs in on-the-record.

- [ ] **Step 1: List the current references**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
grep -rln 'QUOTE-CURATION-PRINCIPLES' .claude/skills | grep -vE '/\.runs/|/__pycache__/'
```
Expected: the five files above (confirm the exact set before editing).

- [ ] **Step 2: Repoint each reference**

Replace every `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` with the in-repo path `docs/quote-curation/PRINCIPLES.md` (these files are in the same repo as the doc, so no cross-repo note is needed here). Keep any existing `§`/section wording but update section names to the Task 2 anchors where they changed.

- [ ] **Step 3: Verify no dangling refs remain**

Run:
```bash
grep -rln 'essentials/docs/QUOTE-CURATION-PRINCIPLES' .claude/skills | grep -vE '/\.runs/|/__pycache__/' || echo "CLEAN"
test -f docs/quote-curation/PRINCIPLES.md && echo "target exists"
```
Expected: `CLEAN` and `target exists`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/publish-quotes/SKILL.md .claude/skills/publish-quotes/EDITORIAL.md .claude/skills/publish-quotes/REFERENCE.md .claude/skills/audit-quotes/SKILL.md .claude/skills/audit-quotes/CHECKS.md
git commit -m "docs(quote-curation): repoint on-the-record refs to real PRINCIPLES.md

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Shared fixtures + Python parity test (on-the-record)

**Files:**
- Create: `docs/quote-curation/fixtures/mechanical-checks.json`
- Create: `.claude/skills/audit-quotes/tests/test_fixture_parity.py`

**Interfaces:**
- Consumes: `scripts.checks.QUOTE_CHECKS` (from `checks.py`).
- Produces: the fixtures file consumed by both parity tests. Each case = `{ "name": str, "row": {id, topic_key, race_id, candidate, editor_note, deidentified_text, quote_text, source_url}, "expect": [check_id, …] }` where `expect` is the set of **quote-level** check_ids the row must raise. Row-level checks only (no topic grouping).

- [ ] **Step 1: Write the fixtures file**

Create `docs/quote-curation/fixtures/mechanical-checks.json` covering every quote-level check in `checks.py` plus a clean case:
```json
[
  {"name": "clean", "row": {"id": "c1", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clearest on-question line. Verbatim, no edits.",
    "deidentified_text": "We should legalize triplexes on every residential lot.",
    "quote_text": "We should legalize triplexes on every residential lot.",
    "source_url": "https://www.youtube.com/watch?v=abc"}, "expect": []},
  {"name": "note-missing", "row": {"id": "c2", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "", "deidentified_text": "x", "quote_text": "We should legalize triplexes.",
    "source_url": "https://youtu.be/x"}, "expect": ["note-missing"]},
  {"name": "note-section-ref", "row": {"id": "c3", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Picked per §4 tier-1 rule.", "deidentified_text": "x", "quote_text": "Legalize triplexes.",
    "source_url": "https://youtu.be/x"}, "expect": ["note-section-ref"]},
  {"name": "note-too-long", "row": {"id": "c4", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "One. Two. Three. Four.", "deidentified_text": "x",
    "quote_text": "We should legalize triplexes on every lot.", "source_url": "https://youtu.be/x"},
    "expect": ["note-too-long"]},
  {"name": "deid-missing", "row": {"id": "c5", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear line.", "deidentified_text": "", "quote_text": "We should legalize triplexes everywhere.",
    "source_url": "https://youtu.be/x"}, "expect": ["deid-missing"]},
  {"name": "trailing-ellipsis", "row": {"id": "c6", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "We should legalize triplexes…",
    "quote_text": "We should legalize triplexes…", "source_url": "https://youtu.be/x"},
    "expect": ["trailing-ellipsis"]},
  {"name": "partisan-tell", "row": {"id": "c7", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "Democrats blocked the housing bill.",
    "quote_text": "Democrats blocked the housing bill.", "source_url": "https://youtu.be/x"},
    "expect": ["partisan-tell"]},
  {"name": "source-tier-4", "row": {"id": "c8", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "We should legalize triplexes on every lot.",
    "quote_text": "We should legalize triplexes on every lot.", "source_url": "https://jane2026.com/issues"},
    "expect": ["source-tier-4"]},
  {"name": "invalid-source", "row": {"id": "c9", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "We should legalize triplexes on every lot.",
    "quote_text": "We should legalize triplexes on every lot.", "source_url": "https://www.ontheissues.org/x.htm"},
    "expect": ["invalid-source"]},
  {"name": "unquotable-source", "row": {"id": "c10", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "We should legalize triplexes on every lot.",
    "quote_text": "We should legalize triplexes on every lot.", "source_url": "https://www.isidewith.com/candidates/x"},
    "expect": ["unquotable-source"]},
  {"name": "scorecard-source", "row": {"id": "c11", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "We should legalize triplexes on every lot.",
    "quote_text": "We should legalize triplexes on every lot.", "source_url": "https://scorecard.lcv.org/congressional-scorecard/x"},
    "expect": ["scorecard-source"]},
  {"name": "stance-label", "row": {"id": "c12", "topic_key": "housing", "race_id": "r1", "candidate": "A",
    "editor_note": "Clear.", "deidentified_text": "Universal Healthcare",
    "quote_text": "Universal Healthcare", "source_url": "https://youtu.be/x"}, "expect": ["stance-label"]}
]
```

- [ ] **Step 2: Write the Python parity test (expect it to pass — it pins current `checks.py`)**

Create `.claude/skills/audit-quotes/tests/test_fixture_parity.py`:
```python
import json, pathlib
from scripts.checks import QUOTE_CHECKS

FIX = pathlib.Path(__file__).resolve().parents[4] / "docs/quote-curation/fixtures/mechanical-checks.json"

def _ids(row):
    out = set()
    for chk in QUOTE_CHECKS:
        f = chk(row)
        if f: out.add(f.check_id)
    return out

def test_every_case_matches_checks_py():
    cases = json.loads(FIX.read_text())
    for c in cases:
        assert _ids(c["row"]) == set(c["expect"]), f"{c['name']}: {_ids(c['row'])} != {set(c['expect'])}"

def test_fixtures_cover_every_quote_check():
    cases = json.loads(FIX.read_text())
    seen = {cid for c in cases for cid in c["expect"]}
    required = {"note-missing","note-section-ref","note-too-long","deid-missing","trailing-ellipsis",
                "partisan-tell","source-tier-4","invalid-source","unquotable-source","scorecard-source","stance-label"}
    assert required <= seen, f"fixtures miss: {required - seen}"
```
(`parents[4]` = skill root → `.claude/skills/audit-quotes/tests` up 4 = repo root; verify the depth in Step 3 and adjust if the assert path is wrong.)

- [ ] **Step 3: Run the Python parity test**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills/audit-quotes
../../../.venv/bin/python -m pytest tests/test_fixture_parity.py -v
```
Expected: 2 passed. If the fixtures path assert fails, fix `parents[N]` until `FIX` resolves to the created file, then re-run.

- [ ] **Step 4: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add docs/quote-curation/fixtures/mechanical-checks.json .claude/skills/audit-quotes/tests/test_fixture_parity.py
git commit -m "test(quote-curation): shared mechanical-check fixtures + python parity pin

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Close the check gap and add the Node parity pin (ev-accounts)

**Files:**
- Modify: `.claude/skills/research-stances/scripts/build-and-check.mjs`
- Create: `.claude/skills/research-stances/scripts/tests/fixture-parity.test.mjs`

**Interfaces:**
- Consumes: the fixtures file from Task 5 (read cross-repo from the on-the-record sibling checkout).
- Produces: `build-and-check.mjs` now raises `invalid-source`, `unquotable-source`, `scorecard-source`, `stance-label`, and uses the `>3`-sentence `note-too-long` threshold — matching `checks.py`. Exports a pure `checkQuote(row)` for the test.

- [ ] **Step 1: Create the ev-accounts branch**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git checkout -b feat/quote-curation-reconcile
```

- [ ] **Step 2: Write the failing Node parity test**

Create `.claude/skills/research-stances/scripts/tests/fixture-parity.test.mjs`:
```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { checkQuoteRow } from '../build-and-check.mjs';

const here = dirname(fileURLToPath(import.meta.url));
// scripts/tests -> ev-accounts root is 5 up; sibling on-the-record holds the fixtures.
const otr = resolve(here, '..', '..', '..', '..', '..', 'on-the-record');
const FIX = resolve(otr, 'docs/quote-curation/fixtures/mechanical-checks.json');

test('build-and-check matches the shared fixture contract', () => {
  const cases = JSON.parse(readFileSync(FIX, 'utf8'));
  for (const c of cases) {
    const got = new Set(checkQuoteRow(c.row).map(f => f.check_id));
    assert.deepEqual([...got].sort(), [...c.expect].sort(), `${c.name}: ${[...got]} != ${c.expect}`);
  }
});
```

- [ ] **Step 3: Run it to confirm it fails**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
node --test .claude/skills/research-stances/scripts/tests/fixture-parity.test.mjs
```
Expected: FAIL — `checkQuoteRow` is not exported yet, and (once exported) the four missing checks and the stale `note-too-long` threshold mismatch.

- [ ] **Step 4: Refactor `build-and-check.mjs` — export a pure row checker and add the missing checks**

In `build-and-check.mjs`: rename the existing `checkQuote(q)` internals into an exported `export function checkQuoteRow(r)` that maps the fixture row fields (`editor_note`, `deidentified_text`, `quote_text`, `source_url`) to findings `{check_id}` (keep the existing `severity`/`what`). Add these regexes near the existing ones:
```js
const AGGREGATOR_SOURCE = /ontheissues\.org|wikipedia\.org/i;   // -> invalid-source (high)
const QUIZ_SOURCE       = /isidewith\.com/i;                    // -> unquotable-source (high)
const SCORECARD_SOURCE  = /\/(?:[a-z]+-)?scorecards?\//i;       // -> scorecard-source (high)
const WORD = /[A-Za-z0-9][A-Za-z0-9'’-]*/g;                     // stance-label word count
const STANCE_LABEL_MAX_WORDS = 4;
```
Add to the per-row check body (after the existing checks):
```js
const url = r.source_url || '';
if (AGGREGATOR_SOURCE.test(url)) out.push({ ...base, check_id: 'invalid-source', severity: 'high',
  what: `source is a secondary aggregator, not an original: ${url}` });
if (QUIZ_SOURCE.test(url)) out.push({ ...base, check_id: 'unquotable-source', severity: 'high',
  what: `source is a quiz/questionnaire site (no quotable row): ${url}` });
if (SCORECARD_SOURCE.test(url)) out.push({ ...base, check_id: 'scorecard-source', severity: 'high',
  what: `source is a legislative scorecard (votes/ratings, not utterances): ${url}` });
const words = ((r.quote_text || '').match(WORD) || []).length;
if (words > 0 && words <= STANCE_LABEL_MAX_WORDS) out.push({ ...base, check_id: 'stance-label', severity: 'medium',
  what: `quote is ${words} word(s) — a stance label, not a rankable statement.` });
```
Fix the stale threshold: change the `note-too-long` guard from `> 2` to `> 3` sentences (matching `checks.py` line 72), and update its `what` string to "longer than 3 sentences." Keep the existing `source-tier-4` (campaign site) and `partisan-tell` logic. Ensure the CSV-bundle path calls `checkQuoteRow` so the two code paths share one implementation.

- [ ] **Step 5: Run the parity test to confirm it passes**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
node --test .claude/skills/research-stances/scripts/tests/fixture-parity.test.mjs
```
Expected: PASS (1 test). If the fixtures path fails to resolve, adjust the `otr` relative depth until `FIX` exists, then re-run.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/research-stances/scripts/build-and-check.mjs .claude/skills/research-stances/scripts/tests/fixture-parity.test.mjs
git commit -m "fix(research-stances): pin build-and-check to shared fixtures; add 4 missing checks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Rule extractor + injection test (ev-accounts)

**Files:**
- Create: `.claude/skills/research-stances/scripts/extract-canonical-rules.mjs`
- Create: `.claude/skills/research-stances/scripts/tests/extract-canonical-rules.test.mjs`

**Interfaces:**
- Consumes: the inject fences from Task 3 (`gates`, `deid`, `note`) in the on-the-record checkout.
- Produces: `export function extractSpan(name)` returning the fenced text (fences stripped) for `gates|deid|note`, and a CLI (`node extract-canonical-rules.mjs gates deid note`) that prints each span under a `### INJECT: <name>` header for pasting into the sub-agent prompt.

- [ ] **Step 1: Write the failing injection test**

Create `.claude/skills/research-stances/scripts/tests/extract-canonical-rules.test.mjs`:
```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractSpan } from '../extract-canonical-rules.mjs';

test('each named span resolves non-empty from the on-the-record corpus', () => {
  for (const name of ['gates', 'deid', 'note']) {
    const txt = extractSpan(name);
    assert.ok(txt && txt.trim().length > 40, `${name} span empty or missing`);
  }
});

test('the gates span carries the ranking-question and differentiation rules', () => {
  const gates = extractSpan('gates').toLowerCase();
  assert.match(gates, /ranking question|on-question/);
  assert.match(gates, /differ/);   // non-differentiating / differentiation
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
node --test .claude/skills/research-stances/scripts/tests/extract-canonical-rules.test.mjs
```
Expected: FAIL — `extract-canonical-rules.mjs` does not exist.

- [ ] **Step 3: Implement the extractor**

Create `.claude/skills/research-stances/scripts/extract-canonical-rules.mjs`:
```js
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const OTR = resolve(here, '..', '..', '..', '..', '..', 'on-the-record');  // sibling checkout
const SPANS = {
  gates: resolve(OTR, '.claude/skills/audit-quotes/CHECKS.md'),
  deid:  resolve(OTR, '.claude/skills/publish-quotes/EDITORIAL.md'),
  note:  resolve(OTR, '.claude/skills/publish-quotes/EDITORIAL.md'),
};

export function extractSpan(name) {
  const file = SPANS[name];
  if (!file) throw new Error(`unknown span: ${name}`);
  const src = readFileSync(file, 'utf8');
  const re = new RegExp(`<!--\\s*inject:${name}:start\\s*-->([\\s\\S]*?)<!--\\s*inject:${name}:end\\s*-->`);
  const m = src.match(re);
  if (!m) throw new Error(`span '${name}' not found in ${file} — is the inject fence present?`);
  return m[1].trim();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const names = process.argv.slice(2);
  for (const n of names) console.log(`### INJECT: ${n}\n\n${extractSpan(n)}\n`);
}
```

- [ ] **Step 4: Run the injection test to confirm it passes**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
node --test .claude/skills/research-stances/scripts/tests/extract-canonical-rules.test.mjs
```
Expected: PASS (2 tests). If the second test fails, the CHECKS.md §4 rules block does not yet name the ranking-question/differentiation rules — fix that wording on the on-the-record side (Task 3 span content) rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/research-stances/scripts/extract-canonical-rules.mjs .claude/skills/research-stances/scripts/tests/extract-canonical-rules.test.mjs
git commit -m "feat(research-stances): canonical-rule extractor + injection test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Refactor `research-stances/SKILL.md` — inject instead of inline (ev-accounts)

**Files:** Modify `.claude/skills/research-stances/SKILL.md`.

**Interfaces:**
- Consumes: `extract-canonical-rules.mjs` (Task 7).
- Produces: a SKILL.md that carries no inlined rule paraphrase; the research sub-agent prompt is built from the extractor output.

- [ ] **Step 1: Add the runtime-injection orchestrator step**

In STEP 1 ("Dispatch Research Agents"), immediately before the agent prompt template, add:
> **Inject the canonical curation rules (do not paraphrase them here).** Run
> `node .claude/skills/research-stances/scripts/extract-canonical-rules.mjs gates deid note`
> and paste its output into the sub-agent prompt at the `[INJECT: gates]`, `[INJECT: deid]`,
> `[INJECT: note]` markers below. These come from the on-the-record corpus (sibling checkout;
> logical home `on-the-record/docs/quote-curation/PRINCIPLES.md` and its mechanics files). If the
> extractor errors, the on-the-record checkout is missing — stop and resolve it, do not fall back to
> a remembered summary.

- [ ] **Step 2: Replace the three inlined blocks with markers**

In the agent prompt template, delete the inlined prose and leave only the markers:
- Replace the `QUOTE-SELECTION GATES — a quote_text must pass ALL THREE …` block with a single line: `QUOTE-SELECTION GATES + RANKING QUESTION + DIFFERENTIATION:\n[INJECT: gates]`
- Replace the `DE-IDENTIFICATION CONTRACT (quote_deidentified) …` block with: `DE-IDENTIFICATION CONTRACT:\n[INJECT: deid]`
- Replace the inlined `editor_note (REQUIRED …)` explanation with: `EDITOR NOTE RULE:\n[INJECT: note]` (keep the one-line CSV column description in the CSV-columns list).

- [ ] **Step 3: Verify no paraphrase survives**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
grep -nE 'pass ALL THREE|DE-IDENTIFICATION CONTRACT —|FORWARD, not record' .claude/skills/research-stances/SKILL.md || echo "NO INLINE PARAPHRASE"
grep -nE '\[INJECT: (gates|deid|note)\]' .claude/skills/research-stances/SKILL.md   # expect 3
```
Expected: `NO INLINE PARAPHRASE` and 3 `[INJECT: …]` markers.

- [ ] **Step 4: Smoke-test the extractor output is paste-ready**

Run:
```bash
node .claude/skills/research-stances/scripts/extract-canonical-rules.mjs gates deid note | head -20
```
Expected: three `### INJECT:` sections with real rule text.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/research-stances/SKILL.md
git commit -m "refactor(research-stances): inject canonical rules; drop inlined paraphrases

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Repoint ev-accounts references + season-model check (ev-accounts)

**Files:** Modify `.claude/skills/research-stances/SKILL.md`, `.claude/skills/compass-topic-builder/SKILL.md`.

**Interfaces:**
- Consumes: the logical corpus path.
- Produces: zero dangling doc refs in ev-accounts; confirmed season-model alignment.

- [ ] **Step 1: List and repoint the references**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
grep -rln 'QUOTE-CURATION-PRINCIPLES' .claude/skills
```
Repoint each `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` to the logical cross-repo location with a resolution note, e.g.:
`the on-the-record corpus, docs/quote-curation/PRINCIPLES.md (sibling checkout: ../on-the-record/docs/quote-curation/PRINCIPLES.md)`. Update `compass-topic-builder`'s `§7` link to the `#coupling-model` anchor.

- [ ] **Step 2: Verify no dangling refs**

Run:
```bash
grep -rln 'essentials/docs/QUOTE-CURATION-PRINCIPLES' .claude/skills || echo "CLEAN"
```
Expected: `CLEAN`.

- [ ] **Step 3: Confirm compass reads still resolve under the season model**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git log --oneline -3 -- .claude/skills/compass-topic-builder/SKILL.md
grep -niE 'season|revision' .claude/skills/compass-topic-builder/SKILL.md | head
```
Read `compass-topic-builder/SKILL.md` and confirm its coupling/season wording matches `PRINCIPLES.md#coupling-model`. If they disagree, update the `PRINCIPLES.md` coupling section (Task 2) on the on-the-record branch to match the shipped season model, and re-run Task 2 Step 2.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/research-stances/SKILL.md .claude/skills/compass-topic-builder/SKILL.md
git commit -m "docs(quote-curation): repoint ev-accounts refs; align coupling with season model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 10: Full-suite verification (both repos)

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything above.
- Produces: green parity + injection tests, zero dangling refs, balanced fences.

- [ ] **Step 1: on-the-record — audit tests green**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills/audit-quotes
../../../.venv/bin/python -m pytest tests/test_checks.py tests/test_fixture_parity.py -q
```
Expected: all pass (existing checks + the new parity pin).

- [ ] **Step 2: ev-accounts — parity + injection tests green**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
node --test .claude/skills/research-stances/scripts/tests/
```
Expected: both test files pass.

- [ ] **Step 3: No dangling references anywhere**

Run:
```bash
grep -rln 'essentials/docs/QUOTE-CURATION-PRINCIPLES' \
  /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills \
  /Users/chrisandrews/Documents/GitHub/ev-accounts/.claude/skills \
  | grep -vE '/\.runs/|/__pycache__/' || echo "CLEAN"
```
Expected: `CLEAN`.

- [ ] **Step 4: Report** — summarize to the user: tests green, refs clean, branches (`docs/quote-curation-reconciliation-spec` in on-the-record, `feat/quote-curation-reconcile` in ev-accounts) ready to push/PR. Do not push or open PRs unless the user asks.

---

## Self-review notes (author)

- **Spec coverage:** §1 corpus home → Task 2; §2 branches+doc → Tasks 1,2; §3 injection → Tasks 3,7,8; §4 fixture parity → Tasks 5,6; §5 reference sweep → Tasks 4,9; §6 season model → Tasks 2,9. Verification → Task 10. All spec sections mapped.
- **Non-goals honored:** insert paths untouched; no DB writes; monorepo-agnostic references with resolution notes.
- **Cross-repo ordering:** Task 3 (fences) precedes Task 7 (extractor test); Task 5 (fixtures) precedes Task 6 (Node parity). Both cross-repo reads resolve the on-the-record sibling checkout, consistent with the existing STEP 4e pattern.

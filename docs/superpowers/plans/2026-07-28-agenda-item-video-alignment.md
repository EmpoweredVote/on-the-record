# Agenda Item → Video Alignment (Pass B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a meeting with published agenda items is processed and published, bind each item to a time span of the video, extract its outcome, and flip the item rows to `happened` — in place, permalinks preserved.

**Architecture:** Anchor-first per the structure-alignment design (2026-07-18): mechanical anchors (legislation refs spoken in segments, outcome phrases) come first; an LLM bounds spans between anchors and reads outcomes; mechanical validation (monotonicity, span-contains-anchor, outcome-evidence, oracle agreement) gates everything — abstain-don't-guess, holes modeled (item without span = not reached; span without item = procedural). Publish is `UPDATE ... WHERE meeting_id AND position` — never delete (item ids are public permalinks). July 22's processed meeting + its real agenda are the committed calibration fixture; Wednesday (July 29) is the first live run.

**Tech Stack:** Python (`.venv/bin/python` ONLY), anthropic (sonnet), psycopg2, requests. Repo: `/Users/chrisandrews/Documents/GitHub/on-the-record`, branch `feat/agenda-item-alignment` off main. Specs: `docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md` (Pass B), `docs/superpowers/specs/2026-07-18-structure-alignment-pipeline-design.md` (alignment architecture).

**House conventions (unchanged from the adapter plan):** flat `tests/test_*.py` via `.venv/bin/pytest`; injected `fetch`/`client`; fixtures committed verbatim; models config in `src/config.py`; pure helpers tested, cursor-bound thin; `conftest.py` strips DATABASE_URL.

---

### Task 1: Calibration fixtures — July 22 agenda + segment index

**Files:**
- Create: `tests/fixtures/onboard/agenda_2026-07-22.pdf` (+ `.txt` via `src/pdf_text.extract_text`)
- Create: `tests/fixtures/alignment/segments_2026-07-22.json`
- Create: `scripts/make_segment_fixture.py`

- [x] **Step 1:** Branch: `git checkout main && git pull && git checkout -b feat/agenda-item-alignment`.
- [x] **Step 2:** Download the July 22 AMENDED agenda (OnBoard file id **17185**, the latest-created Agenda for meeting 11958 — verify via the committed `meetings_window_2026.json` fixture): `curl -sL -A "Mozilla/5.0" "https://bloomington.in.gov/onboard/meetingFiles/17185/download" -o tests/fixtures/onboard/agenda_2026-07-22.pdf`, confirm `file` says PDF, freeze `.txt` via `extract_text` (same as the 07-29 fixture). Run `parse_agenda` on it and print the item list — record in your report how many items and which legislation refs (expect Resolution 2026-13, Ordinance 2026-12, the Car Free Kirkwood ordinance ref, Ordinance 2026-15 first reading, etc.). If parse produces garbage on this agenda (template drift), STOP and report — the parser needs fixing first.
- [x] **Step 3:** Write `scripts/make_segment_fixture.py`: reads a local meeting's `transcript_named.json` and writes a compact segment index JSON: `[{ "i": <segment_index>, "start": <s>, "end": <s>, "speaker": <name or label>, "text": <full text> }]`. Run it on `~/CouncilScribe/meetings/2026-07-22-bloomington-regular-session/transcript_named.json` → `tests/fixtures/alignment/segments_2026-07-22.json`. Full segment text is required (anchors live in it); expect ~1 MB — acceptable, commit it.
- [x] **Step 4:** Commit: `test: July 22 calibration fixtures (amended agenda + segment index)`.

### Task 2: Legislation-page oracle — `src/legislation_oracle.py`

**Files:** Create `src/legislation_oracle.py`, `tests/test_legislation_oracle.py`, `tests/fixtures/legislation/*.html`

- [x] **Step 1:** Capture live fixtures: `https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-12` and `.../Resolution/2026/2026-13` (curl with UA) → `tests/fixtures/legislation/ordinance_2026-12.html`, `resolution_2026-13.html`. READ them to find the final-action markup (spike evidence: text like `Final 2026-06-10 pass 7-2 (Asare, Rosenbarger)`). If 2026-12/13 don't yet show final actions, ALSO capture `Ordinance/2026/2026-14` (known to show `pass 7-2`) and use that as the primary parse fixture; keep the no-action page as the "pending" fixture.
- [x] **Step 2 (TDD):** `fetch_final_action(legislation_ref: str, *, fetch=_default_fetch) -> Optional[FinalAction]` where `FinalAction = {action_date: str, outcome: str, tally: Optional[str]}`. URL built from the ref (`Ordinance 2026-12` → `/council/legislation/Ordinance/2026/2026-12`; handle `Appropriation Ordinance` → its own type path if the site has one — check the legislation landing page's type list in the fixture or live; if unclear, return None for that type and note it). Outcome vocabulary mapping: `pass/passed/adopted → passed`, `fail/failed/rejected/defeated → failed`, postponed/continued → `continued`, withdrawn → `pulled`. Returns None on fetch error, non-200, or no final action on the page (pending). Tests: real-fixture parse (exact outcome+tally), pending page → None, malformed HTML → None, URL construction capture.
- [x] **Step 3:** Commit: `feat: Bloomington legislation-page oracle (final action per ref)`.

### Task 3: Mechanical alignment core — `src/agenda_align.py` (part 1: anchors + validation)

**Files:** Create `src/agenda_align.py`, `tests/test_agenda_align.py`

Pure functions, no LLM in this task:

```python
@dataclass
class SegmentRef:
    i: int; start: float; end: float; speaker: str; text: str

@dataclass
class ItemSpan:
    position: int
    start_segment: Optional[int] = None    # None = not reached / abstained
    end_segment: Optional[int] = None
    outcome: Optional[str] = None           # vocab or None
    outcome_evidence_segment: Optional[int] = None
    rejected_reason: Optional[str] = None   # why validation zeroed it
```

- [x] **Step 1 (TDD):** `find_ref_anchors(items, segments) -> dict[int, list[int]]` — for each item with a `legislation_ref`, the segment indices whose text contains the ref (normalize: case-insensitive, tolerate `2026-15` spoken/transcribed as `2026 15` / `20 26 15`? NO — v1 exact-ish: case-insensitive match of `ordinance 2026-15` AND bare `2026-15`; log a note if an item gets zero anchors). Test against the REAL July 22 fixtures: parse the agenda, load segments, assert the known refs anchor (the transcript demonstrably contains "Ordinance 2026-15" — the summary outline found it at 23:01; verify which refs anchor and pin them).
- [x] **Step 2 (TDD):** `OUTCOME_PHRASES` keyword table (adopted, passes, passed, carries, carried, motion fails, defeated, rejected, postponed, continued, withdrawn, "roll call" tallies like "seven to two", "ayes have it") + `outcome_evidence_ok(outcome, segment_text) -> bool` — the claimed evidence segment must contain a phrase consistent with the claimed outcome (passed-phrases for passed, etc.). Synthetic tests both directions.
- [x] **Step 3 (TDD):** `validate_spans(items, spans, segments) -> list[ItemSpan]` — enforce, zeroing (with rejected_reason) anything that fails:
  - spans monotonic by item position (a span starting earlier than a previous item's span start → reject the later one; out-of-order discussion is real but v1 rejects rather than guesses — the July 22 fixture will tell us if this bites, report if so);
  - span text must contain the item's ref (when it has one) OR ≥2 distinctive title tokens (title_raw words >5 chars, case-insensitive) — the containment gate;
  - end_segment ≥ start_segment, both in range;
  - outcome only with valid evidence segment INSIDE or within 5 segments after the span, passing `outcome_evidence_ok`;
  - outcome vocabulary enforced.
- [x] **Step 4:** Commit: `feat: alignment anchors + mechanical validation gates`.

### Task 4: LLM span bounding — `src/agenda_align.py` (part 2)

**Files:** Modify `src/agenda_align.py`, `src/config.py` (+`AGENDA_ALIGN_MODEL = "claude-sonnet-4-5"`, `AGENDA_ALIGN_MAX_TOKENS = 4000`), extend tests

- [x] **Step 1 (TDD, FakeClient):** `build_align_prompt(items, segments, anchors)` — compact transcript index (one line per segment: `i | mm:ss | speaker | text truncated to 160 chars`), the agenda items (position, item_number, title_raw, legislation_ref), the mechanical anchor hints, and instructions: return JSON `{"spans": [{"position": N, "start_segment": i|null, "end_segment": i|null, "outcome": vocab|null, "outcome_evidence_segment": i|null}]}`; null span for items not reached; do NOT invent outcomes; procedural gaps belong to no item. `align_items(client, items, segments) -> list[ItemSpan]` = anchors → prompt → tolerant JSON parse → `validate_spans`. Malformed reply → all-abstain with reason (never raises). Tests: grounded fake reply survives; non-monotonic fake reply gets zeroed; invented outcome without evidence gets zeroed; malformed JSON abstains.
- [x] **Step 2:** `apply_oracle(spans, items, *, fetch)` — for items with refs and a claimed outcome, fetch the legislation page; when the oracle has a final action and it DISAGREES → zero the outcome (keep the span) with rejected_reason `oracle disagreement`; agrees or oracle-pending → keep. Tests with fake fetch both ways.
- [x] **Step 3:** Commit: `feat: LLM span bounding behind mechanical + oracle gates`.

### Task 5: Publish flip + CLI — `src/publish.py`, `run_local.py`

**Files:** Modify `src/publish.py`, `run_local.py`; extend `tests/test_publish.py` (pure helpers only)

- [x] **Step 1 (TDD, pure):** `build_alignment_updates(spans, segments) -> list[tuple]` — rows `(status, segment_start_seconds, segment_end_seconds, outcome, position)` where status='happened' for EVERY item (the meeting happened; unbound items are happened-without-span), seconds taken from `segments[start].start` / `segments[end].end`, None bounds for abstained spans. Pin arity + None handling.
- [x] **Step 2:** Cursor-bound (thin, untested per policy): `_update_aligned_items(cur, meeting_uuid, updates)` executing `UPDATE meetings.agenda_items SET status=%s, segment_start_seconds=%s, segment_end_seconds=%s, outcome=%s, updated_at=now() WHERE meeting_id=%s AND position=%s` per row (executemany). NEVER delete — permalinks. Top-level `align_and_flip(meeting_id: str) -> dict`: load local `transcript_named.json`; connect; find meeting uuid + its agenda_items BY SLUG (`WHERE slug=%s`); **guard: zero items → raise with the message that the meeting was published under a slug with no agenda items (likely wrong --meeting-id — the scheduled slug is required)**; run `align_items` (real anthropic client) + `apply_oracle`; `_update_aligned_items`; return a summary dict (items, bound, outcomes, abstentions with reasons) and print it loudly.
- [x] **Step 3:** `run_local.py --align-agenda MEETING_ID` dispatch in the utility-flags chain of `main()` (mirror `--publish-meeting`'s dispatch shape).
- [x] **Step 4:** Full suite; commit: `feat: align-agenda publish flip (in-place update, slug-guarded)`.

### Task 6: Calibration run on July 22 (live LLM, NO DB writes)

**Files:** Create `scripts/calibrate_alignment.py`, `tests/fixtures/alignment/llm_reply_2026-07-22.json`, extend `tests/test_agenda_align.py`

- [x] **Step 1:** `scripts/calibrate_alignment.py`: loads the July 22 fixtures (agenda .txt → parse_agenda; segment index JSON), runs `align_items` with a REAL anthropic client (env from .env.local), `apply_oracle` with real fetch, prints the full mapping table (item → span times → outcome → reasons) and **captures the raw LLM reply** to `tests/fixtures/alignment/llm_reply_2026-07-22.json`. No DB access at all.
- [x] **Step 2:** Run it. HAND-CHECK the table against known truth (the published July 22 outline: Reports@1:29, Public Comment@19:41, First Reading 2026-15@23:01, BHA/2026-13 discussion@35:18 + roll-call@1:37:03, 2026-12@1:38:15, Car Free Kirkwood@1:39:44 + roll-call@2:52:41). Known outcomes to verify: Resolution 2026-13 passed; Ordinance 2026-12 passed; Car Free Kirkwood veto override FAILED; Ordinance 2026-15 first reading = no outcome (its vote is July 29). Record hits/misses in your report — misses inform prompt/gate tuning; iterate up to twice, keeping the final captured reply.
- [x] **Step 3:** Pin the calibration as a test: replay the CAPTURED reply through a FakeClient → `align_items` → assert the hand-verified facts (e.g. 2026-15's span covers ~23:01, its outcome is None; 2026-13 outcome passed; monotonicity held; ≥N of M items bound). This is the adapter's first hand-labeled benchmark meeting per the July-18 design.
- [x] **Step 4:** Commit: `test: July 22 hand-checked alignment calibration (first benchmark meeting)`.

### Task 7: Runbook + PR

- [x] **Step 1:** Create `docs/runbooks/bloomington-meeting-day.md`: the Wednesday sequence — (1) after CATS posts the video: process with `--meeting-id bloomington-city-council-2026-07-29 --body bloomington-common-council` (the scheduled slug is MANDATORY — it flips the scheduled row; the GUI's derived id will NOT match) + the standard council flags; (2) review speakers in the GUI; (3) `--publish-meeting bloomington-city-council-2026-07-29`; (4) `--align-agenda bloomington-city-council-2026-07-29`; (5) verify: item pages show happened state + Watch links; meeting footer link live.
- [x] **Step 2:** Full suite green; push; `gh pr create` (title: "Agenda item→video alignment (Pass B): anchor-first spans, gated outcomes, in-place flip"); body summarizing modules, the calibration results table, and the runbook.

**Deferred (explicitly):** per-member vote_records; meetings.votes rows; minutes reconciliation; out-of-order span acceptance; matter linking (continued_from) — Phase 4.

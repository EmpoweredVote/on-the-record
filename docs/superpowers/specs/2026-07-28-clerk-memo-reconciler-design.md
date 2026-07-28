# Clerk Memorandum Reconciler — Design

**Date:** 2026-07-28
**Status:** Approved (user-directed build; decisions below resolved autonomously and surfaced)
**Relates to:** `2026-07-27-bloomington-item-centric-civic-coverage-design.md` (Pass B),
`docs/superpowers/plans/2026-07-28-agenda-item-video-alignment.md` (the LLM alignment this
reconciles over)

## Problem

The LLM alignment pass (Pass B) reads outcomes off the transcript, gated hard — and the
gates correctly abstain on the most common case: a motion that passes, because the chair
never says "motion carries" and roll-calls transcribe garbled (July 22 calibration:
Res 2026-13's true *passed* abstained). The legislation-page oracle can veto but not fill,
and pending legislation 404s.

The clerk posts a **Memorandum** (OnBoard file type `Memorandum`, observed next-day for
July 22, budget ~6 days) that is a *deterministic* record of exactly what we abstain on:

- every motion as templated prose — "X moved, and Y seconded that `<ref>` be `<action>`.
  The motion received a roll call vote of Ayes: N, Nays: N, Abstain: N"
- split votes with **named members per side** — "Ayes: 4 (Asare, Daily, Flaherty,
  Rosenbarger); Nays: 4 (Stosberg, Piedmont-Smith, Rollo, Ruff); Abstain: 0. FAILED"
- explicit continuances with dates — "postpone consideration of Ordinance 2026-15 until
  the next Regular Session to be held on July 29, 2026"
- per-section wall-clock stamps "[7:01pm]", attendance, per-item action history

So: **memo is authoritative for outcomes and votes; the LLM keeps spans.** No LLM anywhere
in this feature — the parser is deterministic, abstain-don't-guess on template drift.

## Architecture

Three new units plus wiring:

1. **Fetcher** (`src/onboard.py`, extended): `OnBoardMeeting.memo_url` / `memo_created`
   properties via the existing `_latest_file("Memorandum")` machinery, plus a
   `memo_updated_marker` for poller change-detection. The reconciler finds the meeting by
   querying the OnBoard window **[date−1, date+1]** — a single-day `start==end` query
   returns `[]` (verified live) — then filters to the exact date + body title prefix.

2. **Parser** (`src/memo_parse.py`, new — pure, deterministic):
   - Sections: `^N. Title [h:mmpm]` headers → wall-clock per section; item subsections
     `^N.M. <Legislation Ref>` open an item scope keyed by that ref.
   - **Motions attribute to the enclosing subsection, never by refs inside motion prose.**
     The July 22 memo itself contains a clerk typo ("The motion to discuss Ordinance
     2026-13" inside the 2026-15 section) that would misattribute under ref-scanning.
   - Motion grammar: `X moved(,) and Y seconded (that|to) …` followed (optionally) by
     `The motion … roll call vote (of|:) Ayes: N[ (names)]; Nays: N[ (names)];
     Abstain: N[ (names)][. FAILED]`. Both tally forms (bare `Ayes: 8` and named
     `Ayes: 4 (…)`) parse; the trailing `FAILED`/`PASSED` tag is captured.
   - Motion classification by action text: *introduce/read by title/synopsis* and
     *to discuss* → procedural; *be adopted* → adopt; *postpone consideration … until
     <date>* → continue (with `continued_to_date`); *table … indefinitely / be tabled* →
     continue; *withdraw* → pull.
   - **Disposition = the last motion in the item's scope that has a recorded roll-call
     vote and carried.** A moved-but-unvoted motion is a non-event (July 22: Res 2026-12's
     adoption motion has no vote sentence; the subsequent table motion carried → continued).
     Outcome mapping: adopt-carried → `passed`; adopt with `FAILED` tag → `failed`;
     continue-carried → `continued`; pull-carried → `pulled`. Adopt with Ayes ≤ Nays and
     **no** FAILED tag → no disposition + loud note (don't guess tie rules). Unparseable
     motion text → skipped with a note, never guessed.
   - The `Actions on Legislation:` history block (`Council Action (June 10, 2026): …`)
     does not match the motion grammar and is deliberately not extracted in v1 (matter
     lifecycle is Phase 4).
   - Output: `MemoItem(legislation_ref, section_wallclock, motions, disposition,
     disposition_motion, notes)`; `Motion(mover, seconder, kind, raw_text, tally,
     ayes_names, nays_names, abstain_names, failed_tag, continued_to_date)`.

3. **Reconciler** (`src/memo_reconcile.py` pure planner + `src/publish.py` DB writer
   `reconcile_memo(meeting_id)` shaped like `align_and_flip`):
   - Load meeting row by slug (id, date); load its `agenda_items`
     (id, position, legislation_ref, outcome) and `speakers` (id, display_name).
   - Fetch + parse the memo. No memo posted yet → loud no-op exit.
   - **Outcome updates:** memo items match agenda items by exact `legislation_ref`; the
     memo disposition **overwrites** `agenda_items.outcome` (authoritative — this is the
     pass-abstention fix). Items without refs are never touched. Memo refs with no
     matching item and items whose memo section had no disposition → logged, no change.
     Zero agenda_items rows (e.g. July 22, pre-poller) → zero updates, still writes votes.
   - **Votes:** one `meetings.votes` row per *substantive* motion (adopt/continue/pull
     kinds) with a recorded roll call — including ones that did not carry
     (procedural read-by-title roll calls are noise): `resolution` = legislation_ref,
     `description` = the motion sentence verbatim (trimmed), `result` = "Passed 8–0" /
     "Failed 4–4" / "Continued 8–0" / "Pulled N–N", `vote_type` = 'roll call',
     `timestamp` = NULL (v1 — the item span already provides click-to-seek; wall-clock →
     video-time derivation is a follow-up), `agenda_item_id` = matched item id or NULL.
     Idempotent delete-then-insert of this meeting's votes (delete `vote_records` first —
     FK), mirroring `_replace_votes`.
   - **Vote records (first writer of `meetings.vote_records`, pipeline-wide):** only for
     votes with **named** sides. `position` ∈ {aye, nay, abstain}. Member last name →
     speaker by case-insensitive last-name-suffix match on `display_name` (verified:
     "Asare" → "Isak Nti Asare" on July 22; all 8 split-vote members resolve).
     `speaker_id` is NOT NULL — **policy: skip-with-loud-log** for a member with no
     (or an ambiguous) speaker match. We do not fabricate speaker rows: speakers are
     diarization-owned; synthetic rows would pollute speakerCount and the review GUI.
     A skipped record loses one member's row, not the vote — the tally in `result` still
     carries the count. Unnamed unanimous tallies get no records (attendance-based
     inference would be a guess; deferred).
   - API readiness verified: ev-accounts `getVotesByMeetingId` already joins
     `meetings.vote_records` into `vote.records`. Web display is a separate task.

4. **Wiring:**
   - `run_local.py --reconcile-memo MEETING_SLUG` in the utility-flags chain (mirrors
     `--align-agenda`).
   - `scripts/poll_agendas.py --reconcile-memos [--lookback-days N]` (opt-in, default
     lookback 10): scans past meetings with a Memorandum file, change-detects on
     `memo_updated_marker` via a separate `memo_state.json` `PollState`, reconciles ones
     whose meeting row exists; missing meeting row → loud skip. The launchd job can add
     the flag once trusted.

## Known interaction (documented, not engineered around)

Re-running `--publish-meeting` wipes memo votes: `_replace_votes` delete-then-inserts
`meetings.votes` from `floor_votes`, which is empty for Bloomington. Mitigation: the
runbook says **re-run `--reconcile-memo` after any re-publish**. (Guarding
`_replace_votes` by vote_type was considered and rejected as subtle cross-feature
coupling for v1.)

## Calibration (July 22, committed fixture)

`tests/fixtures/onboard/memo_2026-07-22.pdf` + frozen `.txt`. Pinned ground truth:

| ref | disposition | vote row | records |
|---|---|---|---|
| Ordinance 2026-15 | continued (postpone → 2026-07-29) | Continued 8–0 | none (unnamed) |
| Resolution 2026-12 | continued (tabled indefinitely; unvoted adoption motion superseded) | Continued 8–0 | none |
| Resolution 2026-13 | passed (adopt carried 8–0 — the abstention the LLM pass can't fill) | Passed 8–0 | none |
| Ordinance 2026-12 | failed (FAILED tag, 4–4) | Failed 4–4 | 8 named records, 4 aye / 4 nay |

Plus: section wall-clocks parse ([6:30pm] … [9:25pm]); the 2026-13-typo motion stays in
2026-15's scope; the Actions-on-Legislation history block yields no motions; procedural
motions excluded from votes.

Live E2E after tests are green: `--reconcile-memo 2026-07-22-bloomington-regular-session`
→ 0 outcome updates (no agenda_items rows), 4 votes rows + 8 vote_records
(agenda_item_id NULL), verified via the ev-accounts API.

## Out of scope (explicit)

Attendance extraction; action-history extraction and `continued_from` edges (Phase 4 —
but `continued_to_date` is parsed and reported now); vote `timestamp` derivation from
wall-clocks; web display of votes/records; oracle-fills-abstention (superseded by this
for Bloomington); minutes reconciliation (4–7 months out, unchanged).

## Success criteria

- July 22 calibration table reproduced by tests from the committed fixture.
- Reconcile is idempotent and abstains loudly on template drift (unparseable motion →
  no outcome change, note printed).
- `meetings.vote_records` receives its first rows ever, with correct member→speaker
  resolution, and the existing API serves them.
- July 29 flow becomes: process → review → publish → align (spans) → reconcile-memo
  (outcomes+votes) when the memo posts.

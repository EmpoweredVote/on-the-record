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
     continue; *withdraw* → pull; *(v2, 2026-07-31)* *amend the agenda* → procedural;
     *adopt Amendment NN to <ref>* → **amend** (vote-eligible, never dispositive);
     *present <ref> for second reading at the next Regular Session to be held on <date>*
     → continue with `continued_to_date` (first-reading referral, July 29 items 6.1/6.2;
     the date regex anchors on both `until …` and `to be held on …` — July 22's postpone
     contains both forms).
   - **Multi-result association (v2, 2026-07-31 — the amendment pattern, June 10 items
     7.2/7.4):** the clerk writes `that <ref> be adopted.` with **no vote sentence**, then
     `to adopt Amendment 01 to <ref>.` whose block carries TWO result sentences —
     `The motion to adopt Amendment 01 to <ref> received … Ayes: 9 …` AND `The motion to
     adopt <ref> as amended received … Ayes: 5 (names); Nays: 4 (names) …`. v1 took only
     the first roll call per motion block, so Ordinance 2026-12's original 5–4 passage was
     never captured. v2 `finditer`s ALL result sentences per block and associates each by
     classifying the result sentence's own description (never by refs inside it): empty
     desc → block-owning motion; desc kind == owner kind and owner unvoted → owner (this
     keeps July 22's clerk-typo result on the discuss motion); desc mentions Amendment →
     the unvoted amend motion (owner-match is checked first — with two amendments each
     result sits in its own block; a foreign-block amendment result with >1 unvoted amend
     candidate abstains loudly); desc classifies adopt (`to adopt <ref> as amended`) → the
     pending unvoted adopt motion; unmatchable → dropped with a loud note. A result that
     would overwrite an already-recorded tally is dropped loudly.
   - **Names/count guard (v2):** the clerk annotates quorum changes as parentheticals on
     zero sides — `Abstain: 0 (Rosenbarger, Ruff out of the room)`. When a side's
     name-list length ≠ its count, or any token isn't name-shaped, the side's names are
     DROPPED with a note (tally stands). Without this, June 10's Ordinance 2026-13 7–0
     adoption would fabricate abstain records.
   - **Drift tripwires (v2):** per block, more `roll call vote` phrases than parsed result
     sentences → loud note; an item scope with roll-call text but zero recognizable motion
     sentences → loud note. (v1 lost such results silently.)
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
   - **Bare-number fallback (v2, 2026-07-31 — ref-type mislabel):** the July 29 memo
     consistently calls Ordinance 2026-15 "Resolution 2026-15" (heading + motions). When
     the exact ref misses, match by bare number (`2026-15`, digit-boundary-anchored) ONLY
     if that number is unique across BOTH the memo item refs AND the agenda rows (counted
     per row, so duplicate-ref-excluded items can never re-enter). June 10 has both
     Ordinance 2026-12 and Resolution 2026-12 in one memo — the guard refuses there. A
     fired fallback leaves a loud note naming both refs; a refused one says which side(s)
     collide. Residual accepted risk: a genuinely-absent Resolution 2026-N vs an unrelated
     Ordinance 2026-N is indistinguishable from a mislabel — the note surfaces it.
   - **Votes:** one `meetings.votes` row per *substantive* motion (adopt/amend/continue/
     pull kinds — amend added in v2 for the June 10 amendment rows; amend can never set an
     outcome since outcomes derive from `item.disposition` only) with a recorded roll
     call — including ones that did not carry
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

## Ownership hardening (2026-07-28)

`meetings.votes` is partitioned by `vote_type` into two ownership stripes, each
delete-then-inserted only by its own writer: `FLOOR_VOTE_TYPE = "recorded"`
(federal CREC floor votes, written by `_replace_votes`) and
`MEMO_VOTE_TYPE = "roll call"` (clerk memo votes, written by `reconcile_memo`).
A re-publish (`_replace_votes`) scopes its DELETE/INSERT to the floor stripe
and can never touch memo rows; a re-reconcile scopes to the memo stripe and
can never touch floor rows. Re-running `--publish-meeting` after
`--reconcile-memo` is therefore safe with no required re-run order.

Separately, agenda-item `outcome` follows an authority ladder: alignment's
`_update_aligned_items` writes `outcome = COALESCE(outcome, %s)` — it FILLS a
NULL outcome but never overwrites one already set. `reconcile_memo` remains
the only overwriter (memo dispositions are authoritative). So: align fills →
memo overwrites → align never un-fills, in either run order.

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

## Calibration v2 (June 10 + July 29, committed fixtures 2026-07-31)

The 2026-07-31 live reconciles surfaced three patterns v1 abstained on (loudly — zero
wrong writes). Fixtures `tests/fixtures/onboard/memo_2026-06-10.{pdf,txt}` +
`memo_2026-07-29.{pdf,txt}`; plan `docs/superpowers/plans/2026-07-31-memo-parser-v2.md`.

June 10 pinned truth (9 vote rows; v1 captured 5):

| ref | disposition | vote rows | records |
|---|---|---|---|
| Resolution 2026-10 | passed | Passed 9–0 | none |
| Ordinance 2026-12 | passed (**5–4 as-amended adoption**, v1 missed) | Passed 5–4 + amendment Passed 9–0 | 9 named: ayes Asare, Daily, Flaherty, Rollo, Rosenbarger; nays Stosberg, Piedmont-Smith, Zulich, Ruff |
| Resolution 2026-09 | passed (trailing "amend the agenda" motion = procedural) | Passed 9–0 | none |
| Ordinance 2026-13 | passed (7–0 per its result sentence) | Passed 7–0 + amendment Passed 8–0 | none — "out of the room" annotations dropped by the names/count guard |
| Resolution 2026-11 | passed | Passed 9–0 | none |
| Resolution 2026-12 | continued → 2026-07-22 | Continued 9–0 | none |
| Ordinance 2026-14 | passed | Passed 7–2 | 2 nay (Asare, Rosenbarger) |

July 29 pinned truth: Ordinance 2026-16 + 2026-17 continued → 2026-08-05 (first-reading
referrals, Continued 8–0 each); memo "Resolution 2026-15" (sic) → agenda **Ordinance**
2026-15 via the bare-number fallback, outcome passed, Passed 8–0 attached, loud note.

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

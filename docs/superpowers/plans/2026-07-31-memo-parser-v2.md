# Memo Parser v2 — amendment pattern, first-reading referral, ref-number fallback

**Date:** 2026-07-31
**Spec:** `docs/superpowers/specs/2026-07-28-clerk-memo-reconciler-design.md` (update in place)
**Calibration sources:** June 10 + July 29, 2026 Bloomington memos (new committed fixtures);
July 22 fixture stays pinned green.

Three real memo patterns observed 2026-07-31 broke or abstained the v1 parser (all abstains
were loud, zero wrong writes). This plan fixes them deterministically — still no LLM,
still abstain-don't-guess.

## Patterns

### 1. Amendment pattern (June 10, items 7.2 / 7.4)

Sequence inside one subsection:

1. `Daily moved and Zulich seconded that Ordinance 2026-12 be adopted.` — adopt, **no vote sentence**
2. `Asare moved and Zulich seconded to adopt Amendment 01 to Ordinance 2026-12.` — new kind
3. TWO result sentences inside motion 2's block:
   - `The motion to adopt Amendment 01 to Ordinance 2026-12 received a roll call vote of Ayes: 9, …`
   - `The motion to adopt Ordinance 2026-12 as amended received a roll call vote of Ayes: 5 (…), Nays: 4 (…), …`

v1 takes only the FIRST roll call per motion block → the 5–4 passage was never captured.

**Fix (memo_parse):**
- `finditer` ALL result sentences per block. A result sentence is
  `The motion[ <desc>] received a roll call vote …` — capture the sentence's own
  description clause.
- Associate each result to a motion by classifying the desc:
  - empty desc → the block-owning motion;
  - desc kind == owner kind and owner unvoted → owner (this keeps the July 22 clerk-typo
    result "to discuss Ordinance 2026-13" on the discuss motion — DO NOT re-attribute by
    ref-in-desc);
  - desc mentions Amendment → the (last unvoted) `amend` motion in the item scope;
  - desc classifies adopt (e.g. "to adopt <ref> as amended") → the pending unvoted adopt
    motion in the item scope;
  - unmatchable → **loud note, result dropped** (abstain-don't-guess).
- New motion kind `amend` for `to adopt Amendment NN to <ref>`: gets a vote row, is NEVER
  dispositive. `amend the agenda …` motions are `procedural` (June 10 has one inside 7.3's
  scope and one outside any item scope).
- **Names/count guard (required by June 10):** clerk annotates quorum changes as a
  parenthetical on a zero side — `Abstain: 0 (Rosenbarger, Ruff out of the room)`. When a
  side's name-list length ≠ its count, DROP the names with a note (tally stands). Without
  this, Ord 2026-13's 7–0 adoption fabricates an abstain record for Rosenbarger.

### 2. First-reading referral (July 29, items 6.1 / 6.2)

`… to present Ordinance 2026-16 for second reading at the next Regular Session to be held
on August 5, 2026 at 6:30pm.` = a continuance. Classify `for second reading` → `continue`;
extend the continued-date regex to also anchor on `to be held on` (the existing `until …`
form must keep working — July 22's postpone contains both, `until` first).

### 3. Ref-type mislabel fallback (July 29, item 7.1)

The July 29 memo consistently calls **Ordinance** 2026-15 "**Resolution** 2026-15"
(heading + motions). Exact-ref matching in `memo_reconcile.build_reconcile_plan` refused →
outcome unfilled, vote unattached.

**Fix (memo_reconcile):** when an exact ref misses, match by bare number (`2026-15`) ONLY
if that number is unique across BOTH the memo items AND the agenda items. June 10 has both
Ordinance 2026-12 and Resolution 2026-12 in one memo — the guard must refuse there. Loud
note when the fallback fires, loud refusal note when a number match exists but is not
unique.

## Pinned ground truth (new fixtures)

`tests/fixtures/onboard/memo_2026-06-10.{pdf,txt}` + `memo_2026-07-29.{pdf,txt}`
(frozen via `src.pdf_text.extract_text`, same as July 22).

June 10 parse:
| ref | disposition | dispositive vote | other |
|---|---|---|---|
| Resolution 2026-10 | passed | 9–0 | |
| Ordinance 2026-12 | passed | **5–4** Ayes (Asare, Daily, Flaherty, Rollo, Rosenbarger) / Nays (Stosberg, Piedmont-Smith, Zulich, Ruff) | amend motion 9–0; kinds [procedural, adopt, amend] |
| Resolution 2026-09 | passed | 9–0 | trailing "amend the agenda" motion = procedural |
| Ordinance 2026-13 | passed | **7–0** (per its result sentence; "out of the room" annotation dropped by the names/count guard, NO abstain names) | amend motion 8–0 |
| Resolution 2026-11 | passed | 9–0 | |
| Resolution 2026-12 | continued → 2026-07-22 | 9–0 | |
| Ordinance 2026-14 | passed | 7–2, nays named (Asare, Rosenbarger) | |

June 10 reconcile plan: **9 vote rows** (7 adopts + 2 amendment rows), Ord 2026-12
adoption carries 9 named records (5 aye / 4 nay incl. Zulich), Ord 2026-14 carries 2 nay
records, amendment rows result "Passed 9–0" / "Passed 8–0".

July 29 parse: Ordinance 2026-16 continued → 2026-08-05; Ordinance 2026-17 continued →
2026-08-05; Resolution(sic) 2026-15 passed 8–0.

July 29 reconcile: agenda holds **Ordinance** 2026-15 → number fallback fires (loud note),
outcome update `passed`, vote "Passed 8–0" attached. Guard test: a memo with both
Ordinance 2026-12 and Resolution 2026-12 refuses number matching.

Every existing July 22 pin stays green, including: clerk-typo result stays in 2026-15's
scope, history block ("Council Action …: Passed Ayes: 5 …" — no "roll call vote" phrase)
yields no motions and no results, FAILED 4–4 unchanged.

## Tasks

1. **Fixtures** (orchestrator): copy the two memo PDFs from
   `~/CouncilScribe/agendas/bloomington-city-council/<slug>/memo.pdf` into
   `tests/fixtures/onboard/`, freeze `.txt` via `extract_text`, commit.
2. **Parser v2** (`src/memo_parse.py` + `tests/test_memo_parse.py`, TDD): multi-result
   association, kind `amend`, agenda-amend procedural, referral→continue with date,
   names/count guard. New June 10 + July 29 fixture test suites + synthetic edges
   (unmatchable result desc → note; names/count mismatch → dropped).
3. **Reconciler** (`src/memo_reconcile.py` + `tests/test_memo_reconcile.py`, TDD): `amend`
   kind vote rows ("Passed N–N" when carried), guarded bare-number fallback with loud
   notes. June 10 + July 29 end-to-end plan tests.
4. **Spec update**: fold the three patterns + names/count guard + new pins into the design
   doc's parser/reconciler sections and calibration table.
5. Final review → PR → merge → live: `run_local.py --reconcile-memo` + `--check-memo` for
   `bloomington-city-council-2026-06-10` and `bloomington-city-council-2026-07-29`
   (+ 2026-05-06 if a published meeting row exists).

House rules: `.venv/bin/python` (venv at repo root), subagent-driven implementation.

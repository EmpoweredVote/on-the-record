# Federal CREC-structure spike — findings

**Date:** 2026-07-18
**Status:** Spike complete (live GovInfo probes). De-risks the Federal adapter before planning.
**Probed:** `CREC-2019-07-11` House (84 House granules incl. the NDAA FY2020 consideration), granule list + htm + MODS via live GovInfo API.

## Question 1 — Data shapes (RESOLVED)

- **Granule list** carries `{granuleId, granuleClass, title}` only — **no bill-number field**, no timestamps.
- **Granule htm** is a single `<pre>` block. **A major bill's consideration is ONE giant granule** — the NDAA granule (`CREC-2019-07-11-pt1-PgH5594-7`) is **1.28 MB** and contains **21 distinct roll-call votes** (`Roll No. 438`–`458`) plus all amendment debate.
- **Roll-call votes live in the granule TEXT** as `[Roll No. NNN] ... AYES--236 <names> NOES--193 <names> ANSWERED "PRESENT"--N NOT VOTING--N`, preceded by "The question is on the amendment offered by Mr. X" (what the vote is *on*).
- **MODS** carries structured per-member votes: `<congMember bioGuideId="A000370" role="VOTED YES" party="D" state="NC">`. Role values on this granule: `VOTED YES` 4846, `VOTED NO` 4138, `NOT VOTING - NV` 212, `VOTED PRESENT` 2, `SPEAKING` 100. Structured `<bill congress="116" number="75" type="HCONRES"/>` refs present (×8).

> ⚠️ **The MODS votes are FLAT and UNGROUPED.** There is no `rollNumber`/`<vote>`/`<recordedVote>` element — the 8,986 `VOTED*` entries are concatenated across all 21 roll calls with **no key tying a member to a specific vote**. You **cannot** reconstruct per-vote tallies from MODS alone (you'd merge 21 votes into one meaningless tally). **Vote extraction must parse the granule text** (`[Roll No. NNN]` blocks + surrounding "the question is on…" context); MODS is a **cross-check/enrichment** (validates name→bioguide, supplies party/state), not the primary source.

## Question 2 — "What is a Federal-floor agenda item?" (RESOLVED — revises the design)

The **granule is the wrong unit in both directions.** For `CREC-2019-07-11`'s 84 House granules:

| Bucket | ~Count | Destination |
|---|---|---|
| **Printed back-matter** — Constitutional Authority Statements (×38, ~600 chars each), committee reports, exec communications | ~45 | **Discard** — never spoken on the floor; won't align to audio |
| **One-minute / special-order speeches** — "IT IS TIME TO BEGIN AN IMPEACHMENT INQUIRY", "HONORING WILLIAM HENRY WARD" | ~25 | **Attention/topic branch** — member spoke on a topic (NOT agenda) |
| **Procedural furniture** — PRAYER, THE JOURNAL, RECESS, ADJOURNMENT | ~10 | **Holes** |
| **Legislative business** — NDAA consideration, "REQUEST TO CONSIDER H.R. 962", amendment adoptions | ~4 | **Agenda branch** — but must be **sub-granule-segmented** into individual items/votes |

**Three design revisions to the main spec:**

1. **Federal floor is a HYBRID input feeding BOTH branches**, not a clean agenda-branch tier-1. The ~4 legislative granules → agenda branch; the ~25 one-minute/special-order granules → attention/topic branch (like the interview branch's "who spoke on what"). The spec treated Federal as pure agenda-branch.
2. **The agenda item is a SUB-granule unit.** For major bills, one granule = the whole consideration + 21 votes; the real items are the individual amendment debates / roll-call votes *inside* the granule, split on `[Roll No. NNN]` + "The question is on the amendment offered by…" markers.
3. **Item detection is not title-regex.** Bill-number-in-title flags 38 back-matter paperwork granules as "bills." The reliable filter is **spoken-vs-printed** (back-matter is short and won't align to audio) + granule-title semantics + sub-granule vote markers.

## Question 3 — Item timing feasibility (NOT RUN — gated on data)

Requires a processed House-floor `transcript_named.json`; the validated E2E run (#85/#86) was not retained in `~/CouncilScribe/meetings/`. Two mechanisms to test when data is available:
- (a) full granule↔transcript monotonic alignment — expensive against a 1.28 MB granule, and the existing `crec_align.py` deliberately drops timestamps (ADR-0001) so it's a **new** component.
- (b) **anchor on the ASR's own vote-announcement phrases** ("the yeas are 236, the nays are 193", presiding-officer "the question is on…") + the roll number — likely far more reliable than aligning the giant granule. **Recommended hypothesis to test first.**

## Question 4 — Vote → essentials feasibility (RESOLVED, feasible)

- Per-member votes are extractable from granule text (tally + names), with MODS `bioGuideId` as the **canonical join key** — cleaner than the current `crec_essentials.py` last-name search. The existing bridge resolves federal incumbent + chamber + district → `politician_id`; keying on bioguide would be more robust.
- The hard sub-step is **correlating each roll call to the measure/amendment it was on** (free-text back-reference), not the tally itself.
- `essentials_client.search_politicians` hits the ev-accounts API (read-only; not exercised live in this spike to avoid the dependency).

## Net: is the Federal adapter plannable now?

**Mostly yes — with a scoping change that de-risks it.** The one genuinely unproven piece is **item timing (Q3)**. So the first Federal slice should **ship structured content WITHOUT precise per-item timestamps**:

- **Slice 1 (plannable now):** fetch granules with structure → filter back-matter → route legislative granules to agenda branch + one-minutes to attention branch → parse roll-call votes from text (MODS cross-check) → correlate votes to measures → attach votes to `politician_id` via bioguide. Output: agenda items + roll-call results + attention, at **granule-level coarse boundaries** (or no boundaries).
- **Slice 2 (after Q3 de-risk):** precise per-item / per-vote **timestamps** for video navigation, via the ASR-anchor mechanism (3b), validated on a real House transcript.

This matches the spec's own "segmentation ships before precise timing/attention matures" philosophy: ship the structured record first, add jump-to-timestamp navigation once the alignment approach is proven on real data.

## Artifacts

Spike scripts: `scratchpad/spike_granules.py`, `spike_vote.py`, `spike_mods.py`. Existing fixtures: `tests/fixtures/govinfo/` (granule list + presiding/debate htm; **no vote granule — add one from `CREC-2019-07-11` for tests**).

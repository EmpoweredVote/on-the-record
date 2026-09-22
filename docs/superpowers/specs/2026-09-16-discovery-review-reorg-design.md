# Discovery review, reorganized by geography with outlet trust — design

Date: 2026-09-16
Status: Approved (pending spec review)
Area: Discovery review tab (`gui/`, the `/discovery` page) + `essentials.source_outlets` / `essentials.discovered_sources` (ev-accounts DB)

## Problem

The discovery queue (`essentials.discovered_sources`, reviewed at `/discovery`)
has grown past 1000 pending rows. Two things make it hard to keep up:

1. **Repetitive trust decisions.** Most rows come from outlets the reviewer
   already trusts — local network-TV affiliates (NBC/ABC/CBS/FOX). Accepting
   them is repeat work, not judgment. The genuine judgment is the minority of
   unknown channels and sites, but those are buried in the same flat list.
2. **No sense of progress.** The queue is ordered by election date then tier,
   across all races nationwide. Clearing rows never adds up to a finishable unit,
   so the work feels bottomless. The reviewer wants to work one **place** at a
   time — "I finished Monroe County, Indiana" — and to see, per race, how much
   coverage already exists so he can decide whether to move on or dig in.

## Goal

Reorganize `/discovery` around geography (state → county), show coverage counts
per race, and make outlet trust a one-time, outlet-level decision so trusted
sources stop consuming per-row attention.

This design is **Task A** of a larger effort. A separate, parallel task —
**Task B, general-election roster refresh** (finding missing independent /
third-party candidates so discovery searches for them) — is out of scope here
and gets its own spec.

### Slices

Task A ships in two slices. **This spec builds Slice 1.** Slice 2 is described
so the data model and UI are designed for it, but is not built yet.

- **Slice 1 (this ship): reorganized view + trust + auto-approve.**
  - State→county navigation, level-grouped races, four coverage counts per race,
    the "done" cue.
  - Frictionless "Trust outlet"; the barred-for-ingest flag.
  - Auto-approve of trusted outlets' **news-clip** rows (lane 3) as quote
    sources, with provenance, a summary line, and an undo.
- **Slice 2 (follow-up): lanes + hunt.**
  - The full-interview ingest glance group (lane 1).
  - The one-click "chase the primary source" hunt for event clips (lane 2),
    reusing the agent gap-filler machinery.
- **Later (not scheduled): earned auto-qualify** (an outlet auto-trusts on a
  clean track record) and **chain-level ToS modeling** (a chains table instead
  of a per-outlet flag).

## Core model

### Trust is a property of the outlet

The reviewer's accept decision is almost always about the **outlet**, not the
individual video ("this station is fine; take everything from it"). So trust is a
persistent flag on `source_outlets`, decided once.

- **Frictionless.** One control sets an outlet `trusted`. No confirmation prompt.
- **Hybrid activation, manual-only in Slice 1.** Marking an outlet trusted turns
  its auto lane on immediately. An outlet the reviewer has *not* marked can
  earn auto-status later on a clean track record (the existing "mode-C" bar:
  ≥10 reviewed, ≥90% approved, 0 identity-class rejects, already computed
  read-only in `gui/discovery.py::outlet_stats`). **Earned auto-qualify is
  deferred to a later slice.** Slice 1 honors only manual trust.

### Safety invariant: the auto lane never ingests

Auto-approval only ever creates **quote sources** (route `quote_source`). It
never launches an ingest job. Ingest — which spends compute and produces a
hosted transcript — is always a human click. This is what makes frictionless
trust safe: trusting an outlet cannot silently transcribe or publish anything.

### Content lanes

Within an outlet, a row's disposition depends on what it *is*. The classifier
already labels this on every row — `original_vs_clip` (`original` | `clip`) and
`event_kind_guess`. Three lanes:

- **Lane 1 — full interview / full original event.** The most valuable content.
  Disposition: `ingest` (human glance). *Grouping + glance UI is Slice 2; in
  Slice 1 these rows stay pending with today's controls.*
- **Lane 2 — clip of a formal event** (press conference, town hall, debate,
  forum). Not the clip's destination — it is evidence a full primary source
  exists. Disposition: "chase the primary" hunt. *Slice 2; in Slice 1 these
  rows stay pending with today's controls.*
- **Lane 3 — news clip / segment.** A weak lead. Disposition: auto-approve as a
  quote source when the outlet is trusted. **Slice 1 builds this lane.**

Lane derivation (`content_lane(row)`), a pure function of stored fields:

```
lane 1  if original_vs_clip == 'original' and event_kind_guess in FULL_EVENT_KINDS
lane 2  if original_vs_clip == 'clip'     and event_kind_guess in FORMAL_EVENT_KINDS
lane 3  otherwise
```

`FULL_EVENT_KINDS` and `FORMAL_EVENT_KINDS` are small sets drawn from
`src/event_kinds.py::EVENT_KINDS`. Proposed defaults, to confirm against that
enum during planning:

- `FORMAL_EVENT_KINDS = {press_conference, town_hall, debate, forum,
  community_meeting}`
- `FULL_EVENT_KINDS = {interview} ∪ FORMAL_EVENT_KINDS` (an `original` of a
  formal event is itself the full event)

A row with a null or unrecognized `event_kind_guess`, or `original_vs_clip`
null, falls to lane 3. Lane 3 is deliberately the safe default: the worst case
is a full event treated as a quote source, which loses no compute and still lets
a human curate.

### Auto-approve rule

A pending row auto-approves as a quote source when **all** hold:

- its outlet is `trusted`, and
- `content_lane(row) == 3`, and
- `row.race_id is not null` (it must be attributable), and
- optionally, `confidence` clears a floor (a tunable knob; default = the pending
  bar the row already passed).

There is no automated wrong-person signal to gate on. The residual identity risk
— low on real TV stations, but non-zero — is handled after the fact by the
provenance stamp and the undo view (see Reversibility and Risks), and by the
earned bar (later) for any non-manual trust.

Effect: `status := 'approved'`, `route := 'quote_source'`, and a provenance
marker (see below). The row never reaches, or leaves, the human queue.

**Barred outlets still auto-approve lane 3.** The barred-for-ingest flag blocks
the *ingest* route only. Pulling a cited candidate quote from a barred chain is
allowed, so a trusted **and** barred outlet still auto-keeps its news clips as
quote sources. Barred ≠ untrusted.

Two touchpoints apply one rule:

1. **On trust.** Clicking "Trust outlet" sets the flag and retroactively
   auto-approves that outlet's existing pending lane-3 rows (like the current
   `approve_source_family`, but scoped to lane 3).
2. **On ingest.** The daily `poll_discovery` run applies the rule to newly
   inserted rows from already-trusted outlets, so they never appear pending.

Provenance and reversibility:

- Auto-approved rows are stamped `status_reason = 'auto: trusted outlet'`
  (`status_reason` is free text; the `rejected_needs_reason` constraint only
  requires a reason for `rejected`, so this is legal for `approved`).
- The health strip gains a summary line: "auto-kept N this week from M outlets"
  (count of `approved` + `status_reason like 'auto:%'`).
- An **undo**: a filtered view of auto-kept rows with a bulk "return to pending".
  This needs a new path — `set_status_bulk` today only touches
  `pending`/`deferred` rows, so it cannot move an `approved` row back. A narrow
  `unapprove_auto(row_ids)` restricted to `status='approved' and status_reason
  like 'auto:%'` avoids widening the general un-approve surface.

### Barred-for-ingest flag

Some owner chains bar AI/ML use of their content (Nexstar, Gray, Hearst, Graham,
Lee/TollBit per the chain ToS scoreboard); others are clean (Sinclair, Cox,
Scripps). The bar is about **hosting a machine transcript**, not about quoting a
candidate. So:

- New per-outlet boolean `ingest_barred` (default false), seeded from the known
  barred chains by outlet/chain name.
- Effect in the UI: for a row whose outlet is barred, the `Approve → ingest`
  control is disabled with a note — "chain ToS: don't host a transcript; pull a
  direct quote instead." The quote-source path is unaffected.
- Reach: the flag lives on a *registered* outlet. Search-found rows
  (`outlet_id` null) carry no flag; because ingest is always a human click, that
  remains a judgment moment, not a silent risk. Chain recognition for unknown
  channels is a later enhancement.

### Section model (B) and geography

"Everything a county voter votes on." A statewide or federal race appears under
**every** county it covers, but it is one `races` row: approving a source for it
is one action reflected everywhere it shows. There is no per-county copy and no
double work.

To keep the "I finished this county" feeling, **county completion counts local
races only.** Statewide/federal races are shown inside the county for context
but tracked once at the state level; their pending never blocks a county's
"done" cue. So a county's pending badge and done check reflect its
county/city/school races; statewide/federal pending is shown in its own band.

## The reorganized `/discovery` page

(See the wireframe reviewed on 2026-09-16. The mockup shows the full Slice 1 +
Slice 2 vision; the "Hunt full source" button and the lane-1 glance grouping are
Slice 2.)

- **Left rail — sections.** Each state expands to its "Statewide & federal" band
  plus its counties. Each entry shows a pending count, or a check when its local
  races have zero pending. This replaces the flat election-date ordering as the
  primary navigation.
- **Main pane — the selected section.** Races grouped by level in the order
  `federal → state → county → local → school` (matching the ev-accounts admin
  Coverage page's `LEVEL_ORDER`). Each race row shows four counts:
  - candidates (from the race roster, `race_candidates`),
  - quote sources (`discovered_sources` `status='approved' and
    route='quote_source'` for the race),
  - ingested (`status='ingested'`),
  - pending (`status='pending'`).
- **Review nests inside a race.** Expanding a race shows its pending rows grouped
  by outlet, each row tagged with its content lane. Trusted outlets show their
  lane-3 rows already auto-kept (a muted, collapsed summary); unknown outlets
  show the per-row Trust / Approve / Reject controls that exist today.
- **Statewide/federal band** sits at the bottom of each county, visually marked
  "tracked once, approved everywhere."

The existing per-row and source-family actions (`approve → quote source`,
`approve → ingest`, `reject`, `watch channel`, family approve/reject) are
preserved; they are re-laid-out inside the new structure, not removed.

## Data model changes (ev-accounts, `essentials`)

New columns on `source_outlets` (idempotent migration, house `CA_NNNN_` style):

- `trusted boolean not null default false`
- `trusted_at timestamptz null`
- `ingest_barred boolean not null default false`

No change to the `discovered_sources` columns. Auto-approve provenance rides the
existing `status_reason`. The `status` enum already includes every value used
(`pending`, `approved`, `ingested`, `rejected`, …).

Seeding: a data step sets `ingest_barred = true` for outlets whose chain is on
the barred list, and may pre-set `trusted = true` for the network-affiliate
outlets already harvested (optional; the reviewer can also trust them from the
UI as they appear).

## Geography and counts

- **Counts** are simple aggregates over `discovered_sources` grouped by
  `race_id` and `status` (plus a roster count from `race_candidates`). No new
  data is needed.
- **Race → county** is the real work. A race carries no county column; geography
  is derived: local races via `office → chamber → government` and/or
  `office`'s district (`districts.district_type = 'county'`, `geoid`); statewide
  and federal via `elections.state`. The ev-accounts admin Coverage API already
  resolves races to counties (`/admin/coverage/map?level=county&state=…`).
  Slice 1 reuses that logic rather than reinventing it — either by calling the
  endpoint or by porting its SQL into a `gui/` helper (a read-only query against
  the DB the GUI already connects to). **This resolution is the main technical
  risk of Slice 1** (see Risks).

## Slice 1 build list

1. Migration: `trusted`, `trusted_at`, `ingest_barred` on `source_outlets`;
   seed `ingest_barred` from the barred-chain list.
2. `content_lane(row)` pure function + tests (against `EVENT_KINDS`).
3. Auto-approve: `auto_approve_trusted_lane3(...)` applied (a) on trust and
   (b) at the end of a `poll_discovery` run; provenance stamp; summary line in
   `health()`.
4. Trust control (frictionless) + retroactive lane-3 clear; barred-ingest UI
   gate; undo path `unapprove_auto`.
5. Geography: race→county resolution helper (reuse Coverage logic) + per-race
   count aggregates.
6. Reorganized `/discovery` template: left-rail sections, level-grouped races
   with counts, done cue, per-race expand with outlet/lane grouping.

## Non-goals (Slice 1)

- Lane-2 "chase the primary" hunt and lane-1 ingest glance grouping (Slice 2).
- Earned auto-qualify and chain-level ToS modeling (later).
- Task B (general-election roster completeness) — separate spec.
- Moving review into the deployed ev-accounts admin app. Review actions launch
  local jobs and stay in `gui/`.
- Auto-ingest of anything. Ingest remains a human click.

## Risks and mitigations

- **Race→county resolution is fiddly** (districted local offices, at-large
  seats, city-within-county nesting). Mitigation: reuse the Coverage API/SQL,
  which already solved it for the admin map; treat unresolved races as a
  "state — unassigned" bucket rather than dropping them.
- **A wrong-person clip auto-kept from a trusted outlet.** Low on real TV
  stations, but non-zero, and identity is the zero-tolerance error. Mitigations:
  lane-3-only + `race_id` required; provenance + the undo view; the earned bar
  (later) for non-manual trust.
- **Silent volume.** Auto-kept rows disappear. Mitigation: the summary line and
  the auto-kept filter keep them auditable.
- **Barred reach gap** for search-found rows without an outlet. Accepted:
  ingest is always a human click; chain recognition is a later enhancement.

## Testing

Unit tests (house style, alongside the existing `tests/test_discovery_*.py` and
`tests/test_gui_discovery.py`):

- `content_lane` across original/clip × each event kind, including null/unknown.
- `auto_approve_trusted_lane3`: trusted+lane3+race_id approves; trusted+lane1
  does not; untrusted does not; barred+trusted+lane3 still approves; missing
  race_id does not.
- Provenance stamp and the summary count.
- `unapprove_auto` moves only `auto:%` approved rows back to pending.
- Barred-ingest UI gate disables `approve → ingest` and leaves quote-source.
- County resolution: a local race resolves to its county; a statewide race
  resolves to the state band; an unresolved race lands in "unassigned".
- Count aggregates per race and the county "done" predicate (local-only).

## Open questions

- Exact `FULL_EVENT_KINDS` / `FORMAL_EVENT_KINDS` membership — confirm against
  `src/event_kinds.py::EVENT_KINDS` in planning.
- Race→county: call the Coverage endpoint vs. port its SQL into `gui/`. Decide
  in planning based on how much of that query is reusable read-only.

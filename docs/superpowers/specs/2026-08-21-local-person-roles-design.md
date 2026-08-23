# Local-person roles: unblock civic publishing, and give the GUI parity

**Date:** 2026-08-21
**Status:** approved design, not yet implemented

## Problem

Three defects, found while fixing the `local_role` defaulting bug (PR #156, migration `CA_0001`).

**1. Civic meetings cannot publish local people at all.** `meetings.local_people.role` carries
`CHECK (role IN ('candidate','moderator','panelist'))` from migration 623. But
`src/event_kinds.py` offers council / school_board / community_meeting reviewers
`public_comment, staff, official, presenter` — a disjoint set — and `resolve_local_role()` can
emit arbitrary normalised free text besides. 38 of the 40 `local_role` values in the local corpus
are unpublishable.

The live consequence: `2026-02-04-council` has 19 local people recorded locally, with roles a
human assigned. Publish runs in one transaction, so republishing that meeting **aborts entirely**
on a CheckViolation. The meeting is frozen and those 19 people have no `local_people` rows.

**2. The GUI cannot create local people.** `gui/review_api.py` never touches `local_slug` or
`local_role`. There is no `apply_make_local_person`. The entire non-roster-participant flow exists
only as a terminal wizard at `run_local.py:2947`. This is not "the GUI lacks a role dropdown" — a
role control alone would have nothing to attach to. The terminal review is a documented stopgap and
the GUI is the intended future, so the GUI needs parity.

**3. `identity_label` reports the wrong identity.** `src/review.py::identity_label` checks
`politician_slug` before `local_slug` but never `politician_id`. Since `politician_slug` is NULL for
~99.4% of essentials politicians, a federal speaker displays as `local:congress-XXXX` in the review
table. Cosmetic — `src/enroll.py:215` (the enrollment key, which has real consequences) already
checks `politician_id` first and is correct. It is the pattern to copy.

## Decisions taken

**The role's purpose is deferred.** Whether a role is ever shown to a reader is an open question.
This work treats the CHECK mismatch purely as the bug it is and commits to no display contract.
That is what makes a shape constraint the right answer rather than a closed enum.

**Names are already public; the label is not the exposure.** 18 of the 19 people at
`2026-02-04-council` — public commenters included — are **already published by name**, via
`speakers.display_name` and the per-segment `speaker_name`, which have nothing to do with
`local_people`. Publishing these rows adds only the role label. A "withhold public_comment" filter
was considered and **rejected**: it would withhold the words "Public comment" from people already
named on the page, buying no privacy. If not naming private citizens is the goal, that is
transcript-level de-identification — a separate project, specced separately, and it would have to
fix already-live data.

## Section 1 — Constraint layer

**`CA_0003`** (ev-accounts, `CA_` namespace) drops `local_people_role_check` and adds:

```sql
CHECK (role IS NULL OR role ~ '^[a-z][a-z0-9_]{0,39}$')
```

Idempotent, with a post-verify gate asserting the old constraint is gone, the new one exists, and
every stored value satisfies it. `role` stays nullable from `CA_0001`.

The DB stops asserting authority it never had: `resolve_local_role` can already emit free text, so
the value-CHECK only ever blocked what the app actually produced. A shape constraint keeps garbage
out — empty strings, spaces, mixed case, long pastes — without deciding the vocabulary. 40
characters is generous against the longest current value, `public_comment` (14).

**The normalizer must guarantee the shape it feeds.** `resolve_local_role("123 Main St")` currently
yields `123_main_st` — a leading digit the new CHECK rejects. `src/event_kinds.py` gains an exported
`LOCAL_ROLE_RE` (a compiled pattern, plus the raw string the migration comment quotes) as the
canonical definition, and `resolve_local_role` is changed to guarantee it: strip leading
non-letters, truncate to 40, and fall back to the event kind's default role if nothing survives.

Because `resolve_local_role` guarantees the shape, **a role can never be invalid at the boundary** —
every caller, terminal and GUI alike, passes raw input through it. Only a slug can be rejected. That
is what keeps the GUI's error handling to a single case.

The regex cannot literally be shared between Python and SQL. It is duplicated by necessity; the
migration comment quotes `LOCAL_ROLE_RE` by name so the pairing is discoverable. Stated plainly
rather than pretending there is one source of truth.

## Section 2 — One local-person implementation, two front-ends

Four additions to `src/review.py`, which already owns every other speaker-state transition
(`rename_speaker`, `link_speaker`, `mark_unidentified`, `mark_non_speaker`,
`link_to_unidentified_handle`) and is 450 lines. A separate module would fragment speaker-state
logic for no gain.

- `LOCAL_SLUG_RE` — `^[a-z0-9][a-z0-9_-]{0,99}$`, matching ev-accounts' `SLUG_REGEX`. Currently an
  inline literal at `run_local.py:2963`.
- `default_local_slug(name, label)` — the kebab-case derivation, also currently inline.
- `assign_local_person(mappings, label, slug, role)` — the state transition. It **clears
  `politician_id` and `politician_slug`**, enforcing one-identity-per-speaker at the source instead
  of leaving publish to suppress the contradiction afterwards (PR #160 added that suppression; this
  stops producing the contradiction). It **refuses when another label in the same meeting already
  holds that slug** — the identity-collision guard that exists for names and that `local_slug` never
  got.
- `clear_local_person(mappings, label)` — mirrors `link_speaker(None, None)`.

The terminal wizard becomes a thin caller. `speaker_status` handling stays out: promoting an
unidentified handle to a named local person is a different transition with its own rules, and every
existing `review.py` function owns exactly one transition.

Why share rather than reimplement: the `or 'candidate'` bug that started this work existed *because*
publish had to invent what review never captured. A second front-end duplicating the wizard is how
that recurs.

## Section 3 — GUI surface

Two routes in `gui/app.py`, following the existing form-POST-then-redirect shape:

- `POST /meetings/{id}/speakers/{label}/local-person` — fields `slug`, `role`
- `POST /meetings/{id}/speakers/{label}/local-person/clear` — mirrors `/unlink`

The `role` field is passed through `resolve_local_role(raw, event_kind)`, so whatever the reviewer
types becomes a valid role; there is no role-validation failure path. Both routes delegate through
`gui/review_api.py` (`apply_make_local_person`, `apply_clear_local_person`)
using the established pattern: `_load_meeting_ctx` → validate label against known labels → call
`src.review` → `persist_review`.

`SpeakerCard` gains `local_slug`, `local_role` and `default_slug`. `local_role_options` goes on the
page object, not the card — it is meeting-level, from `local_roles_for(event_kind)` — which means
extending the `card` macro signature in `gui/templates/panels/_macros.html`.

In the card's identity group, beside link/unlink: when a local person is set, show
`local: <slug> · <role>` with a Clear button; otherwise the create form.

Two UX decisions, both inherited from the terminal wizard:

- The slug field is **pre-filled** with `default_local_slug(name, label)`, so the common path is
  "pick a role, click" with no typing — the equivalent of accepting the wizard's default. It also
  makes invalid slugs rare rather than routine.
- The role control is `<input list=...>` with a datalist, not a `<select>`. A select would make the
  GUI strictly less capable than the terminal, which advertises "or type a custom role" and has
  tests asserting `"City Attorney"` → `city_attorney`. A datalist gives both paths in one control and
  keeps the free-text branch exercised from the interface meant to replace the terminal.

Validation: the input carries an HTML5 `pattern` so the browser blocks a bad slug inline; the server
validates too and returns **400**, not the silent no-op redirect `set_speaker_name` uses for empty
input, because a silently-ignored submission is the worst outcome for a reviewer. With a valid
pre-filled default, that path is only reachable by a hand-crafted request.

## Section 4 — `identity_label`

Check `politician_id` before `politician_slug`, mirroring `src/enroll.py:215`.

## Section 5 — Sequencing and verification

Order matters; the `CA_0001` work showed what happens when it does not.

1. **on-the-record PR** — `LOCAL_ROLE_RE` + normalizer guarantee, the `review.py` unit, terminal
   wizard rewired, `identity_label`, tests. No DB dependency, safe to land first.
2. **Apply `CA_0003` to prod** — dry run with `COMMIT` swapped for `ROLLBACK`, confirm the rollback
   reverted, then apply. Must precede any civic republish.
3. **GUI PR** — routes, `review_api`, card fields, macro. Separate PR to keep diffs reviewable.
4. **Republish `2026-02-04-council`** — the proof. Expect 19 `local_people` rows.

Verification after step 4:

- 19 rows exist; every `role` matches `LOCAL_ROLE_RE`; the distribution is `public_comment` 13 /
  `staff` 6 (verified against the local artifact). Lisa Lehner — one of the six staff — is the only
  one of the 19 not already published by name, so hers is the single new name this republish adds
- `speakers.local_slug` set for those 19
- 0 speakers carrying both an essentials identity and a `local_slug`
- `kathleen-donham` untouched; `meetings.local_people` otherwise unchanged
- the meeting's summary sections still align after republish — a known hazard on republished
  meetings, to be checked rather than assumed
- the live meeting page still renders those speakers correctly

Tests, TDD throughout:

- `resolve_local_role` output always satisfies `LOCAL_ROLE_RE`, including leading-digit,
  empty-after-normalisation and over-long inputs
- `assign_local_person` clears both essentials fields; refuses a slug held by another label
- `clear_local_person` unsets both fields
- `default_local_slug` / `LOCAL_SLUG_RE` behaviour, including the ev-accounts pattern's edges
- `identity_label` prefers `politician_id`
- GUI: `apply_make_local_person` persists; unknown label returns False; invalid slug rejected;
  route returns 400 on an invalid slug

## Out of scope, deliberately

- **Rendering the role anywhere.** The display question is open; nothing here commits to it.
- **A closed role enum.** Rejected: it decides the deferred display question and breaks tested
  free-text behaviour.
- **Transcript-level de-identification** of public commenters. Separate project if wanted.
- **The unguarded API join.** `meetingsService` LEFT JOINs `local_people` without checking
  `politician_id`; it is harmless now only because publish no longer creates dual identities. Worth
  fixing, not here.
- **`speaker_status` transitions** — see Section 2.

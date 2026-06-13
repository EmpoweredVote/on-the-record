# Speaker → Politician/Candidate Linking — Design

**Date:** 2026-06-12
**Status:** Approved for planning
**Repo:** on-the-record (pipeline side only)

## Problem

The pipeline diarizes a meeting, the reviewer names each speaker, and `publish.py`
writes the result into `meetings.*`. The web people pages, essentials appearance
cards, compass, and read-rank all key a person on `essentials.politicians`
identity (`politician_slug` + UUID `politician_id`). But today a named speaker
only acquires that identity in **one** path: `roster.correct_mappings()` matches
the name against the **current body's council roster** (active seated members of
the one body being processed).

Anyone else — candidates at a forum, a mayor not seated on the council, a
politician from another jurisdiction, a guest — gets a name with **no
politician identity**, so they never connect to the downstream products. The
interactive review loop can name a speaker but cannot attach an ID at all.

## Goal

Let the reviewer link a speaker to any essentials politician/candidate **once**,
during CLI review, and have that link **propagate automatically** to every
future meeting the person speaks in (via voice matching) — with zero schema or
downstream changes.

## Key findings (why this is mostly wiring, not new infrastructure)

The identity plumbing already exists end-to-end:

- `SpeakerMapping` carries `politician_slug` / `politician_id`
  (`src/models.py`), serialized in `transcript_named.json` via
  `to_dict`/`from_dict`.
- `StoredProfile` (voice profile) carries `politician_slug` / `politician_id`
  (`src/enroll.py`).
- `publish.py` already writes `meetings.speakers.politician_slug/politician_id`
  and `meetings.segments.politician_slug`.
- A name-typeahead endpoint over **all** of `essentials.politicians`
  (incumbents **and** candidates — a candidate is just a row with
  `is_incumbent = false`; one ID space) already exists in ev-accounts:
  `GET /api/essentials/candidates/search-by-name?q=` → returns `id`, `slug`,
  `office_title`, `party`, `district_label`, `is_incumbent`, ... (cap 20,
  `q ≥ 2`).

**Two wires are disconnected**, which is the whole reason linking neither
reaches non-roster people nor propagates:

1. **Voice-match drops the link.** `identify.match_voice_profiles()` builds a
   `SpeakerMapping` from a matched stored profile but copies only the *name*,
   not the profile's `politician_slug`/`politician_id`
   (`src/identify.py`, ~L65). So even a returning council member auto-IDs by
   voice yet arrives **unlinked**, relying on a later name re-match.
2. **Only roster matches ever set a profile's identity.**
   `enroll.resolve_enrollment_key()` populates `politician_slug`/`politician_id`
   and the `essentials:<slug>` profile key **only** when the name matches a
   roster member. A manually-named candidate gets a `_name_to_slug` profile with
   `politician_id = None`, and no way to attach one.

Connecting these two wires turns the voice-profile system into the automation
engine the project already half-built.

## Approach (chosen: A — link in the CLI pipeline)

Rejected alternatives:

- **B — web/admin panel only.** `meetings.speakers` already has the columns and
  ev-accounts owns the DB, so an admin editor is feasible, but on its own it
  gives per-meeting manual edits with **no propagation** to future meetings
  unless it also writes back into the pipeline-side voice-profile DB. Bigger
  build (routes + UI + auth), and automation is what's wanted.
- **C — hybrid (CLI now, admin later).** Best capability, most work; the admin
  correction surface is deferred, not part of this design.

Automation comes from the embeddings ↔ `politician_id` link, **not** from the UI
medium. Approach A delivers it with the smallest surface because the fields and
enrollment already exist — we connect two wires and add one search call.

## Components

### 1. `essentials_client.search_politicians(q, *, limit=10, base_url=None)`

New function in `src/essentials_client.py` mirroring the existing
`fetch_body_roster` hardening (connect/read timeouts, 5 MB response cap,
`EssentialsClientError` envelope handling, slug/`base_url` resolution helpers).

- Calls `GET {base}/api/essentials/candidates/search-by-name?q={q}` (incumbents +
  candidates).
- Guards `q` to ≥ 2 non-whitespace chars; raises `EssentialsClientError`
  (`code="INVALID_QUERY"`) below that, mirroring the endpoint's 422.
- Returns a normalized `list[dict]`, each:
  `{politician_id, politician_slug, full_name, office_title, party,
  district_label, is_incumbent, government_name}` (fields pulled from the
  endpoint's flat record; missing values default to `None`/`""`).
- **Best-effort contract:** the only caller (review) catches
  `EssentialsClientError` and degrades; this function never prints or blocks.

### 2. Link action — pure core + interactive prompt

**Pure core** (`src/review.py`, testable, no I/O):

```python
def link_speaker(mappings, label, politician_slug, politician_id):
    """Set (or clear, when both None) the politician identity on a mapping.
    Mutates in place; returns the updated SpeakerMapping."""
```

**Interactive prompt** (`run_local.py`, inside the review loop): after any
rename branch (`[Y]` accept and free-typed name), call
`_prompt_link_politician(mapping, typed_name)`:

- **Skip if already linked** (e.g. roster auto-match set `politician_slug`).
- Otherwise `search_politicians(typed_name)` and show numbered matches:

  ```
  Link to a politician/candidate? (Enter = leave unlinked)
    1. John Hamilton — Mayor, Bloomington · Democratic [incumbent]
    2. John Hamilton — Candidate, IN-09 · [candidate]
  [number] pick · [m] search again · [Enter/s] skip · [n] none/unlink:
  ```

- Inputs: a number → `link_speaker(...)` with that match's slug+id;
  `m` → re-prompt for a new query string and re-search; `Enter`/`s` → leave
  unlinked; `n` → explicitly clear identity.
- **Degrade on failure:** if `search_politicians` raises (offline, API down,
  no matches), print a one-line note and offer `[paste slug manually]` or skip.
  Review never blocks or crashes.

The prompt's pure pieces — formatting a match line, parsing the user's
selection token — are factored into small functions and unit-tested; the
`input()` loop itself is not.

This realizes the "offer for everyone, one-key skip" decision: every named
speaker gets the offer, but a single keystroke moves on, so one-off public
commenters don't slow review.

### 3. Wire 1 — voice-match carries identity

In `identify.match_voice_profiles()`, when `best_name` resolves to a profile in
`profile_db.profiles`, copy that profile's `politician_slug`/`politician_id`
onto the constructed `SpeakerMapping`. (`profile_db` is already a parameter.)
Side benefit: existing roster council members start arriving **pre-linked** by
voice instead of depending on a later name re-match.

### 4. Wire 2 — profile unification on link

Enrollment key resolution must honor an explicit identity already on the
mapping, not only a roster lookup:

- When a mapping carries `politician_slug` (from a manual link or from Wire 1),
  enroll under `essentials:<politician_slug>` with the identity fields set —
  identical to the roster path.
- A pre-existing `_name_to_slug` profile for the same person is **merged** into
  the `essentials:<slug>` profile using the existing merge logic
  (`_enroll_one` / `merge_profiles` / `rename_profile` patterns), carrying
  embeddings, `meetings_seen`, and segment counts. Result: one profile per real
  person, regardless of how they were first named.
- The `_enroll_after_review` path in `run_local.py` (which currently keys via
  `_name_to_slug` directly) routes through the same mapping-aware key
  resolution so manual-link enrollments unify correctly.

Concretely, `resolve_enrollment_key` (or a thin wrapper it feeds) gains a
"mapping already has identity" branch that short-circuits to
`("essentials:<slug>", slug, id)` before the roster lookup.

### 5. Persistence & publish — no changes

`SpeakerMapping` serialization, `transcript_named.json` round-trip, and
`publish.py`'s writes of `politician_slug`/`politician_id` already exist and are
exercised by current tests. No DB schema, people-page, or essentials changes.

## Data flow (after the change)

```
Review: name speaker ──▶ optional link (search_politicians → pick)
                              │
                              ▼
                    SpeakerMapping.politician_slug/id set
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
   transcript_named.json   enroll: profile     publish: meetings.speakers
   (round-trips ids)       keyed essentials:<slug>   + segments.politician_slug
                           with ids (Wire 2)          (already wired)
                              │
                              ▼
            Next meeting: voice match (Wire 1) ──▶ mapping arrives pre-linked
                              │
                              ▼
                       (no human step needed)
```

## Testing

- `search_politicians`: success parse → normalized dicts; `<2`-char guard →
  `EssentialsClientError`; 404/422/5xx/transport/oversize → raises, mirroring
  `fetch_body_roster` tests; mocked `requests`.
- `link_speaker`: sets both fields; `(None, None)` clears.
- `match_voice_profiles` (Wire 1): a stored profile with `politician_id` set →
  resulting mapping carries `politician_slug`/`politician_id`; a profile without
  identity → mapping fields stay `None`.
- enroll (Wire 2): a mapping with `politician_slug` and no roster → profile keyed
  `essentials:<slug>` with ids; a pre-existing `_name_to_slug` profile for the
  same person merges in (embeddings/meetings/counts preserved).
- Prompt pure helpers: match-line formatting and selection parsing.

## Out of scope (deferred, not missed)

- No web/admin panel and no post-publish correction UI (Approach B/C). Today,
  correcting a wrong link = re-name + re-run.
- No back-link pass over already-published meetings. Forward-only: new meetings
  auto-propagate; re-publishing an older meeting after linking picks up the ids.
- No new candidate entity — candidates are `essentials.politicians` rows with
  `is_incumbent = false`.
- No downstream changes (DB schema, people pages, essentials, compass, read-rank).

## Open considerations

- **Wrong-link correction.** Re-naming the same speaker re-opens the link prompt;
  choosing `n` clears it. A profile that was wrongly merged is harder to undo —
  acceptable for now; an admin correction surface (Approach C) is the eventual
  answer.
- **Two speakers → same politician in one meeting** (diarization over-split):
  both mappings get the same slug; `publish` upserts by label, the people page
  shows one person. Acceptable.
- **Offline review.** Fully supported via the degrade path; the person just stays
  unlinked until a later run (or re-publish) links them.

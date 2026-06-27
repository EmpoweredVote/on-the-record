# Bulk Unlinked-Speaker Review Surface (`--bulk-relink-scan` / `--bulk-relink-apply`) — Design

**Date:** 2026-06-26
**Status:** Approved for planning
**Repo:** on-the-record (pipeline / `run_local.py`)
**Context:** Sub-project **C** of the speaker-linking automation effort. Builds
directly on sub-project A (`2026-06-26-relink-person-design.md`,
`src/relink.py`, shipped as PR #28 → main). The decomposition A → B → C → D was
recorded during the slug→id re-key work
(`2026-06-26-speaker-politician-id-rekey-design.md`). This spec covers C only.

## Problem

There is a backlog of **unlinked named speakers**: people who were *named* in
review but never *linked* to an essentials politician, so their transcript
mapping carries `speaker_name` but `politician_id = None`. They stay name-keyed
in the voice-profile DB (~108 name-slug profiles) and are absent from `/people`.
Meetings like the CA governor debates have 10–23 speakers each, mostly unlinked
real candidates (Katie Porter, Antonio Villaraigosa, Tom Steyer, …).

The shipped engine, `run_local.py --relink-person "<name>"`, links **one** named
speaker at a time across every meeting they appear in. Clearing the backlog this
way is slow: one invocation per person, each requiring the operator to know the
target `politician_id` up front for any ambiguous name.

## Goal

A **bulk** surface that enumerates every unlinked named speaker across all
meetings, pre-fills suggested essentials matches, lets the operator approve or
assign every link in one editing pass, and then feeds all approved links through
the existing relink engine (relink transcripts → fold voice profile →
re-publish → optional redeploy) — in a single apply run. It is also the natural
seed of the eventual web review UI: the review file it produces is a stable data
contract a web page can later generate and consume unchanged.

## Decisions (resolved in brainstorming)

- **Surface: a review-file CLI (generate → edit → apply).** Two
  non-interactive commands. `scan` writes an editable file enumerating the
  backlog with pre-filled suggestions; the operator edits it; `apply` applies
  the approved links. Chosen over a live web page (more to build/host now) and
  an interactive terminal TUI (the memory note "review-ui-future-direction"
  warns against over-investing in terminal-only UX). The file is the seam a
  future web UI plugs into.
- **Enumeration: transcripts primary, enriched (fully offline).** Walk every
  meeting's `transcript_named.json` (the authoritative source `publish` derives
  from, and the only one that includes unpublished + debate meetings). Enrich
  each speaker with `has_voice_profile` (from the local profile DB) and a
  `known_id` — any `politician_id` the *same name* is already linked to in some
  other transcript mapping (e.g. Steve Hilton linked in his interview but not
  his debates). No DB query is needed for the scan; the only network call is the
  essentials name search. Every enumerated row is genuinely unlinked, so there
  is nothing to "skip as already published."
- **Suggestion policy: auto-approve only unambiguous matches.** Mirror
  `resolve_link_target`: a name with exactly one `search_politicians` match is
  pre-marked `decision: link` with that id; zero or several matches →
  `decision: review` with candidates listed. Nothing is applied until `apply`
  runs, so every auto-approval is visible and editable first. (A mislink is
  worse than a manual disambiguation — never auto-pick among multiple.)
- **Apply chain: relink + fold + publish, with `race_id` auto-resolution.**
  Loop the relink engine per approved name, then re-publish every touched
  meeting. `--publish-anyway` (default off) passes the review gate. For
  `event_kind=debate` meetings missing a `race_id`, resolve it from
  `essentials.race_candidates` (a linked candidate's `politician_id` → its
  race) and set it before publishing; report any that can't be resolved.
  `--deploy` fires one Render rebuild at the end.
- **File format: YAML.** Friendliest to hand-edit a 100+ row backlog (inline
  candidate hints, low punctuation noise, easy to flip a decision). `PyYAML` is
  already installed in the venv; add it to `requirements.txt` to make the
  dependency honest.
- **Home: new `run_local.py` subcommands**, alongside `--relink-person`,
  `--fix-transcripts`, `--publish-meeting` — not a separate script.

## CLI surface

```
python run_local.py --bulk-relink-scan
    [--out <path>]            # review file path (default: ./bulk_relink_review.yaml)

python run_local.py --bulk-relink-apply <path>
    [--dry-run]               # print the plan; write nothing
    [--publish-anyway]        # pass the publish review gate (as --publish-meeting does)
    [--deploy]                # POST the Render deploy hook once after publishing
```

## Components

Each unit is small and independently testable. Pure logic and serialization
live in a new module `src/bulk_relink.py`, mirroring how `src/relink.py` keeps
logic separate from the `run_local.py` orchestrator. Linking, profile fold, and
publish are **reused** from the existing engine, not reimplemented.

### 1. Unlinked-speaker enumeration — `src/bulk_relink.py`

`UnlinkedSpeaker` dataclass:
- `display_name: str` — a representative `speaker_name` as it appears.
- `normalized_name: str` — lowercased/stripped key used for grouping (same
  comparison `relink_in_meeting` uses).
- `appearances: list[tuple[str, str]]` — `(meeting_id, speaker_label)` pairs.
- `meeting_count: int`.
- `has_voice_profile: bool` — a name-slug profile key exists in the DB.
- `known_id: Optional[str]` — a `politician_id` the same name is already linked
  to elsewhere in the transcripts (the authoritative known id), or `None`. If
  the name is linked to *several distinct* ids, leave `None` (a conflict the
  operator must resolve).
- `decision: str` — suggested decision (`link` or `review`).
- `candidates: list[dict]` — `search_politicians` results (normalized fields).

`enumerate_unlinked(meetings, profile_db) -> list[UnlinkedSpeaker]`
- `meetings` is an iterable of loaded `Meeting` objects (the orchestrator does
  the directory walk and file I/O, matching the `_relink_person` pattern).
- First pass: record, per `normalized_name`, the set of `politician_id`s the
  name is *already linked* to (mappings where `politician_id` is set) — this
  yields `known_id` (single distinct id → that id; multiple → `None`).
- Collect every `SpeakerMapping` where `speaker_name` is set,
  `politician_id is None`, `speaker_status` is normal (not `'unidentified'` /
  `'non_speaker'`), and `local_slug is None` (already routed to a local person).
- Group by `normalized_name`; aggregate appearances and `meeting_count`; attach
  `known_id`.
- Enrich: `has_voice_profile` from `enroll._name_to_slug(name) in profile_db.profiles`.
- Pure; unit-tested with in-memory `Meeting` inputs.

### 2. Match suggestion — `suggest_link(speaker, *, search=search_politicians) -> tuple[str, list[dict]]`
- `speaker` is an `UnlinkedSpeaker` (carries `known_id`).
- **Fast path:** if `speaker.known_id` is set, return `("link", [stub])` where
  the stub is a candidate dict carrying that id (no network call) — the name is
  already linked elsewhere, so reuse the authoritative id.
- Otherwise call `search` (injectable for tests). Exactly one match →
  `("link", [match])`. Zero or several → `("review", matches)`. Mirrors
  `resolve_link_target`'s exactly-one rule; never auto-picks among multiple.
- An `EssentialsClientError` propagates (an outage must not be silently
  rendered as "no matches"); the orchestrator decides whether to abort the scan
  or mark the row `review` with an error note. **Decision: abort the scan** —
  a half-populated file with silent suggestion gaps is worse than a clear
  failure the operator can retry.

### 3. Review-file (de)serialization — YAML

`build_review_doc(speakers) -> dict`
- Produces a YAML-serializable mapping: a top-level `speakers` list, each entry
  carrying `name`, `meeting_count`, `has_voice_profile`, `decision`,
  `politician_id` (the single match's id for `link`, else `null`), and
  `candidates` (only for `review` rows, to keep `link` rows terse).
- The orchestrator dumps it with a header comment block explaining how to edit.

`parse_review_doc(data) -> list[ReviewDecision]`
- `ReviewDecision = (name, decision, politician_id)`.
- Validation: `decision ∈ {link, review, skip}`; a `link` row must carry a
  syntactically valid UUID `politician_id` (reject otherwise with the row name);
  `review`/`skip` rows need no id. Returns rows partitioned by the caller.
- Pure; unit-tested for round-trip and each rejection case.

### 4. `race_id` resolver — `resolve_race_id_for_politicians(cur, politician_ids) -> Optional[str]`
- Queries `essentials.race_candidates` for a race containing any of the given
  linked `politician_id`s, following the `publish._resolve_chamber_id` shape
  (`LIMIT 2`; exactly one distinct race → that id; zero or several → `None`).
- Lives where it can reuse the publish Postgres connection (a helper in
  `src/publish.py` or a small `src/race_lookup.py` imported by the apply
  orchestrator). Unit-tested with a mocked cursor.

### 5. Scan orchestrator — `_bulk_relink_scan(args)` (subcommand handler)
1. `load_profiles()`.
2. Walk `config.MEETINGS_DIR` (skip dotfiles), load each
   `transcript_named.json` via `Meeting.from_dict` — the same walk
   `_relink_person` uses.
3. `enumerate_unlinked` → for each, `suggest_link` to fill `decision` +
   `candidates`.
4. `build_review_doc` → write YAML to `--out` (default
   `./bulk_relink_review.yaml`).
5. Print a summary: total speakers, auto-approved (`link`), needing review,
   and the output path.

### 6. Apply orchestrator — `_bulk_relink_apply(args)` (subcommand handler)
1. Read + `parse_review_doc`; partition into `link` / `review` / `skip`.
2. Print the plan. Any leftover `review` rows → warn and **skip them**
   (don't block the approved links); list them so the operator knows.
3. `--dry-run` → print the full plan (per-name relinks, profile folds, meetings
   that would publish, whether it would deploy) and stop — no writes.
4. For each `link` row: `resolve_link_target(name, explicit_id=politician_id)`
   (display-only — tolerate slug/name miss). Walk the meetings the name appears
   in, `relink_in_meeting`, and write back changed transcripts with
   `Meeting.to_dict` (pretty JSON, matching the existing writer).
5. `rekey_profile_for_link` once per name; `save_profiles(db)` once at the end.
6. Collect every touched meeting. For each: if `event_kind=debate` and
   `race_id` is missing, `resolve_race_id_for_politicians` and set
   `meeting.race_id` (persist to transcript + `PipelineState`) before
   publishing. Then publish via the existing `_publish_meeting_standalone`
   path, honoring `_may_publish` / `--publish-anyway`. Report meetings still
   blocked (unresolved race, or gate without `--publish-anyway`).
7. `--deploy` → POST `RENDER_DEPLOY_HOOK_URL` once (reuse `_trigger_render_deploy`).
8. Closing summary: counts (linked, meetings published, meetings still
   blocked), then — if any rows were skipped as `review` — list those names and
   print the exact command to finish them
   (`python run_local.py --bulk-relink-apply <path>` after editing, or
   `--bulk-relink-scan` for a fresh narrowed list). Apply never rewrites the
   review file.

### Completing leftover `review` rows

No extra tooling is needed; the file is the mechanism and every path is
idempotent:
- **Re-edit the same file** — it persists after apply. Resolve the remaining
  `review` rows and re-run apply; already-linked rows no-op.
- **Re-scan** — because apply writes `politician_id` into the transcripts, a
  fresh `--bulk-relink-scan` no longer enumerates the just-linked people,
  yielding a narrowed file with only the remaining names, re-suggested. This is
  the canonical "what's left" view (reflects actual state).
- **One-offs** — `--relink-person "<name>" --to-id <uuid>` for a single
  stubborn name, without touching the file.

Deliberately *not* added: auto-rewriting the operator's file (surprising), or
emitting a redundant `<file>.remaining.yaml` (re-scan already gives the
canonical narrowed list).

## Data flow

```
--bulk-relink-scan
   │  walk MEETINGS_DIR → unlinked mappings grouped by name
   │  suggest_link per name (search_politicians)
   ▼
bulk_relink_review.yaml   (Steve Hilton → link; Katie Porter → review, candidates…)
   │  operator edits: picks ids for review rows, flips/keeps decisions
   ▼
--bulk-relink-apply bulk_relink_review.yaml
   │  per link row: relink_in_meeting (all appearances) → save transcripts
   │  rekey_profile_for_link → save profile DB once
   │  per touched meeting: resolve race_id if debate → publish (gate/--publish-anyway)
   ▼
(optional --deploy → one Render rebuild)
   ▼
/people now shows the whole approved batch
```

## Error handling / edge cases

- **Essentials outage during scan** → abort with a clear message (no
  half-populated file).
- **Ambiguous / zero matches** → `decision: review`, candidates listed; never
  auto-picked.
- **Leftover `review` rows at apply** → warned + skipped; approved rows still
  apply. (Re-scan or re-edit to finish them.)
- **Invalid `link` row** (missing/malformed UUID) → reject at parse with the
  row name; no writes.
- **`--dry-run`** → no filesystem, profile, DB, or deploy writes.
- **Speaker already linked** → `relink_in_meeting` reports no change; that
  meeting is not re-published (idempotent).
- **No voice profile for a name** → fold is skipped with a note; the DB link
  still publishes.
- **Debate meeting missing `race_id`, unresolvable** → reported and skipped for
  publish (transcript is still linked; operator resolves the race manually).
- **Publish gate blocks** → same message as `_publish_meeting_standalone`;
  honor `--publish-anyway`.
- **Re-running apply** → safe; already-linked meetings no-op, already-published
  meetings re-publish idempotently.

## Testing

- `enumerate_unlinked`: filters out linked / `unidentified` / `non_speaker` /
  `local_slug` mappings; groups by normalized name across meetings; aggregates
  appearances + `meeting_count`; sets `has_voice_profile` from a stub profile
  DB; sets `known_id` when the same name is linked elsewhere (and `None` on
  conflicting ids). In-memory `Meeting` objects.
- `suggest_link`: `known_id` set → `link` via the stub with no search call
  (assert search not invoked); else one match → `link`; zero/multiple →
  `review` with candidates; `EssentialsClientError` propagates. Mocked search.
- `build_review_doc` / `parse_review_doc`: round-trip; rejects bad `decision`,
  rejects `link` row with missing/invalid UUID; accepts `review`/`skip` without
  id.
- `resolve_race_id_for_politicians`: exactly one race → id; zero/multiple →
  `None`. Mocked cursor.
- Apply integration: temp meeting dirs + a small YAML; mock `search_politicians`,
  publish, and the DB; assert transcripts relinked, profile fold invoked,
  correct meetings published, leftover `review` rows skipped, `--dry-run` writes
  nothing.
- Smoke: `--bulk-relink-scan` against real meetings produces a plausible file;
  `--bulk-relink-apply --dry-run` prints the expected plan and writes nothing.

## Out of scope (later / deferred)

- **The web review page** (sub-project direction) — C delivers the YAML data
  contract it will build on, not the page.
- **Routing to local (non-roster) people** — the engine only sets essentials
  identity; such speakers get `decision: skip` (a later flow handles local
  people). The `local_slug` filter keeps already-routed ones out of the file.
- **Creating a voice profile from audio** when none exists — the link still
  publishes; a later normal run / reenroll builds the centroid.
- **Full reenroll** — the cheap profile fold (`rekey_profile_for_link`) stands.
- **Renaming a speaker** — identity linking only; name correction stays with
  `--fix-transcripts`.
- **Auto-link on first pass (sub-project D)** — separate spec.

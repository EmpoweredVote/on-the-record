# Non-Interactive Relink + Auto-Chain (`--relink-person`) — Design

**Date:** 2026-06-26
**Status:** Approved for planning
**Repo:** on-the-record (pipeline / `run_local.py`)
**Context:** Sub-project **A** of the speaker-linking automation effort (the
post-ship follow-up to `2026-06-26-speaker-politician-id-rekey-design.md`).
Decomposition (A → B → C → D) recorded in that conversation; this spec covers
A only. B (one-command orchestration), C (bulk unlinked surface), and D
(auto-link on first pass) are separate, later specs.

## Problem

Connecting a speaker to a politician — or fixing/adding a link after the fact —
is the only step in the publish loop that still requires the **interactive**
`run_local.py` review loop (`_prompt_link_politician`). Everything downstream is
already non-interactive:

- `run_local.py --publish-meeting <id>` re-publishes a meeting from its
  `transcript_named.json` (driven in bulk by `republish_all.sh`).
- `reenroll_profiles.py` rebuilds the voice-profile DB (batch).
- The web redeploy is a single Render deploy-hook POST.

So a retroactive fix like "link Steve Hilton" means re-running interactive
review for a meeting that's otherwise done. Concretely, after the slug→id
re-key shipped, Steve Hilton's transcript mapping is `politician_id=None`
(`id_method=human_review`) — he was *named* but never *linked* — so he stays
name-keyed (`hilton_steve`) and absent from `/people`. There is no way to attach
his `politician_id` without the interactive loop.

## Goal

A single non-interactive command that links a person to an essentials politician
across **every meeting they appear in**, re-keys their voice profile, and
re-publishes the affected meetings — turning a retroactive fix into one line and
serving as the engine the later sub-projects (B/C/D) build on.

## Decisions (resolved in brainstorming)

- **Scope: all appearances, selected by name.** Given a speaker name, the
  command finds every meeting whose `transcript_named.json` contains that
  speaker and relinks each. A `--meeting <id>` flag restricts to one meeting.
  (Matches the existing `_fix_transcripts` cross-meeting walk.)
- **Default chain: edit transcript(s) + re-key profile + re-publish affected
  meetings.** The web redeploy is **opt-in** (`--deploy`) — a static rebuild is
  global and slow, better batched across several fixes.
- **Home: a new `run_local.py` subcommand**, alongside `--fix-transcripts`,
  `--merge-profiles`, `--publish-meeting` — not a separate script.
- **Ambiguous target names refuse and list.** When `search_politicians` returns
  zero or multiple matches, print the candidates and stop, requiring an explicit
  `--to-id <uuid>`. Never auto-pick among politicians (a mislink is worse than a
  manual disambiguation).
- **Profile re-key is the cheap fold, not a full reenroll.** Reuse the existing
  `enroll.promote_unidentified_handle` to move the person's name-keyed profile
  into `essentials:<politician_id>` carrying embeddings/meetings — no audio
  re-extraction.

## CLI surface

```
python run_local.py --relink-person "<transcript name>"
    [--to-id <uuid>]            # explicit essentials politician_id (skips/forces resolution)
    [--to-name "<essentials name>"]  # search essentials by a different name than the transcript's
    [--meeting <meeting_id>]    # restrict to one meeting instead of all appearances
    [--dry-run]                 # print the plan; write nothing
    [--deploy]                  # also POST the Render deploy hook after publishing
    [--publish-anyway]          # pass through the publish review gate (as --publish-meeting does)
```

`--relink-person` selects **which speaker** to relink (by `speaker_name` match).
The **target politician** is resolved from `--to-id`, else `--to-name`, else the
same `--relink-person` string.

## Components

Each unit is small and independently testable.

### 1. Target resolver — `resolve_link_target(query, *, explicit_id) -> ResolvedTarget`
`ResolvedTarget = (politician_id, politician_slug, full_name)`.
- `explicit_id` set → use it; call `search_politicians` only to fetch a
  display name/slug (tolerate `slug=None`; tolerate no search hit — id still
  used).
- Otherwise `search_politicians(query)`:
  - exactly one match → use it;
  - zero or ≥2 matches → raise a `RelinkAmbiguous` error carrying the candidate
    list (the orchestrator prints them and exits non-zero, instructing `--to-id`).
- Pure but for the `search_politicians` call; unit-tested with a mocked client.

### 2. Transcript relink (pure core) — `relink_in_meeting(meeting, speaker_name, politician_id, politician_slug) -> list[str]`
- Find every speaker mapping in `meeting.speakers` whose `speaker_name` matches
  `speaker_name` (case-insensitive; reuse the same comparison `_fix_transcripts`
  uses for consistency).
- For each, call the existing `review.link_speaker(meeting.speakers, label,
  politician_slug, politician_id)`.
- Return the list of labels changed (empty if none matched / already linked).
- **Mappings only** — `Segment` has no politician fields; `publish` derives
  segment identity from the speaker mappings (`slug_by_label`), so editing the
  mappings is sufficient. Pure, no I/O; unit-tested.

### 3. Appearance finder — `find_appearances(speaker_name, *, only_meeting=None) -> list[(meeting_dir, Meeting)]`
- Walk `config.MEETINGS_DIR` (skip dotfiles), load each
  `transcript_named.json` via `Meeting.from_dict`, keep those with a matching
  speaker. If `only_meeting` is set, restrict to that directory.
- Mirrors the `_fix_transcripts` walk.

### 4. Profile re-key (reuse) — `enroll.promote_unidentified_handle`
- Determine the person's current profile key (the name slug via
  `enroll._name_to_slug(speaker_name)`; if absent, scan for a profile whose
  `politician_id` already equals the target or whose `display_name` matches).
- `promote_unidentified_handle(db, handle_key, f"essentials:{politician_id}",
  display_name=full_name, politician_id=politician_id,
  politician_slug=politician_slug)` folds it into the id-keyed profile (creating
  the target if needed), then `save_profiles(db)`.
- No matching profile → skip with a note (the DB link still publishes; voice
  propagation simply has no prior centroid to carry yet). Already
  `essentials:<id>` → no-op.

### 5. Orchestrator — `_relink_person(args)` (the subcommand handler)
1. `resolve_link_target` (on failure/ambiguity: print candidates, exit 2).
2. `find_appearances` (respect `--meeting`); if none, report and exit 0.
3. For each appearance: `relink_in_meeting`; if changed, write the transcript
   back with `Meeting.to_dict` (pretty JSON, matching the existing writer).
4. Re-key the profile once (Component 4).
5. Re-publish each **changed** meeting via the existing `publish_meeting`
   path (respect the review gate; `--publish-anyway` passthrough), reusing the
   loading logic of `_publish_meeting_standalone`.
6. If `--deploy`: POST `RENDER_DEPLOY_HOOK_URL` (from `.env.local`/env), once.
7. `--dry-run`: perform steps 1–2, print the full plan (meetings + labels +
   the profile move + which meetings would publish + whether it would deploy),
   and write nothing.

## Data flow

```
--relink-person "Steve Hilton"
   │  resolve_link_target → (politician_id, slug, "Steve Hilton")
   ▼
find_appearances("Steve Hilton")  → [2026-04-01-ca-courier-stevehiltoninterview]
   │  relink_in_meeting → SPEAKER_00 linked → save transcript_named.json
   ▼
promote_unidentified_handle: hilton_steve ──▶ essentials:<id>   (save profile DB)
   ▼
publish_meeting(interview)  → meetings.speakers.politician_id set
   ▼
(optional --deploy → Render rebuild)
   ▼
/people/<id> now shows Steve Hilton; future meetings auto-link by voice
```

## Error handling / edge cases

- **Ambiguous or no target match** → list candidates, exit non-zero, no writes.
- **`--dry-run`** → no filesystem, profile, DB, or deploy writes.
- **No appearances found** → clear message, exit 0.
- **Speaker already linked to the same id** → `relink_in_meeting` reports no
  change; that meeting is skipped for re-publish (idempotent).
- **No existing voice profile for the person** → skip the re-key step with a
  note; the publish still links them in the DB.
- **Publish gate blocks** (`review_status`) → surface the same message
  `_publish_meeting_standalone` does and honor `--publish-anyway`.
- **Re-running the command** → safe no-op once everything is linked.

## Testing

- `resolve_link_target`: single match → resolved; zero/multi → `RelinkAmbiguous`
  with candidates; `explicit_id` with no search hit → still resolves. Mocked
  `search_politicians`.
- `relink_in_meeting`: matches by name (case-insensitive), sets both
  `politician_id` and `politician_slug` on all matching labels, returns changed
  labels; no match → empty, mapping untouched; already-linked → no spurious
  change.
- Profile re-key path is already covered by existing
  `promote_unidentified_handle` tests; add one asserting a name-keyed handle
  folds into `essentials:<id>` carrying embeddings.
- Smoke: `--dry-run --relink-person "<known speaker>"` against a real meeting
  prints the expected plan and writes nothing.

## Out of scope (later sub-projects / deferred)

- **B — one-command orchestration** of publish-all + reenroll + redeploy across
  arbitrary changes (this spec only chains the meetings a single relink touches;
  `--deploy` is the only redeploy hook here).
- **C — bulk unlinked surface** (enumerate all unlinked named speakers + suggest
  matches). The eventual web review UI lives here.
- **D — auto-link on first pass** (auto-accept high-confidence matches during
  the initial run/review).
- **Full reenroll** for a single relink — the cheap profile fold replaces it.
- **Segment-level transcript fields** — none exist; mappings are the source.
- **Creating a voice profile from audio** when none exists — out of scope; the
  link still publishes, and a later normal run / reenroll builds the centroid.
- **Renaming a speaker** — this tool only sets politician identity; name
  correction stays with `--fix-transcripts`.

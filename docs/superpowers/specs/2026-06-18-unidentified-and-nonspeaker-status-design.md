# Unidentified & Non-Speaker Status Design

## Goal

Give review/enrollment a first-class way to mark speakers that are not normal,
named, tracked people, so the voice-profile DB stops being polluted and stops
merging distinct speakers:

- **Non-persons** (intro/outro music, the Pledge, voiceover, station IDs, video
  clip audio) must never enroll a voiceprint.
- **Unidentified people** (a real, distinct speaker whose name you don't know
  yet) must keep enrolling — but each distinct unknown must stay separate, and a
  recurring one should be recognizable and nameable later.

This fixes the contamination observed after the first full re-enrollment: e.g.
`Moderator (Right)` and `Outro Music` each merged two different meetings into one
profile, purely because the same placeholder label was typed twice and
enrollment keys off the typed label.

## Problem

`SpeakerMapping` has only `politician_slug`, `local_slug`, or nothing — no notion
of "this is not a tracked identity." Two consequences:

1. The only non-person guard is `reenroll_profiles.py` skipping the *exact* names
   `"unknown"/"unidentified"/"n/a"`. Descriptive placeholders ("Outro Music",
   "Pledge") sail through and enroll.
2. Enrollment keys off the typed name. `resolve_mapping_enrollment` returns the
   `essentials:` key from `politician_slug`, otherwise
   `resolve_enrollment_key(speaker_name, roster)` → `_name_to_slug(name)`. It
   never uses `local_slug`. So two speakers labeled the same text collapse to one
   slug and **merge into one voiceprint** — fatal for "identify them later,"
   since it becomes two people's voices in one profile.

## Approach

Add one status field and reuse the existing local-person identity and
voice-match/returning-speaker machinery rather than building a parallel system.

Rejected alternatives: a full `speaker_kind` enum threaded through every consumer
(more invasive, worse default/migration story); pure slug-prefix conventions with
no field (brittle — status logic scattered across string checks).

## Data Model

Add to `SpeakerMapping` (`src/models.py`):

```python
speaker_status: Optional[str] = None   # None=normal | "unidentified" | "non_speaker"
```

`to_dict`/`from_dict` persist it only when set (mirrors the existing optional
fields), so all existing `transcript_named.json` files are unaffected.

- **`None`** — today's behavior, unchanged.
- **`"non_speaker"`** — not a person; never enrolled; excluded from gate-eligible
  speech.
- **`"unidentified"`** — a real distinct speaker without a known name; enrolled
  under a unique generated `local_slug`; recognizable/linkable later.

## Non-Speaker Behavior (`"non_speaker"`)

- **Enrollment** (`enroll_speakers`, `enroll_confirmed`, `reenroll_profiles.py`):
  skip any mapping with `speaker_status == "non_speaker"`, regardless of method.
  This generalizes and replaces the brittle exact-name skip list.
- **Gate** (`src/quality.py::evaluate_meeting`): exclude non-speaker labels from
  the eligible-speech denominator so music/pledge don't dilute coverage.
- **Transcript/exports**: still render the label (e.g. "Outro Music") so the
  transcript reads correctly; the segment is simply flagged as not-a-speaker. No
  speaker identity is published for it.

## Unidentified People (`"unidentified"`) — the collision fix

The merge bug is the name-keyed enrollment. For unidentified people:

1. **Unique handle.** On marking, assign a generated, collision-proof
   `local_slug`: `unidentified-<meeting_id>-<speaker_label>` (deterministic, so
   re-running review is idempotent; unique across meetings by construction).
   The display name is whatever the reviewer types (or "Unidentified Speaker").
   Identity is decoupled from the label.
2. **Enroll by `local_slug`, not by name.** Extend `resolve_mapping_enrollment`:
   when a mapping has no `politician_slug` but has a `local_slug`, key enrollment
   under that `local_slug` (namespaced, e.g. `local:<local_slug>`) instead of
   `_name_to_slug(speaker_name)`. Two "Interviewee 1"s in different meetings then
   land under distinct keys and never merge. *(Side benefit: this also fixes the
   same latent collision for named local people.)*
3. Unidentified mappings still enroll (they are human-confirmed distinct
   speakers), so a voiceprint accumulates for the handle.

## Recognizing a Returning Unknown

Reuse the existing Layer-1 voice match. Today `match_voice_profiles` (Wire 1)
propagates a matched profile's `politician_slug`. Extend it so that when the
matched stored profile is an unidentified handle (carries `local_slug`, no
`politician_slug`), it propagates the `local_slug` + `display_name` and tags the
mapping `speaker_status = "unidentified"`.

- In review, such a match surfaces as **"returning unidentified speaker
  [label]"**.
- **Confirm-only — never auto-merge.** Because false-positive voice matches are
  exactly what caused the earlier collisions, a returning-unknown match is only
  applied when the reviewer confirms it. Distinct unknowns never merge silently.

## Naming an Unidentified Handle Later

When a handle's real identity becomes known, the reviewer renames/links it to the
real person. The handle's accumulated embeddings (across meetings) merge into the
real identity's profile via the existing `merge_profiles` (and the
already-fixed rename re-derivation). Add a thin "promote unidentified handle →
person" path so this is a single action rather than manual profile surgery.

## Review UX

Two new actions in the interactive speaker loop (`_interactive_speaker_review`):

- **Mark unidentified person** — prompts for an optional descriptive label,
  assigns the unique handle, sets `speaker_status="unidentified"`, enrolls.
- **Mark not a speaker** — sets `speaker_status="non_speaker"`; excluded from
  enrollment and the gate.

Both set `id_method="human_review"`, `confidence=1.0`, `needs_review=False`, like
any other human decision. Exact keybindings are an implementation detail for the
plan.

## Review-Flow UX Hardening

The contamination this design fixes was *invisible* during review — the table
showed name/method but not the identity link, and the wrong link only appeared in
the enroll output after confirming. These three additions surface and gate it,
and they compose with the new statuses above.

1. **Show the identity in the review table.** Add an `Identity` column to the
   `_interactive_speaker_review` table rendering each speaker's resolved identity:
   the `essentials:` slug, the local handle, `unidentified`, `non-speaker`, or
   `unlinked`. A mismatch like `Isak Nti Asare → hopi-h-stosberg` is then visible
   at a glance instead of buried in enrollment output.

2. **Pre-enroll safety check.** Before the existing `Enroll these speakers?
   [Y/n]` prompt, render each enrollable speaker as `name → resolved profile key`
   (via `resolve_mapping_enrollment`) and flag, without blocking:
   - **name/slug mismatch** — the linked slug's surname shares no token with the
     name (reuses the `repair_stale_links` heuristic);
   - **duplicate name across labels** — two labels resolve to the same name/key
     (offer to merge them right there — catches the split-speaker case);
   - **named-but-unlinked roster match** — a typed name matches a roster member
     but carries no link.
   This is the backstop that catches contamination *before* it is written.

3. **Per-speaker undo / back.** Add a "back to previous speaker" action to the
   review loop that reverts the last applied change (rename/merge/link/status)
   and re-presents that speaker, so a mistake no longer requires `[Q]` + re-run.
   The loop already accumulates a `changes` list; snapshot the prior mapping
   state per step to support one-level (ideally multi-level) undo.

## Gate / Calibration Treatment

An `"unidentified"` handle is a human-confirmed *distinct* speaker but not a
*named* identity:

- It **counts toward trusted coverage** — the speech was correctly attributed to
  a consistent speaker, which is what coverage measures.
- It is **never scored as a named-identity match** in `bench/calibrate_gate.py`
  precision (there is no name to be right or wrong about); a handle is neither a
  correct nor incorrect identity claim and is excluded from the precision
  numerator/denominator.

A `"non_speaker"` contributes to neither coverage nor precision (excluded from
eligible speech entirely).

## Testing

- `speaker_status` round-trips through `SpeakerMapping.to_dict`/`from_dict`;
  absent field loads as `None`.
- `enroll_speakers`/`reenroll` skip `"non_speaker"` mappings; a meeting of only
  non-speakers enrolls nothing.
- Two mappings in different meetings with the same typed label but
  `"unidentified"` status enroll under **distinct** keys (no merge).
- `resolve_mapping_enrollment` keys an unidentified mapping by its `local_slug`,
  not `_name_to_slug(name)`; a `politician_slug` still wins when present.
- A voice match to an unidentified handle propagates `local_slug` + label and
  tags `speaker_status="unidentified"`, and is NOT applied without confirmation
  (confirm-only path).
- Gate: a `non_speaker` label is excluded from eligible speech; an
  `unidentified` label counts toward coverage but not precision.
- Promote path: naming a handle merges its embeddings into the target identity
  and removes the handle profile.
- Review table renders the resolved `Identity` column for each status
  (essentials / local handle / unidentified / non-speaker / unlinked).
- Pre-enroll check flags a name/slug mismatch, a duplicate name across two
  labels, and a named-but-unlinked roster match (pure-function tests over a set
  of mappings; no TTY needed).
- Per-speaker undo reverts the last rename/merge/link/status change and restores
  the prior mapping state.

## Out of Scope

- No automatic clustering of unknown voices into handles — handles are created by
  explicit human marking only.
- No change to the essentials/roster linking flow for named people beyond the
  shared `local_slug`-keying fix.
- Backfilling the already-polluted profiles (e.g. `Outro Music`, `Moderator
  (Right)`) is a one-time data cleanup, handled by re-marking those speakers in
  review and re-enrolling — not new code in this spec.
- **Clip-control input (separate diagnosis).** While a clip plays, shortcut keys
  (next clip, replay, etc.) often don't register — the `ffplay` video window
  appears to steal keyboard focus on macOS, so keys reach the player, not the
  line-based prompt. The fix (single-keypress raw mode vs. a non-focus-stealing
  player) depends on reproducing the root cause, so it is tracked as its own
  diagnose-then-fix rather than designed here. A terminal `input()` cannot
  reliably capture modifier-key chords, so that approach is not viable.
- **Roster-cache name/slug quality.** Garbled cached names ("City Common Council
  - At Large Asare") and UUID-suffixed slugs are a roster-refresh data-quality
  issue, tracked separately.

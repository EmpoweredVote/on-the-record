# Renaming a speaker keeps its identity

**Date:** 2026-09-09
**Repo:** `on-the-record`
**Follows:** `2026-09-08-speaker-identity-picker-design.md` (PR #203, commit `5246ffc`)

## The problem

The review card has two places to set a speaker's name, and they behave
differently.

- The local-person panel's `Name` field posts to
  `POST /meetings/{id}/speakers/{label}/local-person` ->
  `gui.review_api.apply_make_local_person`, which does clear-status -> rename ->
  assign. The identity survives, because it is set in the same write.
- The *Also* block's **Display name** box posts to
  `POST /meetings/{id}/speakers/{label}/name` -> `gui.review_api.apply_rename`
  -> `src.review.rename_speaker`. At `src/review.py:203-213`, whenever the final
  name differs from the old one, `rename_speaker` sets `local_slug = None`,
  `local_role = None`, and — with no roster — nulls `politician_slug` /
  `politician_id`.

So fixing a typo in the Display name box deletes the local person or the roster
link shown one line above it on the same card.

PR #203 mitigated this and said so: it dropped the box's `value=` prefill and
added an amber warning line, "Saving a different name here drops the current
identity." A curator who reads the warning and proceeds still loses the
identity. That PR's own review recorded the real fix as out of scope.

## Why `rename_speaker` clears

Its comment at `src/review.py:196-202` is sound for the case it was written for.
A human-assigned name is authoritative, so a prior identity belonged to the OLD
name — a voice-profile collision later corrected by hand, say — and must not
survive a name change, or the voice enrolls under the wrong person, because
`enroll.resolve_mapping_enrollment` keys on `politician_id` ahead of the name.

That reasoning covers correcting a WRONG name whose link came from the wrong
person. It does not cover the case the four-outcome picker made common: a
curator adjusting the spelling of a name they themselves attached to an identity
they deliberately chose.

## The distinction, and where it lives

The distinction is not a property of the mapping. It is a property of the
**caller**.

- The **GUI card** has separate, explicit controls for every identity outcome:
  roster link, local person, unidentified, not-a-speaker, plus unlink and
  clear-local-person. Nothing there needs the rename box to change an identity,
  and the box is the only control on the card whose name does not say what it
  will do.
- The **terminal review** has no such controls. There, rename *is* the identity
  flow: `rename_speaker` -> `_prompt_link_politician` -> `_prompt_create_local_person`
  (`run_local.py:3300`, `3324`). Clearing the link is what makes the re-link
  prompt reachable.

So the GUI rename becomes name-only, and `src.review.rename_speaker` keeps its
current semantics untouched.

### Options rejected

**Key the clearing on `id_method` inside `rename_speaker`.** Rejected for two
reasons. `run_local._prompt_link_politician:2979` returns immediately when
`politician_slug` or `politician_id` is set, so a surviving link would make the
terminal's re-link prompt unreachable — the terminal curator would lose the only
correction path in that flow. And `id_method` describes the last *name*
assignment, not the identity: `link_speaker` never sets it, so the key is only
accidentally correlated with "a human chose this identity".

**Refuse the rename at the route and send the curator to the identity panel.**
Rejected because it leaves the local-person panel as the only way to fix a local
person's published name, and that path forces slug and role to be re-confirmed
to change one letter. It also leaves the unidentified-handle bug below in place.

**A `preserve_identity` parameter on `apply_rename`.** Rejected as dead
configuration: `apply_rename` has exactly one production caller,
`gui/app.py:345`. The parameter would never be passed `False`.

## Design

### `src/review.rename_preserving_identity`

New function beside `rename_speaker`, same signature and same `RenameResult`:

```python
def rename_preserving_identity(mappings, segments, label, new_name, *, roster=None) -> RenameResult:
```

It snapshots the four identity fields — `politician_slug`, `politician_id`,
`local_slug`, `local_role` — calls `rename_speaker`, and restores all four
verbatim, but **only when at least one of them was set**.

Two properties follow from restoring verbatim rather than re-deriving:

- **The one-identity-per-speaker invariant (ev-accounts migration 623) cannot
  break.** The snapshot was one identity when it was taken, so it is one
  identity when it is put back. No new code has to enforce what `link_speaker`
  and `assign_local_person` enforce.
- **A rename cannot fail.** Re-applying through `assign_local_person` would
  re-validate the slug against `LOCAL_SLUG_RE` and could raise on a stored slug
  that no longer passes, turning a name edit into a 500.

The "at least one was set" guard keeps today's useful behaviour intact: on a
card with no identity at all, `rename_speaker`'s roster branch may still derive
a link from the new name, and that derivation is kept.

### `gui.review_api.apply_rename`

Calls `rename_preserving_identity` instead of `rename_speaker`. It still passes
the meeting's roster, which still normalises the typed name through
`correct_speaker_name` and still derives a link for an identity-free card.

### The enrollment consequence

The original comment's hazard is that a surviving link keys the voice profile to
the wrong person. After a preserving rename of a roster-linked speaker,
`enroll.resolve_mapping_enrollment` returns `essentials:<the same politician_id>`
— the person the curator deliberately linked, which is the right answer for a
spelling fix. This is pinned by its own test, not inferred from the rename test.

For an `unidentified` card the change also **fixes** an enrollment bug.
`local_slug` there is the synthetic `unidentified-<meeting>-<label>` handle whose
whole purpose is keeping two distinct unknown speakers from sharing one
enrollment key. Today a rename nulls it, so `resolve_mapping_enrollment` falls
back to the name and unrelated strangers collapse onto one profile — precisely
the collision `clear_local_person` refuses to cause. Preserving the handle stops
that.

### Known trade-off

The `✓ Accept <name>` button posts to the same `/name` route. On a card that is
linked but has no name, accepting a voice-hint name now keeps the existing link
instead of re-deriving from the hint. That state is rare — `identify` sets name
and link together — and where it occurs the mismatch is visible on the card and
fixable with the chooser, rather than resolved by silently deleting the link.

### Card wording and prefill

PR #203's warning is replaced, not removed. Leaving a warning about something
that no longer happens is worse than no warning. The condition stays
`identity_kind != 'none'`, which is accurate for all four kinds, since
`rename_speaker` never touched `speaker_status` and a marked card keeps its
mark. The amber `.ident-cost` class becomes a neutral `.ident-note`:

> Saving a name here keeps the current identity — use the chooser above to
> change who this is.

The `value=` prefill returns, as `value="{{ c.name or '' }}"`. PR #203 removed it
because prefilling put a real identity one typo-fix away from deletion; that
reason is exactly what this change removes. Fixing a typo now means editing one
letter instead of retyping the name from scratch, which is the point. An empty
submission is already a route no-op, so a blank box on a nameless card behaves
as it does today.

## Testing

1. **The failing test first**, through the real route. `TestClient` POSTs to
   `/meetings/{id}/speakers/{label}/name` on a card holding a local person, and
   asserts `local_slug` and `local_role` survive. The same for a roster-linked
   card's `politician_id`.
2. **The enrollment key.** After a preserving rename of a roster-linked speaker,
   `resolve_mapping_enrollment` returns `essentials:<same politician_id>` — not a
   name-derived slug and not another person.
3. **The unidentified handle** survives a rename and still keys `local:<handle>`.
4. **`rename_speaker` is unchanged.** A direct-call test pins the terminal
   semantics — a changed name still clears the identity and still re-derives from
   a roster — so a later edit cannot quietly move the terminal onto the new
   behaviour.
5. **A no-identity card still gets its roster-derived link**, using the
   `fake_roster_cache` fixture. Every other test in `tests/test_gui_review.py`
   uses `body_slug="x"`, which `load_roster` fails to find, so `_load_roster_for`
   returns `None` and the roster branch is never exercised there.
6. `test_the_also_rename_box_warns_only_when_an_identity_would_be_dropped` is
   renamed and re-pointed at the new line and the restored prefill.

## Documentation

`docs/superpowers/specs/2026-09-08-speaker-identity-picker-design.md`, section
"The name travels with the identity": the paragraph beginning "The **Display
name** box in the *Also* block stays" and the "The fix:" paragraph both become
false and are rewritten to describe the route-level preservation. The ordering
rules above them stay as they are — they still govern `_reset_and_rename`, which
still calls `rename_speaker` directly and still depends on its clearing
behaviour.

## Out of scope

- Any change to `src.review.rename_speaker` itself. Branch
  `claude/great-allen-6f0746` is editing that function for the fuzzy-match
  hazard; this work stays additive so the two merge cleanly.
- Whether the `✓ Accept` button should carry its own identity semantics.

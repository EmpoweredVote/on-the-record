# Auto-derived meeting IDs are rich and kind-aware

## Status

accepted

## Context & Decision

A meeting's directory name doubles as its `meetings.meetings.slug` and is frozen at creation ([ADR-0002](0002-meeting-url-identity-and-frozen-slug.md)). It was auto-derived as `{date}-{slug(meeting_type)}` — e.g. `2026-02-10-regular-session` — which carries no locus: it doesn't say which council, which outlet, or which race, and two same-labelled meetings on one date collided into a `-2` suffix.

We decided the auto-derived ID should be **rich and kind-aware**: `{date}-{locus}-{label}`, where `label` is the slugged event label (as before) and `locus` is the most identifying context available at creation time, chosen by event kind:

| Kind | `locus` (first non-empty) |
|---|---|
| council / school_board | roster body-slug → city |
| community_meeting / other | city → event org |
| debate / forum | race → event org → city |
| news_clip / press_conference / podcast | guest, then race (joined) → event org |
| floor | *(none — the chamber is already in the label, e.g. "House Floor")* |

The race token comes from a picker backed by `essentials.races` (`position_name`, normalized: "U.S. Senate Alabama" → `senate-alabama`); the guest is an operator-typed name folded into the slug only. Guards: whole-hyphen-token overlap de-dup (never `bloomington-bloomington-…`), an 80-char cap, and the existing `is_safe_meeting_id` + `-N` collision suffix.

## Considered Options

- **Rich derivation, new-meetings-only (chosen).** Descriptive, self-documenting URLs and near-elimination of collisions, at zero risk to existing meetings.
- **Keep the terse ID, add context only in the UI.** No URL change, but the ID itself stays uninformative — rejected as not solving the operator's actual complaint.
- **Compact state-code race slugs (`ca-governor`, `tx-senate`).** Cleaner for statewide races but requires a state-name→code map + office-keyword extraction that degrades on the many local/district/multi-seat races. Rejected in favor of the robust `position_name`-derived slug.

## Consequences

- **New meetings only.** Derivation runs solely from `runner.launch_run`; `--resume`/`--redo` never re-derive, so every existing slug is untouched — ADR-0002's freeze is preserved.
- The ID is longer (e.g. `2026-05-01-becerra-ca-governor-interview`), bounded by the 80-char cap.
- Picking a race also sets `race_id` → `--race-id`, so the meeting is linked to its race at creation (previously a manual CLI step). The new-meeting form gates each kind-specific field server-side so a field that doesn't apply to the chosen kind can't leak into the launch.
- The client-side ID preview mirrors the server rule (including the token-boundary de-dup); the server (`runner.derive_meeting_id`) remains authoritative.

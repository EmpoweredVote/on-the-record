# Reject a source family with one checkbox — design

Date: 2026-09-09
Status: Approved (pending spec review)
Area: Discovery review tab (`gui/`)
Builds on: `2026-09-09-approve-source-family-design.md` (merged, PR #204)

## Problem

The "approve a source family" feature (merged) lets one click approve every
pending row from the same source. Reject has no equivalent. A reviewer who wants
to reject a whole outlet — say a tier-5 source, or a chain they will never use —
still rejects each row one at a time.

But reject is not symmetric with approve. An approve almost always applies to the
whole outlet, so it fans out by default. A reject reason often applies to just
one video ("clip, not original", "wrong person"). So a whole-source reject must
be **opt-in**, never the default.

## Goal

On the Discovery review page, give the reviewer a way to reject the clicked row
**and every pending row from the same source**, in one action, without changing
the normal one-row reject.

Non-goals:
- Do not change the default reject. Unchecked, reject behaves exactly as today.
- Do not fan out on the deferred view or for a row with no siblings.

## Definition: "the same source"

Identical to the approve feature — the row's **source key** by precedence:
`outlet_id`, else `channel_id`, else normalized `channel_name`
(`(channel_name or "").strip().lower()`). Reuses the existing `family_key(row)`.
Whole-queue scope, across every race. Only `status = 'pending'` rows are changed.

## Behavior

### The checkbox

The reject control keeps its reason dropdown and **Reject** button unchanged.
When the row has siblings — i.e. the `family_count` the page already computes for
the approve button is greater than 0 — a checkbox appears in the reject form:

> ☐ apply to all 6 from this source

The count shown is `family_count + 1` (the whole source, including the clicked
row). Approve's button says "(+5 more)" (the others); reject says "all 6" (the
whole source) — different phrasing for the two mental models, intentionally.

- **Unchecked** (default) → rejects only the clicked row, with the chosen reason.
  Byte-for-byte today's behavior.
- **Checked** → rejects the clicked row and every pending sibling, all with the
  one reason picked from the dropdown.

No checkbox renders for a loner (`family_count == 0`) or on the deferred view
(where `family_count` is left at 0), so the per-video reject case looks exactly
as it does now.

### The reason

A whole-source reject applies one reason to all of the siblings. That is
inherent, and correct for the whole-source case (tier 5, stale). The opt-in
checkbox is exactly what protects the per-video reasons: leave it unchecked.

### The route

`discovery_reject` gains a `whole_source` form field:

- `whole_source` truthy → `n = discovery.reject_source_family(row, reason)`;
  flash `rejected {n} ({row.channel_name or 'source'})`, or the existing
  `rejected — SAVE FAILED, retry` when `n == 0`.
- otherwise → the current single-row `discovery.set_status(row_id, "rejected",
  reason=reason)`, flash `rejected`.

The existing guards stay: `if row is None: 404`, and the
`if row.status != "pending": return "already <status>"` short-circuit (so a
non-pending clicked row rejects nothing).

## Components and files

### `gui/discovery.py`

- **Extract** `_family_where(row) -> tuple[str, str]` — a pure helper returning
  the `(where_clause, value)` pair from the source key: the hardcoded match map
  (`outlet_id = %s::uuid` / `channel_id = %s` / `lower(btrim(channel_name)) = %s`)
  and the keyless `id = %s::uuid` fallback. This is the injection-sensitive part,
  now shared. The `where_clause` is always drawn from the hardcoded map or the
  literal — never from row data.
- **Refactor** `approve_source_family(row)` to call `_family_where(row)`. Its SQL
  (`set status = 'approved', status_reason = null`), its parameter tuple
  (`(val,)`), and therefore its existing unit tests are unchanged — only the
  extraction of the where/val computation moves into the helper.
- **Add** `reject_source_family(row, reason) -> int` — calls `_family_where`,
  runs `update … set status = 'rejected', status_reason = %s, reviewed_at = now()
  where status = 'pending' and {where}` with params `(reason, val)`. Returns the
  rows-changed count, 0 on missing DB url or DB exception (mirrors the module's
  other DB functions).

### `gui/app.py`

- `discovery_reject`: add `whole_source: str = Form("")`. When truthy, call
  `reject_source_family` and flash the count; otherwise keep the single-row
  path. Keep both guards.

### `gui/templates/discovery.html`

- Inside the reject `<form>`, when `r.family_count` is truthy, render a labelled
  checkbox `<input type="checkbox" name="whole_source" value="1">` reading
  `apply to all {{ r.family_count + 1 }} from this source`. The reason dropdown
  and Reject button are untouched.

## Testing

- `_family_where`: returns the right clause + value for outlet / channel / name
  keys and the keyless `id` fallback (precedence identical to `family_key`).
- `approve_source_family`: existing tests still pass unchanged (regression guard
  that the refactor preserved behavior).
- `reject_source_family`: sets `status = 'rejected'` with the reason bound as a
  parameter (`params == (reason, val)`); matches by each key type and keyless;
  only `status = 'pending'` rows are touched; returns the count; returns 0
  without a DB url.
- `discovery_reject` route: with `whole_source=1`, calls `reject_source_family`
  and the flash reports the count; without it, still calls the single-row
  `set_status` path and flashes `rejected`; a non-pending clicked row
  short-circuits with `already …` and rejects nothing.
- Page render: the whole-source checkbox appears only when `family_count > 0` and
  only on the pending view; absent for a loner and on the deferred view.

## Risks

- **Wrong-reason blast.** A checked box applies one reason to the whole source.
  Mitigated by opt-in default and the count in the label ("all 6"). Reject is
  reversible via the existing bulk "restore to pending".
- **Count vs. action drift.** The checkbox count comes from the page snapshot;
  the action re-queries the DB (pending-only). If the queue shifted between load
  and click, the flash count is the truth — same accepted trade-off as approve.

# Approve a source family in one click — design

Date: 2026-09-09
Status: Approved (pending spec review)
Area: Discovery review tab (`gui/`)

## Problem

On the Discovery review page (`/discovery`), discovered sources are grouped by
race and reviewed one row at a time. A single outlet often appears many times in
the queue — e.g. six "Wisconsin PBS" rows. Approving each one as a quote source
takes six separate clicks even though the decision is the same for all of them.

## Goal

When the reviewer approves one row **as a quote source**, also approve every
other pending row from the same source, anywhere in the queue, in that one click.

Non-goals:
- `Approve → ingest` stays a single-row action. It launches a processing job per
  item, so it is never fanned out.
- No new undo path. Approving as a quote source is non-destructive (it only flags
  a candidate for the race pipeline; nothing auto-ingests).

## Definition: "the same source"

A row's **source key** is chosen by precedence:

1. `outlet_id` if present (a registered outlet), else
2. `channel_id` (YouTube channel), else
3. normalized `channel_name` — `channel_name.strip().lower()`, e.g.
   `"wisconsin pbs"`.

A row with none of the three has no key: it approves only itself.

Two rows are siblings when they resolve to the **same key on the same field**.
Consequences of the precedence:

- A row that has an `outlet_id` matches only by that id. Its `channel_name` is
  ignored, so two different outlets that happen to share a name never merge.
- Only id-less rows fall back to matching on `channel_name`.
- Asymmetric edge case (accepted): if the same real outlet appears both as
  id-bearing rows and as id-less name-only rows, the two sets do not merge. In
  practice the discovery engine sets `outlet_id` on every row that came from a
  registered outlet, so a registered outlet's rows cluster on `outlet_id` and
  unregistered search/agent finds cluster on `channel_name`; the mixed case is
  rare.

**Scope:** the fan-out spans the whole pending queue, across every race — not
just the race group the clicked row sits in.

**Route is ignored.** Matching is on source identity only. If one sibling was
tagged route `ingest` and the rest `quote_source`, approving fans out to all of
them as quote sources. Marking a row a quote source is harmless — nothing
auto-ingests — and this matches the reviewer's intent ("approve all the Wisconsin
PBS in my queue"). Explicitly decided, not an oversight.

## Behavior

### Button pre-labels the sibling count

The pending page already loads every pending row into memory. The per-row sibling
count is computed in Python from that list — no extra DB round-trip. The button
renders:

- `Approve → quote source (+5 more)` when the row has 5 pending siblings.
- `Approve → quote source` when the row is a loner (count 0).

The count is shown only on the **pending** view. On the deferred view the button
stays plain, because the quote-source action only acts on pending rows (a
quote-source click on a deferred row short-circuits with `already deferred`).

### One click approves the family

Clicking `Approve → quote source` recomputes the clicked row's source key
**server-side** and runs one statement:

```sql
update essentials.discovered_sources
set status = 'approved', status_reason = null, reviewed_at = now()
where status = 'pending' and <key match>
```

where `<key match>` is one of:

- `outlet_id = %s::uuid`
- `channel_id = %s`
- `lower(btrim(channel_name)) = %s`

Doing the match in the database (rather than trusting the ids rendered into the
page) keeps it authoritative if the queue shifted since the page loaded. The
statement returns the number of rows changed.

The clicked row matches its own key, so it is included in the count. A row with no
key approves only itself (falls back to the existing single-row update).

Flash message: `approved 6 (Wisconsin PBS)` — count first, then the clicked row's
`channel_name` (or `source` when the name is missing).

The existing guard is kept: the handler acts only when the clicked row's status
is `pending`; otherwise it returns `already <status>` and fans out nothing.

## Components and files

All changes are small and confined to the Discovery tab.

### `gui/discovery.py`

- `family_key(row) -> tuple | None` — pure function returning `("outlet", id)`,
  `("channel", id)`, `("name", normalized)`, or `None`. Used for the in-memory
  count so the button label and the DB action agree on the same precedence.
- `approve_source_family(row) -> int` — derives the same key, translates it to the
  matching `where` clause, runs the update, returns the rowcount. On a row with
  no key it approves just that row (equivalent to today's single update). Returns
  0 on DB failure, consistent with the other functions in this module.
- `DiscoveredRow` gains a `family_count: int = 0` field (default keeps every
  existing constructor call and `_to_row` unpacking valid, since it is not
  selected from SQL).

### `gui/app.py`

- `discovery_page`: after loading `rows`, when `status == "pending"`, group by
  `family_key` and set each row's `family_count` to (group size − 1). Left at 0
  for the deferred view and for keyless rows.
- `discovery_quote_source`: replace the single `set_status(row_id, "approved")`
  with `n = approve_source_family(row)`; flash
  `approved {n} ({row.channel_name or 'source'})`. Keep the `status != "pending"`
  short-circuit and the `SAVE FAILED` fallback when `n == 0` but the row was
  pending.

### `gui/templates/discovery.html`

- The quote-source button label appends ` (+{{ r.family_count }} more)` when
  `r.family_count` is truthy.

## Testing

- `family_key` precedence: `outlet_id` wins over `channel_id` and name;
  `channel_id` wins over name; name used only when both ids are absent; name is
  normalized (case/whitespace); all-absent → `None`.
- `approve_source_family`: approves siblings across different races; a row with an
  `outlet_id` does not pull in a same-name row that has a different `outlet_id`;
  an id-less name-only row pulls in same-name id-less rows; a keyless row approves
  only itself; only `pending` rows are touched (already-`approved`/`rejected`/
  `deferred` rows are left alone).
- `discovery_page`: `family_count` is (siblings − 1) on the pending view and 0 on
  the deferred view.
- `discovery_quote_source` route: clicking approves the whole family and the flash
  reports the count; a non-pending clicked row short-circuits with `already …`.

## Risks

- **Over-reach across the queue.** Whole-queue scope means one click can clear an
  outlet everywhere. Mitigated by the pre-labeled `(+N more)` count so the reach
  is visible before clicking, and by the action being non-destructive. No undo is
  added, per decision.
- **Count vs. action drift.** The button count is computed from the page snapshot;
  the action re-queries the DB. If the queue changed between load and click, the
  flash count is the truth. The label uses `btrim`/`strip` normalization that can
  differ on exotic whitespace in a channel name — accepted as negligible for real
  outlet names.

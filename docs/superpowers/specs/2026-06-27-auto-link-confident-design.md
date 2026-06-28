# Auto-Link High-Confidence Matches on First Pass — Design

**Date:** 2026-06-27
**Status:** Approved for planning
**Repo:** on-the-record (pipeline / `run_local.py`, `src/relink.py`)
**Context:** Sub-project **D** (final item of the A → B → C → D decomposition;
A/B/C/E shipped). Attacks the unlinked-speaker backlog *at the source*: instead
of leaving named speakers unlinked for the bulk tool (C) to clean up later,
auto-link the high-confidence ones during the initial run/review.

## Problem

Today a speaker is *named* during a run (roster / LLM / voice), but linking it to
an essentials politician (`politician_id`) requires the **interactive**
`_prompt_link_politician` step. So:

- Non-interactive / `--no-review` runs name speakers but never link them — every
  confident match (e.g. a debate candidate named "Katie Porter" with exactly one
  essentials match) is left unlinked and accumulates in the backlog.
- Even in interactive review, the operator must pick from a list for every
  speaker, including obvious single matches.

The bulk surface (C) cleans this up after the fact, but the backlog keeps
regenerating. D stops it at the source.

## Goal

After speakers are named, **auto-link the high-confidence ones** — set
`politician_id` with `id_method="auto_linked"` — in both the interactive and
non-interactive run flows, using a conservative strong-match bar. Auto-linking
sets identity only; it never auto-publishes (the confidence gate is unchanged).

## Decisions (resolved in brainstorming)

- **Reach: everywhere (interactive + non-interactive), marked `auto_linked`.**
  Auto-link confident matches during interactive review (the obvious ones need
  no prompt) AND during `--no-review`/non-interactive runs (so unreviewed
  meetings stop accumulating unlinked speakers). The distinct `id_method`
  ("auto_linked", vs "human_review"/"human_confirmed") makes auto-links
  auditable and bulk-reversible.
- **Confidence bar = exactly one STRONG match, or a known id.** A single
  essentials match counts only when it's a strong name match — every
  whitespace token of the normalized speaker name appears as a whole word in the
  candidate's `full_name` (case-insensitive). So "Steve Hilton"→"Steve Hilton"
  ✓; "Host"→"Matthew Hostettler" ✗ (substring, not a token); "Councilmember
  Rollo"→"David R Rollo" ✗ (title prefix isn't a token). Zero / multiple / weak
  → left unlinked. (A known id the same name is already linked to is also
  high-confidence.)
- **Conservative by design.** A mislink is worse than a manual disambiguation,
  so the bar is strict and ambiguous cases are left for the operator / bulk tool.
- **Does NOT auto-publish.** Auto-link sets `politician_id` in the transcript;
  publishing stays gated/manual.
- **Out of scope: `suggest_link` / the bulk scan.** The Host→Hostettler false
  auto-link in `src/bulk_relink.py` `suggest_link` is being fixed by a separate,
  already-in-flight task; D must NOT touch `bulk_relink.py`/`suggest_link` to
  avoid colliding with it. D implements its own strong-match check in
  `confident_target`; a later cleanup can DRY the two once both land.

## Components

### 1. `confident_target(name, *, search=search_politicians, known_id=None) -> Optional[ResolvedTarget]`
New, in `src/relink.py` beside `resolve_link_target` (reuses `ResolvedTarget`).
- `known_id` set → `ResolvedTarget(known_id, slug?, name)` (already linked
  elsewhere; highest confidence; slug best-effort from a search hit, else None).
- Else `search_politicians(name)`: return the single match **only if** there is
  exactly one AND it is a strong name match (every token of the normalized
  speaker name is a whole-word token of the candidate `full_name`). Otherwise
  `None`.
- `EssentialsClientError` → `None` (best-effort; never blocks a run).
- A small private `_is_strong_name_match(name, full_name) -> bool` helper holds
  the token rule. Pure (but for the injected `search`); unit-tested.

### 2. `auto_link_confident(mappings, *, search=search_politicians) -> list[str]`
New, in `src/relink.py` (uses `review.link_speaker`, like `relink_in_meeting`).
For each mapping that is **named** (`speaker_name` set), **unlinked**
(`politician_id is None`), **normal** (`speaker_status` not
'unidentified'/'non_speaker'), and **non-local** (`local_slug is None`):
- `t = confident_target(mapping.speaker_name, search=search)`;
- if `t`: `review.link_speaker(mappings, label, t.politician_slug,
  t.politician_id)` and set `mapping.id_method = "auto_linked"`.
Returns the labels auto-linked. Pure but for the injected `search`; unit-tested.

### 3. Wiring in `run_local.py` (the main run flow)
- **Interactive review** (`sys.stdin.isatty() and not --no-review`, ~line 1277):
  run `auto_link_confident(mappings)` and print the auto-links **before**
  `_interactive_speaker_review`, so the operator reviews with confident speakers
  already linked (and can still override/unlink). 
- **Non-interactive / `--no-review`** (the `else: mappings = human_review(...)`
  branch, ~line 1288): run `auto_link_confident(mappings)` **after**
  `human_review` so confident speakers are linked without any prompt.
- **`_prompt_link_politician` guard fix** (~line 2512): its "already linked"
  check tests only `mapping.politician_slug`, but id-keyed links have
  `slug=None`. Change it to `if mapping is None or mapping.politician_slug or
  mapping.politician_id: return` so auto-linked (slug-null) speakers are not
  re-prompted during the interactive rename flow.

### 4. `id_method="auto_linked"`
A new value alongside the existing `human_review`/`human_confirmed`/etc. Set by
`auto_link_confident`. Carries through to the published `meetings.speakers`
row's `id_method`, so auto-links are queryable for audit and reversible via
`--relink-person` / the bulk tool.

## Data flow

```
run → speakers named (roster / LLM / voice)
   ▼ auto_link_confident(mappings):  per named+unlinked mapping →
       confident_target(name)  [known_id | exactly-one STRONG essentials match]
       → review.link_speaker + id_method="auto_linked"
   ▼ interactive: _interactive_speaker_review (confident ones already linked;
        _prompt_link_politician only prompts the rest; operator can override)
   ▼ non-interactive: links kept as-is
   ▼ confidence gate / enroll / publish unchanged  (auto-link ≠ auto-publish)
```

## Error handling / edge cases

- **Essentials outage** → `confident_target` returns None for all; run proceeds
  with nothing auto-linked.
- **Weak / ambiguous / substring match** (Host→Hostettler) → not auto-linked.
- **Already linked** (roster auto-match, voice carry-over) → skipped (only
  unlinked mappings are considered).
- **Operator override** → in interactive review the operator can unlink/change
  an auto-linked speaker; their decision is final (review happens after the
  auto-link pass).
- **Auto-link is reversible** → `id_method='auto_linked'` distinguishes these;
  `--relink-person --to-id` or the bulk tool corrects any mislink.

## Testing

- `_is_strong_name_match`: "steve hilton"/"Steve Hilton" → True;
  "host"/"Matthew Hostettler" → False; "councilmember rollo"/"David R Rollo" →
  False; "steyer"/"Tom Steyer" → True.
- `confident_target`: single strong match → target; substring-only → None;
  multiple matches → None; weak single → None; `known_id` → target; API error →
  None. Mocked `search`.
- `auto_link_confident`: links only named+unlinked+normal+non-local mappings,
  sets `id_method="auto_linked"` and both id fields, returns labels; skips
  already-linked / unidentified / local; no confident match → unchanged.
- `_prompt_link_politician` guard: a mapping with `politician_id` set but
  `politician_slug=None` is treated as already-linked (early return) — no
  re-prompt. (Unit-test the guard via a mappings dict + non-tty short-circuit,
  or assert the guard condition directly.)
- Wiring smoke: a `--no-review` run over a meeting whose speaker name has one
  strong essentials match results in that speaker linked with
  `id_method="auto_linked"` (mock `search_politicians`).

## Out of scope / deferred

- **`suggest_link` / bulk-scan Host fix** — owned by the in-flight separate task.
- **DRYing the two strong-match checks** (`confident_target` vs the Host task's
  `suggest_link` guard) — a small follow-up once both land.
- **Auto-publishing** — the gate stays manual/off.
- **Standalone `--review` / `--identify` re-review paths** — D targets the
  initial run flow; a later extension can add the pass there too.
- **Voice-only auto-link** — already handled by `identify._carry_link`.

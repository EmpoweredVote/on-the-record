# CREC → essentials politician_id bridge — design

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Parent:** the Congressional Record speaker-oracle (the linkage deferred across Phases 1–4)
**Depends on:** shipped `src/crec_align.py` (`LabelResolution`), `src/congress_roster.py` (`CongressMember`), `src/crec_identify.py` (`crec_speaker_mappings`), and the existing `src/essentials_client.py` (`search_politicians`).

## Goal

When the CREC oracle confidently resolves a diarized floor speaker to a congressional member, link that speaker to the member's **essentials `politician_id`** so they surface on the real politician profile / quotes on the site — instead of only carrying a name + `local_slug=congress-<bioguide>`.

## Empirical grounding (verified 2026-07-18 against the live `search-by-name` API)

- Essentials **already holds sitting members** as incumbents with `politician_id`s (Steil, Stanton, Thune, Baldwin, Fischbach all resolved).
- `congress-legislators` `official_full` ≠ essentials display name: `"James P. McGovern"` → **0 matches**, but `"Jim McGovern"` and a bare `"McGovern"` search **do** resolve. → **search by last name**, not `official_full`.
- Disambiguation fields returned by `search_politicians`:
  - `government_name` `"United States Federal Government"` distinguishes federal members from same-name **local** politicians (a city-councillor "Marc McGovern", dozens of local "Smith"s).
  - `office_title` (`"Senator"` / `"Representative"` / `"U.S. Representative"`) → chamber.
  - `district_label` (`"Congressional District 9"`) → the **district number**, which separates same-surname reps (Adam Smith CD-9 vs Adrian Smith CD-3, matching `CongressMember.district`).
  - `politician_slug` is often `None`; `politician_id` (UUID) is the real key.

## Scope decisions (locked during brainstorming)

- **Read-only.** Reuse the existing `GET /api/essentials/candidates/search-by-name` via `search_politicians`. **No writes to essentials.** Members not in essentials (or not safely matchable) stay name-only, exactly as today.
- **Never a wrong link.** Attach a `politician_id` **only on an unambiguous single match**. Any ambiguity (>1 federal same-chamber[/district] candidate, or a name that can't be confirmed) → leave name-only. A wrong link would misattribute floor quotes to the wrong person's profile — worse than no link.
- **Inline + transparent.** The bridge runs inside `crec_speaker_mappings` (Phase-4's orchestrator, already networked). It defaults to the real `search_politicians`, so it activates with **no `run_local.py` change**, and is best-effort (API failure → name-only).

## Non-goals

- No writes / inserts to essentials; no reconciliation of missing members.
- No change to CREC alignment, confidence, or the identity-collision guard.
- No senator same-surname state disambiguation beyond name (essentials' return has no state field; such collisions are rare → they fall to name-only).
- No `run_local.py` change (bridge is transparent via the default `search`).

## Component 1 — `src/crec_essentials.py` (new, pure matching)

```python
def resolve_politician_id(
    member: CongressMember,
    *,
    search=search_politicians,
) -> Optional[tuple[str, Optional[str]]]:
    """Resolve a CongressMember to an essentials (politician_id, politician_slug),
    or None when there is no single unambiguous federal match.

    Best-effort: any search error -> None (caller falls back to name-only).
    """
```

Algorithm:
1. `cands = search(member.last_name, limit=25)` (last name, not `full_name` — handles nickname/first-name-form mismatch). Any exception → return `None`.
2. Filter:
   - **federal:** `"united states federal"` in `government_name.lower()`.
   - **chamber:** `member.chamber == "senate"` → `"senator"` in `office_title.lower()`; `"house"` → `"representative"` in `office_title.lower()`.
   - **district (house only, when `member.district` is not None):** the integer parsed from `district_label` (`"Congressional District 9"` → `9`) equals `member.district`.
3. If **exactly one** candidate remains → return `(politician_id, politician_slug)`. Otherwise → `None`.

Helpers (individually tested): `_is_federal(rec)`, `_chamber_matches(rec, chamber)`, `_district_number(district_label) -> Optional[int]`.

## Component 2 — `src/crec_identify.py` change (enrich in the orchestrator)

`crec_speaker_mappings(date, chamber, segments, *, fetch=None, min_confidence=0.5, cache_path=None, search=search_politicians)`:

After converting a `LabelResolution` to a `SpeakerMapping`, when `res.member` is set, attempt the bridge and enrich the mapping:

```python
m = label_resolution_to_mapping(res)
if m is None:
    continue
if res.member is not None:
    link = resolve_politician_id(res.member, search=search)
    if link:
        m.politician_id, m.politician_slug = link
mappings[label] = m
```

- `search` is threaded so tests inject a fake (and never touch the network). Default is the real `search_politicians`.
- Role / ambiguous / unresolved speakers never reach the bridge (no `res.member`).
- On a successful link, the mapping keeps its `id_method='congressional_record'`, `speaker_name`, and `local_slug=congress-<bioguide>` (provenance), and **gains** `politician_id`/`politician_slug`. Downstream (`_dedupe_identities`, publish) then treats it as a linked politician, and the id-based dedupe pass gains real force (two labels → same politician_id both caught) alongside the CREC-exemption from the split fix.

## Data-flow guarantees

- **Read-only / best-effort:** essentials API down or slow → `resolve_politician_id` returns `None` → name-only. Never blocks Stage 4.
- **No wrong links:** ambiguity → `None`. The federal + chamber + district filters make a wrong single-match highly unlikely; when they can't produce exactly one, we abstain.
- **Anti-partisan invariant preserved:** `search_politicians` already excludes affiliation fields (`_normalize_politician` whitelist); the bridge reads only id/slug/name/office/district/government — no affiliation.

## Testing (offline, injected `search`)

`resolve_politician_id` (fake `search` returning canned records):
- exact single federal match → `(id, slug)`.
- **nickname case:** `member.last_name="McGovern"`, search returns `Jim McGovern` (fed rep, CD-2) + `Marc McGovern` (city councillor) → local filtered out → the rep resolved.
- **same-surname reps:** search returns Adam Smith (CD-9) + Adrian Smith (CD-3); `member.district` picks the right one.
- **chamber filter:** a senator member vs a returned `Representative` of the same surname → filtered out.
- **ambiguous:** two federal same-chamber-and-district candidates → `None`.
- **no match / empty → None**; **search raises → None** (best-effort).
- helper units: `_is_federal`, `_chamber_matches`, `_district_number` (incl. no-number label → `None`).

`crec_speaker_mappings` (existing tests updated to inject `search`, + new):
- update the two existing orchestration tests to pass `search=lambda q, **kw: []` (keeps them offline; their name/local_slug assertions are unaffected since no politician_id was asserted).
- new: with a fake `search` returning a federal match for a resolved member, the mapping gains the `politician_id`; ambiguous/no-match → mapping stays name-only (`politician_id is None`).

## Known limits (carried forward)

- Members essentials doesn't have, or can't disambiguate (rare same-surname-same-district; same-surname senators; at-large districts whose `district_label` lacks a number) → name-only, unchanged from today.
- Very common surnames could exceed the search `limit=25` and truncate the target out → a miss → name-only (safe, never a wrong link). Documented, not silently "covered."
- `politician_slug` is often `None` in essentials → we attach whatever the API returns; `politician_id` is the authoritative link.

## Files

- Create: `src/crec_essentials.py`
- Create: `tests/test_crec_essentials.py`
- Modify: `src/crec_identify.py` (thread `search`, enrich member mappings)
- Modify: `tests/test_crec_identify.py` (inject `search` in existing tests; add bridge tests)

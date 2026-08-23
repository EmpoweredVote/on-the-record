# GUI politician picker: candidacy context and duplicate-row disambiguation

Date: 2026-08-03
Branch: `worktree-feat+gui-politician-picker-candidacy`
Status: implemented on branch `worktree-feat+gui-politician-picker-candidacy`,
verified in the running GUI

## Problem

When linking a speaker to an essentials politician in the processing GUI, the
picker gives the curator too little information to choose correctly, and a wrong
choice silently detaches the meeting (and any quotes published against that
person) from its race.

Three separate defects, discovered while trying to link Tom Tiffany to the 2026
Wisconsin Governor race.

### 1. One person can appear twice, and the intuitive row is sometimes wrong

`essentials.politicians` is the person table. Candidacy is not a separate
profile — it is an edge in `essentials.race_candidates` (`race_id` →
`politician_id`). There is one ID space.

But two different mechanisms make one human appear as two picker rows:

**Office fan-out (cosmetic).** `getPoliticiansFlatList` joins
`essentials.office_current_holder`, which returns one row per office. People who
hold both a real office and a "Candidate for …" placeholder office (ev-accounts
migration 196) fan out to two rows carrying the *same* `politician_id`:

```
James Talarico | Representative                      || Candidate for U.S. Senate — Texas
Ken Paxton     | Candidate for U.S. Senate — Texas   || Texas Attorney General
```

Linking either row gives an identical result, so this is noise, not a hazard.

**Genuine duplicate person rows (a hazard).** A later hand-add session minted a
*second* person row for people who already existed in essentials. The synthetic
`-66000xxx` external_ids are the tell. The race edge landed on the new row; the
office, images, bio and any already-published quotes stayed on the old one:

| person | pre-existing row | hand-added row |
|---|---|---|
| Kris Mayes | `e947c3c1` ext `-400092` · Attorney General · 6 quotes · **0 races** | `638e122d` ext `-66000122` · no office · **1 race** |
| Kimberly Yee | `ad8c25f3` ext `-400094` · Treasurer · 1 quote · **0 races** | `93d63118` ext `-66000136` · **1 race** |
| Alexander Kolodin | `178470c3` ext `-4006005` · State Rep · 1 quote · **0 races** | `65f1e278` ext `-66000133` · **1 race** |
| Francesca Hong | `f1212497` ext `-5506076` · Rep. to the Assembly · **0 races** | `dfe4ad6a` ext `-559004` · **1 race** |

For Tiffany the office-holding row *is* the candidate. For Hong the office-holding
row is **not**. So "should I pick the current politician?" has no general answer,
and the picker shows nothing that would let a curator tell the cases apart.

Across active politicians: **85 duplicate-name groups**, **45** where exactly one
row is an active candidate (a silent coin flip), **6** where several are.

### 2. Candidates render as a bare name

[`gui/static/workspace.js:157`](../../../gui/static/workspace.js) renders
`full_name · office_title · government_name`. **1,648 of the active candidates
hold no office at all**, so both trailing fields are empty:

```
Thomas P. Tiffany · U.S. Representative · United States Federal Government
Mandela Barnes
Kelda Roys
Francesca Hong
```

The disambiguating context exists in the database — `race_candidates` →
`races.position_name` + `elections.state` / `election_type` / `election_date` —
it just never reaches the picker. `search_politicians_safe` whitelists the
response down to six fields, none of which carry candidacy.

### 3. Name search has no token or nickname matching

The upstream search ILIKEs the whole query as one substring against `full_name`,
`preferred_name`, `first_name`, `last_name`, and `first_name || ' ' || last_name`.
Stored name is `Thomas P. Tiffany`:

| query | hits |
|---|---|
| `Tiffany` | 8 (Tiffany is #1) |
| `Thomas Tiffany` | 1 ✓ (matches `first+last`) |
| `Tom Tiffany` | **0** |

The codebase already works around this: [`src/crec_essentials.py:40`](../../../src/crec_essentials.py)
searches by last name only and then disambiguates, because "essentials display
names differ … nicknames, dropped middle initials."

### Why this matters beyond ergonomics

Two independent consumers key strictly off `politician_id`:

- [`src/publish.py:187`](../../../src/publish.py) `_reconcile_event_races` derives a
  meeting's races **solely** from `politician_id → race_candidates`. A wrong pick
  resolves to no race: for `debate`/`forum` publish raises and aborts the
  transaction; for `council`/`school_board` it silently clears `event_races`.
- Read & Rank reaches quotes **only** via
  `race_candidates.politician_id = quotes.politician_id`
  (`ev-accounts/backend/src/lib/readrankService.ts:325`, and the
  `rankable_topic_count` subquery at :310). A quote on a row with no race edge is
  invisible on the race page, silently and permanently.

This has already happened: **16 quotes across 6 people are stranded on
non-candidate twins, 11 of them `readrank_selected = true`** — Mayes (6),
Priest (5), Rice (2), Schweikert, Yee, Kolodin. Repairing that data is tracked
separately (see Out of scope).

## Goals

1. A curator can tell, from the picker row alone, which record is attached to the
   race they care about.
2. One person never appears twice for reasons that carry no meaning.
3. `Thomas Tiffany` finds `Thomas P. Tiffany`.
4. Duplicate-name situations are visibly flagged rather than left to chance.

## Non-goals

- Nickname aliasing (`Tom` → `Thomas`). Explicitly declined: searching the
  surname and picking from a well-labelled list is sufficient.
- Changing the ev-accounts `search-by-name` endpoint or `PoliticianFlatRecord`.
  The candidacy join and the duplicate warning are curation concerns and do not
  belong on a general-purpose public record.
- Repairing the stranded-quote data, or merging duplicate person rows.
- Blocking or gating the link action itself. Publish already hard-fails for
  `debate`/`forum` with no resolved race; the label is the intervention here.

## Approach

Add `gui/politicians.py`: a direct-DB politician search via `DATABASE_URL` +
`psycopg2`, mirroring the existing [`gui/races.py`](../../../gui/races.py)
precedent (which already reads `essentials.races` this way for the race picker).
Best-effort throughout — a missing `DATABASE_URL`, a bad query or a dead
connection yields no results rather than raising.

`gui/races.py` is reused, not duplicated: candidacy labels are composed with its
existing `race_display()`, so a candidacy in the picker reads exactly the same as
the race picker's own label for that race.

`src/essentials_client.search_politicians` is left untouched — `crec_essentials`
depends on it for the automated CREC bridge, which must not require a database
handle.

### Module layout

`gui/politicians.py`

| function | purpose | testable without a DB |
|---|---|---|
| `parse_name_query(q) -> list[str]` | split a raw query into lowercase tokens | yes |
| `politician_display(rec) -> str` | compose the primary label line | yes |
| `candidacy_display(rec) -> str` | compose the candidacy line | yes |
| `mark_duplicate_names(results) -> list` | annotate rows sharing a normalized name | yes |
| `search_politicians_safe(q, *, limit=10) -> dict` | the DB query + assembly | via `_FakeConn` |

The pure functions carry the behaviour worth asserting; the query function is
thin glue, matching how `races.py` splits `race_display`/`race_slug`/
`parse_race_query` away from `search_races_safe`.

### The query

`DISTINCT ON` requires its `ORDER BY` to lead with `p.id`, which is not the order
we want to present. Limiting at that level would therefore truncate an arbitrary
ten rows *before* ranking, and for a common surname the candidate could be cut
entirely. So the deduplicating select is wrapped, and ranking plus `LIMIT` happen
outside it:

```sql
SELECT * FROM (
  SELECT DISTINCT ON (p.id)
         p.id, p.full_name, p.slug,
         COALESCE(o.title, '') AS office_title,
         COALESCE(d.label, '') AS district_label,
         COALESCE(g.name, '')  AS government_name,
         cand.candidacies
  FROM essentials.politicians p
  LEFT JOIN essentials.office_current_holder och ON och.politician_id = p.id
  LEFT JOIN essentials.offices     o  ON o.id  = och.office_id
  LEFT JOIN essentials.districts   d  ON d.id  = o.district_id
  LEFT JOIN essentials.chambers    ch ON ch.id = o.chamber_id
  LEFT JOIN essentials.governments g  ON g.id  = ch.government_id
  LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
             'position_name',  r.position_name,
             'state',          e.state,
             'primary_party',  r.primary_party,
             'election_type',  e.election_type,
             'year',           EXTRACT(YEAR FROM e.election_date)::int,
             'status',         COALESCE(rc.candidate_status, 'active')
           ) ORDER BY e.election_date) AS candidacies
    FROM essentials.race_candidates rc
    JOIN essentials.races     r ON r.id = rc.race_id
    JOIN essentials.elections e ON e.id = r.election_id
    WHERE rc.politician_id = p.id
  ) cand ON true
  WHERE p.is_active = true
    AND <one AND-ed clause per query token>
  ORDER BY p.id,
           (COALESCE(o.title, '') ILIKE 'Candidate for%'),  -- real office wins the DISTINCT
           o.title NULLS LAST
) t
ORDER BY (candidacies IS NULL),      -- anyone in a race first
         (office_title = ''),        -- then office-holders
         full_name
LIMIT %s
```

Notes:

- **`DISTINCT ON (p.id)`** collapses the office fan-out. The inner `ORDER BY`
  makes a real office win over a `Candidate for …` placeholder, so Talarico shows
  once as `Representative` — with his U.S. Senate candidacy on line 2, which is
  strictly better information than the placeholder office ever was.
- **Per-token matching.** Each token is a separate AND-ed clause, each an OR over
  `full_name` / `preferred_name` / `first_name` / `last_name`, wrapped in
  `public.f_unaccent` on both sides (the same helper the upstream search uses, so
  `Munoz` still finds `Muñoz`). `Thomas Tiffany` becomes two clauses, both of
  which `Thomas P. Tiffany` satisfies — the dropped middle initial stops
  mattering. Order-independent as a side effect.
- **Ranking is entirely in SQL**, so `LIMIT` is safe: a candidate can never be
  squeezed out by non-candidates. Withdrawn status does not affect ranking (it
  would need unwrapping the aggregate for little gain) — it only shows in the
  label.
- **All candidacies, not just active ones**, are fetched, so a person whose only
  candidacy is withdrawn reads as withdrawn rather than as a non-candidate. This
  matches `resolve_races_for_politicians`, which also does not filter on status.
- Two-character minimum on the whole query, as today.

### Verified against production before writing this

Run by hand against the live DB (not automated — there is no test DB):

| query | result |
|---|---|
| `thomas tiffany` | one row · `U.S. Representative` · `Congressional District 7` · candidacy `Governor / WI / Republican primary / 2026` |
| `talarico` | **one** row (was two) · `Representative` · `TX House District 50` · candidacy `U.S. Senate Texas / TX / general / 2026` |
| `paxton` | Ken Paxton **once** · `Texas Attorney General` + his U.S. Senate candidacy; Angela Paxton separately with no candidacy |
| `hong` | both Francesca Hong rows returned and now distinguishable — one carries the WI Governor Democratic primary, the other carries none |
| `smith` | all ten returned rows carry a candidacy; office-less non-candidates correctly ranked out |

### Verified in the running GUI

Driven through the real app on `http://localhost:8001` (a second instance, so the
developer's own GUI on :8000 was untouched), against the live `essentials` schema.
The markup below is what the page actually produced:

```
"hong"
  Francesca Hong                                          ⚠ 2 results share this name
    running: WI · Governor · Democratic primary · 2026            <- politician_id dfe4ad6a
  Francesca Hong · Representative to the Assembly · …     ⚠ 2 results share this name
    no candidacies                                                <- politician_id f1212497
  ...4 other Hongs, each "no candidacies", none flagged

"thomas tiffany"
  Thomas P. Tiffany · U.S. Representative · Congressional District 7 · United States Federal Government
    running: WI · Governor · Republican primary · 2026

"barnes"   (candidates ranked ahead of office-holders)
  Brice Barnes                     running: FL · U.S. Representative District 2 · General · 2026
  Mandela Barnes                   running: WI · Governor · Democratic primary · 2026
  Ben Barnes · Delegate · …        no candidacies

"andrew chase"
  Andrew Chase                     running: TX · Murphy Council Member Place 3 · General · 2026

"tom tiffany"  ->  "no matches"   (expected: nickname aliasing is a non-goal)
```

Computed styles confirmed in the browser: the picker button is
`display: flex; flex-direction: column` with `text-align: left` preserved; the
candidacy line is `#5c6b82` at 12.48px; `no candidacies` and the duplicate chip are
`#9a6a00`, matching the existing `.thin` caution. A synthetic race-picker button
(`.link-result` inside `.race-results`) computed `display: block` — confirming the
scoped selector leaves the race picker's own buttons alone.

Cost on the worst case (`smith`): planning 7 ms, server-side execution **101 ms**.

**Corrected after implementation.** That figure is SQL execution only and was
misleading as a latency claim. Measured end-to-end on the finished module:

| | min | avg |
|---|---|---|
| `connect()` + `close()` alone | 461 ms | 473 ms |
| query on an already-open connection | 176 ms | 217 ms |
| a full `search_politicians_safe()` call | 642 ms | 660 ms |

So ~470 ms of every keystroke's ~650 ms is connection setup to the Supabase
pooler, not the query — and the query cost barely varies between a 1-row result
and the worst-case surname. With the 250 ms debounce the curator waits ~900 ms.

That is sluggish but it is **not a regression**: `gui/races.py` opens and closes a
connection per call in exactly the same way, so the race picker already behaves
this way. Reusing a module-level connection would cut it to ~430 ms, but it needs
thread-safety (FastAPI runs sync endpoints in a threadpool) and stale-connection
retry, so it is left as a follow-up rather than smuggled into this change.

### Row rendering

Two lines per result, so the label stays readable. Line 1 is identity, line 2 is
candidacy:

```
Thomas P. Tiffany · U.S. Representative · Congressional District 7 · United States Federal Government
  running: WI · Governor · Republican primary · 2026

Francesca Hong                                            ⚠ 2 records for this name
  running: WI · Governor · Democratic primary · 2026

Francesca Hong · Representative to the Assembly           ⚠ 2 records for this name
  no candidacies

Mandela Barnes
  running: WI · Governor · Democratic primary · 2026
```

Rules:

- Line 1: `full_name`, then `office_title`, `district_label`, `government_name`,
  each omitted when empty, joined with ` · `. This is today's format plus
  `district_label`, which is what distinguishes the 39 identically-titled
  `U.S. Representative` rows.
- Line 2: `running: ` followed by each candidacy rendered with
  `races.race_display(position_name, year, state, primary_party, election_type)`,
  joined with `; `. A non-active candidacy is prefixed with its status
  (`withdrawn: …`). Capped at 3 candidacies with `+N more` so one row cannot run
  away.
- No candidacies at all → `no candidacies`, styled as a warning. This is the
  signal that would have caught the Hong case.
- **Duplicate marker**: when two or more results in the same response share a
  normalized `lower(first_name last_name)` key, every row in that group is
  tagged `⚠ N records for this name`. Computed in Python over the result set, so
  it needs no extra query and is directly unit-testable.

`mark_duplicate_names` deliberately flags the whole group rather than guessing
which row is right — the candidacy line already answers that, and the marker's
job is only to stop the curator from picking on autopilot.

### Wiring

- `gui/review_api.py` — `search_politicians_safe` delegates to
  `gui.politicians.search_politicians_safe`. When `DATABASE_URL` is absent it
  falls back to today's HTTP path, so a GUI run without DB access degrades to the
  current behaviour (name + office, no candidacy line) instead of returning
  nothing. The response gains `candidacies`, `display`, `candidacy_display` and
  `duplicate_note`; all six existing keys are kept. Note `district_label` is
  already in the response today — the renderer simply never used it, which is why
  the 39 identically-titled `U.S. Representative` rows are currently
  indistinguishable.
- `gui/static/workspace.js` — render the two-line button from `display` /
  `candidacy_display` / `duplicate_note` instead of re-joining fields in JS. The
  server owns the label; the client just prints it.
- `gui/static/style.css` — a `.link-result .pr-cand` line (smaller, dimmer) and
  a `.link-result .pr-warn` treatment for `no candidacies` and the duplicate
  marker.

The `/api/politicians/search` route and the link POST are unchanged.

The race picker is **not** affected: it uses a separate `.race-search` widget in
`gui/templates/new_meeting.html` rendered by `gui/static/new_meeting.js`. The
`.link-search` class that `workspace.js` handles is used only by the speaker card
macro in `gui/templates/panels/_macros.html`, so the politician picker is the sole
consumer of the changed renderer. The renderer still omits line 2 when
`candidacy_display` is absent, which keeps the HTTP-fallback payload working.

## Error handling

Unchanged in kind from `races.py`: `search_politicians_safe` never raises.

| condition | behaviour |
|---|---|
| query < 2 chars | `{"results": [], "error": None}` — no query attempted |
| no `DATABASE_URL` | fall back to the HTTP client path |
| HTTP fallback also fails | `{"results": [], "error": "<message>"}` → "search unavailable" |
| DB connect/auth/schema/query failure | `{"results": [], "error": "politician search failed: …"}` |
| `candidacies` NULL (no `race_candidates` rows) | treated as `[]` → `no candidacies` |
| malformed candidacy JSON element | that element skipped; the rest still render |

The connection is opened and closed per call, as `races.py` does.

## Testing

New `tests/test_gui_politicians.py`, following `tests/test_gui_races.py`'s
`_FakeConn(cur)` seam (monkeypatch `_db_url` and `psycopg2.connect`).

Pure functions:

- `parse_name_query`: `"Thomas Tiffany"` → `["thomas", "tiffany"]`; punctuation
  and extra whitespace dropped; empty query → `[]`
- `politician_display`: name only; name + office; all four fields; empty fields
  omitted rather than leaving stray separators
- `candidacy_display`: none → `no candidacies`; one; several joined with `; `;
  a withdrawn one prefixed; more than 3 → `+N more`; label matches
  `races.race_display` for the same tuple
- `mark_duplicate_names`: no duplicates → no notes; two rows sharing a normalized
  name → both noted with the group size; middle initials do not defeat the key;
  two genuinely different people with the same name still both get flagged
  (correct — a curator must look)

Query behaviour:

- the generated SQL contains `DISTINCT ON (p.id)` and one AND-ed clause per token
- a two-token query produces two clauses and 8 bound parameters (4 fields × 2
  tokens), plus the limit
- the outer `ORDER BY` ranks on `candidacies IS NULL` before `office_title = ''`,
  and the `LIMIT` is applied outside the deduplicating select — asserted against
  the generated SQL, since ordering happens in the database and a fake cursor
  cannot demonstrate it
- rows map to results with `politician_id`, `politician_slug`, `display`,
  `candidacy_display`; `candidacies` JSON is parsed, and a NULL becomes `[]`
- `< 2` chars → empty, no connection attempted
- no `DATABASE_URL` → HTTP fallback invoked (monkeypatched), not a raise
- `psycopg2.connect` raising → `{"results": [], "error": ...}`

Regression:

- `tests/test_gui_workspace.py` still passes — the widget contract
  (`/api/politicians/search`, `data-link-action`) is unchanged
- baseline before this work: 1718 passed

An end-to-end check against the live DB is not automated (no test DB), but the
three cases above — Tiffany, both Hongs, Mandela Barnes — are verified by hand in
the running GUI before merge, and recorded in the PR.

## Out of scope / follow-ups

1. **Repair the 16 stranded quotes and the duplicate person rows.** Filed as a
   separate task; needs per-person judgment (merge rows vs. move the race edge
   vs. repoint the quotes) and touches live curated data. Francesca Hong should
   be fixed before quotes get published against her wrong row.
2. **A guard against re-minting duplicate person rows** in whatever tooling does
   the hand-adds. The picker makes the problem visible; it does not prevent it.
3. **The public site's typeahead keeps the `Tom Tiffany` miss and the office
   fan-out**, since we are not touching `search-by-name`. Worth a narrow
   ev-accounts PR (`DISTINCT ON` alone) if it bothers users there.
4. **`resolve_races_for_politicians` ignores `candidate_status`**, so a withdrawn
   candidate still pulls a meeting into a race. Pre-existing, unexamined here.
5. **Connection reuse for the pickers.** ~470 ms of each picker keystroke is
   `connect()`, not the query (see the table above). A shared connection or small
   pool would roughly halve perceived latency for both this picker and the race
   picker, but needs a lock and stale-connection retry.
6. **13 `is_active = false` rows hold a real office, so the picker cannot find
   them.** This is upstream data rot in `essentials`, not a query defect — among
   them are sitting officials: `Daniel Webster` (U.S. Rep, FL-11)
   `7119c7db-6909-4d05-8613-95d5dc9818de`, `Raul Ruiz` (U.S. Rep, CA-25)
   `05349fa0-8529-4738-8556-f386965e4cc8`, `Kristi Noem` (Secretary of Homeland
   Security) `9f756a19-c14a-4546-911d-8d86a4eef430`, `Pamela Bondi` (Attorney
   General), two Indiana Appeals Court judges, plus local seats (Shruti Rana,
   Bloomington Council D5; Dan Combs, Perry Township Trustee; Patrice Lattimore,
   LA City Clerk).

   I tested rescuing them in the query with
   `OR (g.id IS NOT NULL AND COALESCE(o.title,'') <> '')` — which is safe from the
   76,343 FEC-committee rows, since those carry no chamber/government link — but it
   costs **5x**: 165 ms becomes 850-1068 ms, because the `OR` defeats the planner's
   pruning. It also surfaced a duplicate `Raul Ruiz` (one active row, one inactive).
   Rejected: taxing every keystroke forever to work around 13 bad rows is the wrong
   trade when the fix upstream is a single `UPDATE`. Fix `is_active` in essentials
   instead.

7. **Nickname variants escape the duplicate marker.** `dan brotman` and
   `daniel brotman` are different keys, so the 21 such pairs in prod go unflagged —
   9 of them with one row holding a race edge and its twin holding none
   (Dan/Daniel Brotman, Mike/Michael Thompson, Rick/Richard Bennett, ...). The
   candidacy line still distinguishes them, which is the safeguard that matters.
   Closing it needs a nickname map; keying on the first initial instead would
   group "Angela Davis" with "Andrew Davis".

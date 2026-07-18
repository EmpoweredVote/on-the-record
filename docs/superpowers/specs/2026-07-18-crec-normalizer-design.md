# CREC designation → congress-member normalizer (Phase 2) — design

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Parent:** `docs/superpowers/specs/2026-07-18-congressional-record-speaker-oracle-design.md` (Phase 2 of 5)
**Depends on:** Phase 1 `src/govinfo.py` (shipped — provides `CrecTurn.speaker_raw`)

## Goal

Resolve a Congressional Record speaker designation — `"Mr. McCONNELL"`, `"Ms. BALDWIN of Wisconsin"`, `"The PRESIDING OFFICER (Mrs. Ernst)"` — to a **congressional member identity** (or a procedural role, or unresolved), using the public `unitedstates/congress-legislators` dataset. This is the identity layer Phase 3 (`crec_align.py`) will attach to each aligned turn.

## Scope decisions (locked during brainstorming)

- **Self-contained identity.** Resolve to a member record sourced from `congress-legislators` (bioguide id + name + state + party + chamber). Do **not** touch the ev-accounts/essentials prod DB. The `essentials.politician_id` link is a separate, later step. Keeps Phase 2 fully offline-testable.
- **Current Congress only.** Use the small `legislators-current.json`. Historical CREC dates degrade gracefully to "unresolved" (no crash); historical support (`legislators-historical` + term-date filtering) is deferred.
- **Two focused modules** (mirrors the repo's `roster.py` data / `identify.py` matching split): `src/congress_roster.py` (fetch + cache + build) and `src/crec_normalize.py` (designation → resolved speaker).

## Non-goals

- No `legislators-historical` / date-scoped term filtering (deferred).
- No writes to essentials / no `politician_id` resolution.
- No refinement of Phase 1's turn parser (the "of the Virgin Islands" delegate gap and bare-leadership-role handling stay as-is; documented below).
- No Stage-4 `identify.py` wiring — that's Phase 4.

## Data source (verified 2026-07-18)

`https://unitedstates.github.io/congress-legislators/legislators-current.json` — public domain, one JSON file, 537 current members. Per member:
- `id.bioguide` — stable member id (e.g. `"E000295"`).
- `name.{first, last, official_full}` — e.g. `{"first":"Joni","last":"Ernst","official_full":"Joni Ernst"}`.
- `terms[-1].{type, state, district, party, start, end}` — latest term; `type` is `"sen"` or `"rep"`, `state` is a 2-letter code, `district` is an int or null.

No YAML dependency (use the JSON edition + stdlib `json`).

## Architecture

```
CrecTurn.speaker_raw ─────────────┐
   (from Phase 1)                  ▼
                          crec_normalize.normalize_designation(speaker_raw, chamber, roster)
                                   │  reuses roster.extract_surname
                                   ▼
                          ResolvedSpeaker{member | role | unresolved, method, confidence, needs_review}

congress_roster.fetch_current_legislators(fetch) ──► cache CONFIG_DIR/congress/legislators-current.json
                                   │
                                   ▼
                          build_roster(raw, chamber) ──► CongressRoster (members indexed by lowercased last_name)
```

## Component 1 — `src/congress_roster.py`

Pure parsing with network behind an injected `fetch` (same pattern as `src/govinfo.py`).

```python
@dataclass
class CongressMember:
    bioguide: str
    full_name: str          # name.official_full
    last_name: str          # name.last
    state: str              # 2-letter, from terms[-1].state
    district: Optional[int] # terms[-1].district (None for senators)
    chamber: str            # 'house' | 'senate'
    party: Optional[str]    # terms[-1].party

@dataclass
class CongressRoster:
    chamber: str
    members: list[CongressMember]
    _by_surname: dict[str, list[CongressMember]]   # lowercased last_name -> members
```

Functions:
- `_default_fetch(url) -> str` — `requests.get`, raise_for_status (as in `govinfo.py`).
- `fetch_current_legislators(*, fetch=_default_fetch, cache_path=None) -> list[dict]` — returns the parsed list; when `cache_path` given, writes it (best-effort) for reuse. Default cache: `config.CONFIG_DIR / "congress" / "legislators-current.json"`.
- `_member_from_raw(entry, chamber) -> CongressMember | None` — build from an entry whose `terms[-1].type` matches the chamber's type; return None otherwise.
- `build_roster(raw, chamber) -> CongressRoster` — `chamber` in `{'house','senate'}` → term type `{'rep','sen'}`; keep members whose latest term matches; index by `last_name.lower()`.
- `load_current_roster(chamber, *, fetch=_default_fetch, cache_path=None) -> CongressRoster` — convenience: read cache if present (and non-empty), else fetch; then `build_roster`.

## Component 2 — `src/crec_normalize.py`

Pure; no network. Consumes a `CongressRoster`.

```python
@dataclass
class ResolvedSpeaker:
    member: Optional[CongressMember]   # the matched member, if any
    role: Optional[str]                # 'presiding_officer' | 'speaker' | 'clerk' | ... when procedural
    method: str                        # 'surname' | 'surname_state' | 'presiding_parenthetical' | 'role' | 'unresolved' | 'ambiguous'
    confidence: float                  # 1.0 exact-unique, lower for fuzzy/ambiguous, 0.0 unresolved
    needs_review: bool                 # True for ambiguous / low-confidence
```

`normalize_designation(speaker_raw, chamber, roster) -> ResolvedSpeaker`:

1. **Member forms** — `(Mr|Mrs|Ms|Miss)\.? <SURNAME>[ of <State>]`:
   - Extract surname via `roster.extract_surname`; look up `roster._by_surname[surname.lower()]`.
   - If a trailing `of <State-name>` is present, resolve the state name → 2-letter code (a small US state map) and filter candidates by `state`.
   - 1 candidate → `member=…, method='surname'|'surname_state', confidence=1.0`.
   - >1 candidate after filtering → `member=None, method='ambiguous', needs_review=True` (never guess — honors the speaker identity-collision guard).
   - 0 candidates → `method='unresolved', confidence=0.0`.
2. **Presiding officer with parenthetical** — `The PRESIDING OFFICER (Mrs. Ernst)`: pull the parenthetical surname, resolve as a member; `method='presiding_parenthetical'`. If unresolvable, fall through to a role marker.
3. **Bare procedural roles** — `The PRESIDING OFFICER`, `The SPEAKER[ pro tempore]`, `The Clerk`, `The VICE PRESIDENT`, etc.: `member=None, role=<slug>, method='role', confidence=1.0, needs_review=False`.
4. **Anything else** → `method='unresolved'`.

`annotate_turns(turns, roster) -> list[tuple[CrecTurn, ResolvedSpeaker]]` — map a day's `list[CrecTurn]` (from Phase 1) to `(turn, resolved)` pairs. This is the Phase 3 hand-off.

## State-name resolution

A module-level `_STATE_NAME_TO_CODE` map (full state/territory name → 2-letter code) covers the `of <State>` disambiguation. CREC uses full names ("of Michigan", "of North Carolina", "of Wisconsin").

## Degradation & guarantees

- Fetch failure / empty cache / historical date with no current match → empty roster → every designation resolves `method='unresolved'` (never raises). Phase 3 alignment can still use turn ordering.
- Ambiguous surname (same surname, same chamber, same/absent state) → `needs_review=True`, `member=None` — a wrong-but-confident identity is never emitted.
- Procedural roles never fabricate a member.

## Testing (offline, fixtures)

Fixture `tests/fixtures/congress/legislators-current.sample.json` — a trimmed `legislators-current.json` with a handful of members chosen to exercise:
- a senator (`Joni Ernst`, IA) for the `(Mrs. Ernst)` parenthetical,
- a `Ms. BALDWIN of Wisconsin` senator,
- a deliberate **same-surname pair** in different states (for `of <State>` disambiguation and the ambiguous case),
- a representative (house term) to test chamber filtering.

Tests:
- `congress_roster`: `build_roster` chamber filter (rep vs sen), surname indexing, `_member_from_raw` field mapping (bioguide/state/party/district), cache read/write via injected fetch.
- `crec_normalize`: plain member (`Mr. McCONNELL`-style), `of <State>` disambiguation resolves the right one, ambiguous surname without state → `needs_review`, presiding-officer parenthetical, bare procedural role, unknown surname → unresolved, empty roster → unresolved.

## Known gaps (documented, carried forward)

- **Delegate designations** ("of the Virgin Islands", "of Guam") won't arrive from Phase 1's parser (its `of <State>` clause requires an uppercase word after "of"), so they resolve `unresolved` until the parser is refined. Non-voting delegates/commissioners are present in `legislators-current` (term type `rep`, state `VI`/`GU`/`PR`/`DC`/`AS`/`MP`), so the roster supports them once the parser does.
- **Bare leadership roles** ("The SPEAKER") stay role markers; resolving them to a specific sitting member is deferred to Phase 3 alignment context (which knows who is speaking from the audio ordering).
- **Current-Congress only:** older CREC dates resolve unresolved by design.

## Files

- Create: `src/congress_roster.py`
- Create: `src/crec_normalize.py`
- Create: `tests/test_congress_roster.py`
- Create: `tests/test_crec_normalize.py`
- Create: `tests/fixtures/congress/legislators-current.sample.json`

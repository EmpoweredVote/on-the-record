# CREC Stage-4 wiring (Phase 4) — design

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Parent:** `docs/superpowers/specs/2026-07-18-congressional-record-speaker-oracle-design.md` (Phase 4 of 5)
**Depends on:** Phase 1 `src/govinfo.py`, Phase 2 `src/congress_roster.py` + `src/crec_normalize.py`, Phase 3 `src/crec_align.py` — all shipped.

## Goal

Land the Congressional Record oracle in the real pipeline: make Stage 4 (`identify_speakers`) resolve anonymous diarized `speaker_label`s to congressional members using the CREC alignment, and let an operator trigger it from the CLI for a House/Senate floor session.

## Scope decisions (locked during brainstorming)

- **Defer the essentials `politician_id` link.** A CREC-resolved speaker gets `speaker_name` (member full name) + the bioguide stashed in `local_slug` (`"congress-<bioguide>"`), `id_method='congressional_record'`, and `politician_id`/`politician_slug` left null. No ev-accounts/essentials DB coupling in Stage 4. The essentials bridge is a later phase.
- **Layer + CLI flag** (GUI deferred). Build the CREC identification layer + a testable orchestration helper, and wire a `--congressional-record DATE CHAMBER` flag into `run_local.py`. The processing GUI's new-meeting form is out of scope.

## Non-goals

- No essentials `politician_id` resolution / no DB queries in Stage 4.
- No GUI changes (`gui/`).
- No Senate media download (Phase 5); no changes to Phases 1–3 modules.
- No changes to diarization/transcription/word-assignment.

## Architecture

```
--congressional-record DATE CHAMBER (run_local.py)
        │  _parse_crec_arg  (validate date + chamber)
        ▼
crec_identify.crec_speaker_mappings(date, chamber, segments, *, fetch, min_confidence)
        │  govinfo.fetch_congressional_record_turns  ─────────► CREC turns
        │  congress_roster.load_current_roster        ─────────► roster
        │  crec_normalize.annotate_turns              ─────────► [(turn, ResolvedSpeaker)]
        │  crec_align.align_crec_to_diarization        ─────────► {label: LabelResolution}
        │  label_resolution_to_mapping (per label)
        ▼
   {label: SpeakerMapping}
        │
        ▼
identify_speakers(..., crec_mappings={label: SpeakerMapping})
   → CREC layer (authoritative when confident) → _dedupe_identities → review flags
        ▼
   {label: SpeakerMapping}  (existing Stage-4 output contract, unchanged shape)
```

## Component 1 — `src/crec_identify.py` (new)

Bridges Phase-3 output to Stage-4 `SpeakerMapping`s and orchestrates Phases 1–3. Network lives behind an injected `fetch` (same pattern as `govinfo.py`).

### `label_resolution_to_mapping(res: LabelResolution) -> Optional[SpeakerMapping]` (pure)

`SpeakerMapping` fields (existing, `src/models.py`): `speaker_label, speaker_name, confidence, id_method, needs_review, politician_slug, politician_id, local_slug, local_role, speaker_status`.

| `LabelResolution` | → `SpeakerMapping` |
|---|---|
| confident **member** (`res.member` set) | `speaker_name=member.full_name`, `confidence=res.confidence`, `id_method='congressional_record'`, `local_slug=f"congress-{member.bioguide}"`, `needs_review=False` |
| **role** (`res.role` set) | `speaker_name=<human role label>` (e.g. `"The Presiding Officer"`), `confidence=res.confidence`, `id_method='congressional_record'`, `needs_review=False` (no person link) |
| **ambiguous** (`method=='ambiguous'`) | `SpeakerMapping(speaker_label, needs_review=True, speaker_status='unidentified')` |
| **unresolved** | `None` (no mapping — falls through to today's layers) |

A small `_ROLE_DISPLAY = {'presiding_officer': 'The Presiding Officer', 'speaker': 'The Speaker', 'president_pro_tempore': 'The President pro tempore', 'vice_president': 'The Vice President', 'chief_justice': 'The Chief Justice', 'chair': 'The Chair', 'clerk': 'The Clerk'}` maps role slugs to display names (fallback: title-case the slug).

### `crec_speaker_mappings(date, chamber, segments, *, fetch=_default_fetch, min_confidence=0.5) -> dict[str, SpeakerMapping]`

Orchestrates: `fetch_congressional_record_turns(date, chamber, fetch=fetch)` → if `None`/empty return `{}`; `load_current_roster(chamber, fetch=fetch)`; `annotate_turns`; `align_crec_to_diarization(segments, annotated, min_confidence=min_confidence)`; convert each `LabelResolution` via `label_resolution_to_mapping`, dropping `None`s. Returns `{label: SpeakerMapping}`. Never raises on a missing Record — returns `{}` (Phase 4 caller then runs Stage 4 normally).

## Component 2 — `src/identify.py` change (minimal)

Add a keyword param to `identify_speakers`:

```python
def identify_speakers(segments, speaker_embeddings, stored_profiles=None,
                      llm_identify_fn=None, roster=None, profile_db=None,
                      crec_mappings=None):
```

A new **CREC layer** runs after Layer 3 (LLM) and **before** `correct_mappings` / `_dedupe_identities`:

```python
# CREC layer: the Congressional Record is authoritative for WHO spoke.
if crec_mappings:
    for label, cm in crec_mappings.items():
        if cm.speaker_name and not cm.needs_review:
            mappings[label] = cm            # confident -> override other layers
        elif label not in mappings:
            mappings[label] = cm            # record ambiguous as needs_review
```

Rationale for override: when CREC alignment clears `min_confidence`, the Record's identity is ground truth for *who*, so it supersedes voice/pattern/LLM guesses (parent spec: "takes precedence when confident"). Placing it before `_dedupe_identities` keeps the identity-collision guard authoritative over CREC too. For congressional runs `roster=None`, so the council-roster `correct_mappings` never mangles congress names.

## Component 3 — `run_local.py` CLI wiring (thin)

- New arg: `parser.add_argument("--congressional-record", nargs=2, metavar=("DATE", "CHAMBER"), default=None)`.
- New testable helper `_parse_crec_arg(value) -> Optional[tuple[str, str]]`: validates `DATE` is `YYYY-MM-DD` and `CHAMBER ∈ {house, senate}` (case-insensitive → lowercased); raises a clear `SystemExit`/`ValueError` on bad input; returns `None` when the flag is absent.
- In the Stage-4 block (around `run_local.py:1389`): if the flag is set, compute `crec = crec_speaker_mappings(date, chamber, segments)` before the `identify_speakers` call and pass `crec_mappings=crec`. A one-line log reports how many labels CREC resolved.

The only logic-bearing new code in `run_local.py` is `_parse_crec_arg` (unit-tested); the call-site wiring is a couple of lines.

## Data-flow guarantees

- **ADR-0001 preserved:** identity only; timestamps/words untouched (Phase 3 already guarantees this; Phase 4 only converts identities to `SpeakerMapping`s).
- **Identity-collision guard:** CREC mappings pass through the existing `_dedupe_identities`, so two labels can't silently become the same member.
- **Graceful no-op:** a recess day / missing Record / no CLI flag → `crec_mappings` empty or `None` → Stage 4 behaves exactly as today.

## Testing (offline)

- `label_resolution_to_mapping`: member (name + `local_slug=congress-<bio>` + id_method, no politician_id), role (display name), ambiguous (`needs_review`, `speaker_status='unidentified'`), unresolved (`None`).
- `crec_speaker_mappings`: injected `fetch` returning the existing Phase-1 CREC granule fixtures + Phase-2 legislators fixture, plus synthetic `Segment`s → end-to-end `{label: SpeakerMapping}`; empty/missing Record → `{}`.
- `identify_speakers` CREC layer: a confident CREC mapping overrides a lower-confidence pattern match for the same label; an ambiguous CREC mapping flags `needs_review` when the label is otherwise unidentified; two labels resolving to the same member are caught by `_dedupe_identities`.
- `_parse_crec_arg`: valid `("2018-10-10","Senate")` → `("2018-10-10","senate")`; bad date and bad chamber each raise.

## Known limits (carried forward)

- No essentials `politician_id` — floor speakers show correct names but don't yet link to essentials profiles/quotes (deferred phase).
- Role-dominant labels resolve to a role display name, not a specific member (Phase 2/3 gap).
- CLI-only trigger; GUI new-meeting form unchanged.
- Confident-CREC-overrides is intentional; if a future run shows CREC over-riding a correct voice-profile match on weak alignment, tighten `min_confidence` (already a parameter) rather than changing precedence.

## Files

- Create: `src/crec_identify.py`
- Create: `tests/test_crec_identify.py`
- Modify: `src/identify.py` (add `crec_mappings` param + CREC layer)
- Modify: `tests/test_identify.py` (CREC layer tests) — or a new focused test module if `test_identify.py` is unwieldy.
- Modify: `run_local.py` (arg + `_parse_crec_arg` + call-site wiring)
- Modify/Create: a test for `_parse_crec_arg`.

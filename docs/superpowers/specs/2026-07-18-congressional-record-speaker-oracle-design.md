# Congressional Record speaker-ID oracle for floor proceedings — design

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Goal:** Process U.S. House and Senate **floor proceedings** by pairing publicly-available media (House Clerk YouTube / senate.gov) with the official **Congressional Record** (GovInfo CREC), using the Record as an authoritative source for *speaker identity* — the pipeline's hardest stage.

---

## Motivation

Floor proceedings are public: media is free on House Clerk YouTube and the senate.gov floor webcast, and the **Congressional Record** publishes a free, official, speaker-attributed transcript via the GovInfo API. The Record is *not* verbatim (members "revise and extend" remarks) and carries *no timestamps*, so it is a poor substitute for ASR. Its real value is that it records **exactly who spoke, in what order** — which maps directly onto Stage 4 (speaker identification), the pipeline's known weak point (voice-embedding collisions, pattern misattribution).

**Core idea:** verbatim words + exact timestamps keep coming from ASR/captions (preserving ADR-0001's link-to-the-exact-moment promise). The Congressional Record contributes **identity only** — we align the two ordered sequences and transfer real names onto the anonymous diarized speaker labels.

---

## Non-goals

- **Not** using CREC text as the transcript. It has no timestamps and is non-verbatim; feeding it through the captions seam would break exact-moment linking. Rejected during design.
- **Not** wiring GovInfo into `resolve_source`. That dispatches on a media URL; CREC enrichment keys on *(date, chamber)* — a different trigger. CREC is a separate, explicitly-triggered source.
- **Not** committee hearings in this iteration. The CHRG collection's transcripts are delayed months-to-years and often missing for recent hearings. Floor proceedings (CREC, next-business-day) come first.

---

## Architecture overview

```
                 media URL (House Clerk YouTube / senate.gov)
                        │
   date + chamber ──────┼─────────────────────────────┐
        │               ▼                              │
        │      Stage 1 Ingest → Stage 2 Diarize        │
        │      → Stage 3 Transcribe (ASR/captions:      │
        │        verbatim words + timestamps)           │
        │               │                               │
        ▼               ▼                               │
  govinfo.py      diarized turns (anon labels,          │
  fetch CREC      verbatim text, timestamps)            │
  turns (ordered,        │                              │
  named)                 ▼                              │
        │        crec_align.py                          │
        └──────► text-anchored monotonic alignment ◄────┘
                        │
                        ▼
             speaker_label → CREC identity
                        │
                        ▼
        Stage 4 Identify: new high-authority source
        → SpeakerMapping{politician_id, id_method=
          "congressional_record", confidence, needs_review}
```

---

## Components

### 1. `src/govinfo.py` — CREC fetch (pure, injected fetch)

Mirrors `podcast.py` / `brightspot.py`: parsing is pure and network-free; the only network primitive is an injected `fetch`.

**Public entry point:**

```python
def fetch_congressional_record_turns(
    date: str,                    # 'YYYY-MM-DD'
    chamber: str,                 # 'house' | 'senate' (case-insensitive)
    *,
    fetch: Callable[[str], str] = _default_fetch,
    api_key: str | None = None,   # arg → GOVINFO_API_KEY env → 'DEMO_KEY'
    max_granules: int | None = None,  # rate-limit / demo guard
) -> list[CrecTurn] | None
```

`CrecTurn` dataclass: `{speaker_raw: str, text: str, granule_id: str, order: int}`.

**Flow:**
1. Resolve key: `api_key` arg → `GOVINFO_API_KEY` env → `DEMO_KEY` fallback.
2. `package_id = f"CREC-{date}"`.
3. Page `/packages/{package_id}/granules?offsetMark=*&pageSize=100`, following `nextPage.offsetMark` until null; keep granules whose class matches chamber (`house`→`HOUSE`, `senate`→`SENATE`; `DIGEST`/`EXTENSIONS` excluded), **in API document order**.
4. For each matched granule, fetch `/packages/{package_id}/granules/{granuleId}/htm`, strip to text (BeautifulSoup — already a dep), and parse into ordered speaker turns (CREC delimits turns with `Mr. SMITH.` / `Ms. JONES.` style markers and per-granule bylines).
5. Return ordered `list[CrecTurn]`, or `None` if no package / no matching granules.

**Pure helpers (individually tested):** `_package_id`, `_granules_url`, `_granule_text_url`, `parse_granule_list(json_text, chamber)` (reads `granuleClass`, falls back to `docClass`), `_next_offset_mark(json_text)`, `html_to_text(htm)`, `parse_granule_turns(text) -> list[CrecTurn]`.

**API facts (verified against usgpo/api):**
- `api.data.gov` key required; `DEMO_KEY` works (rate-limited).
- Granules list: `/packages/{packageId}/granules?offsetMark=*&pageSize=100`, paginate via `nextPage.offsetMark`.
- Granule text: `/packages/{packageId}/granules/{granuleId}/htm`.
- Chamber via `granuleClass`/`docClass` ∈ `HOUSE | SENATE | DIGEST | EXTENSIONS`.

### 2. Speaker-designation normalizer

Maps CREC speaker forms to a roster politician or a procedural role:
- `Mr. SMITH of Michigan`, `Ms. JONES` → roster member (surname + optional state disambiguation).
- `The SPEAKER`, `The SPEAKER pro tempore`, `The PRESIDING OFFICER`, `The Clerk`, `The Acting President pro tempore` → procedural roles (mapped to a member when known, else a non-member role).

Reuses existing `extract_surname` / `_surname_matches_roster` (`roster.py` / `identify.py`). State-of qualifier resolves same-surname collisions.

### 3. `src/crec_align.py` — sequence alignment (the heart)

**Inputs:**
- ASR-transcribed diarized turns: contiguous runs of one `speaker_label`, each with verbatim text + `[start,end]` timestamps.
- CREC turns: ordered `list[CrecTurn]`.

**Method:** text-anchored, monotonic sequence alignment (Needleman–Wunsch / DP). Cell score = text similarity between a diarized turn's ASR text and a CREC turn's text; monotonicity encodes that both sequences advance in time. Gaps absorb diarization split/merge and CREC "revise-and-extend" turns that were never spoken aloud.

**Output:** per diarized turn → matched `CrecTurn` (or gap). Aggregate per `speaker_label` by majority vote → identity + an aggregate alignment confidence.

**Robustness requirements:**
- Many diarized turns : one CREC turn (over-segmentation) and the reverse — handled by DP gaps.
- Unmatched CREC turns (revise-and-extend) must be tolerated as gaps, never force-fit.
- Procedural interjections ("Without objection…") shouldn't derail alignment.

### 4. Stage-4 integration

A new high-authority identification source inside `identify_speakers(...)`, active only when a CREC record is present **and** aggregate alignment confidence clears a threshold. When present and confident it takes precedence (CREC is authoritative for *who*); otherwise the pipeline falls through to today's layers unchanged. Emits `SpeakerMapping` with `id_method="congressional_record"`, `confidence` from the alignment score, and `needs_review=True` for low-confidence labels. Reconciled against the roster to attach `politician_id` / `politician_slug`.

### 5. Inputs plumbing (media, Part 2)

- **House floor:** House Clerk YouTube through the existing yt-dlp path (captions already short-circuit Whisper at run_local.py:905–914).
- **Senate floor:** senate.gov floor webcast — downloadability is **an open spike** (below), not assumed.
- CLI/GUI gain `date` + `chamber` inputs to trigger CREC enrichment alongside the media URL.

---

## Data-flow guarantees

- **Timestamps / exact-moment promise (ADR-0001):** preserved. Words and timing come from ASR/captions; CREC supplies identity only.
- **Speaker identity-collision guard:** the majority-vote aggregation must not assign two distinct diarized labels to the same politician_id without the existing `_dedupe_identities` reconciliation — CREC alignment feeds that guard, doesn't bypass it.

---

## Error handling

- First granules-list fetch fails / package 404 → `None` (a recess day is normal, not an error; mirrors resolvers returning `None`).
- Per-granule text fetch fails → log-and-skip, continue; if **all** fail → `None` (never present a silently-partial Record as complete).
- `max_granules` caps requests and **logs when it truncates** — no silent capping.
- Alignment below threshold → do not identify from CREC; fall through to existing layers; mark `needs_review`.

---

## Testing strategy

Fixture-based and offline (injected `fetch` returns recorded JSON + htm under `tests/fixtures/govinfo/`):
- `parse_granule_list` filters by chamber and preserves order; pagination follows `nextPage`.
- `html_to_text` strips markup; `parse_granule_turns` splits speaker turns correctly (incl. `of <State>` qualifiers and procedural designations).
- Normalizer resolves member and procedural forms; disambiguates same-surname by state.
- `crec_align` on synthetic ASR/CREC turn sequences: clean 1:1, over-segmentation (many:1), merge (1:many), unmatched CREC gaps (revise-and-extend), procedural interjections.
- End-to-end `fetch_congressional_record_turns` with fixtures: chamber isolation, ordering, `None` on empty/failed package.

---

## Open questions & dependencies

1. **Congressional roster data (prerequisite for the identity *link*).** Resolving CREC names → `politician_id` needs House/Senate member rosters in `essentials`. Existing roster infra targets local bodies; congressional membership is new reference data. Alignment can still label by *name* without it; the `politician_id` link waits on this.
2. **Senate floor media spike.** Confirm whether the senate.gov floor webcast is yt-dlp / HLS downloadable; if not, find the archival source. Blocks Senate end-to-end, not House.
3. **CREC turn granularity vs diarization granularity.** Real-world robustness of the alignment DP against heavy over-segmentation — validate on a real House day early.
4. **Availability lag.** CREC posts next business day; same-day enrichment may find nothing. Surface clearly rather than failing opaquely.
5. **Rate limits.** A full debate day = many granule htm requests; `DEMO_KEY`'s hourly cap can bite. A real `GOVINFO_API_KEY` + `max_granules` mitigate; bulk-XML is a documented escape hatch if it becomes painful.

---

## Implementation phasing (for the planning step)

One connected design, sequenced so each piece is independently shippable and testable:

1. **`govinfo.py` fetch** — CREC package → ordered `CrecTurn`s, fixture-tested. Runnable CLI demo (`python -m src.govinfo 2026-07-16 house`).
2. **Normalizer + congressional roster** — CREC designations → roster politician / procedural role.
3. **`crec_align.py`** — text-anchored sequence alignment, unit-tested on synthetic sequences.
4. **Stage-4 wiring** — new identification source + CLI/GUI date+chamber inputs; House end-to-end.
5. **(Parallel) Senate media spike** — resolves open question 2 before Senate end-to-end.

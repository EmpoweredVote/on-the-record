# Structure-alignment pipeline (agenda / topic segmentation + attention)

**Date:** 2026-07-18
**Status:** Design — pending review (from a /grill-me session)
**Goal:** Break any political recording into meaningful, navigable chunks and attach *attention* (who spoke on what) and — where data exists — *votes*, in a way that is benchmarkable at hundreds of meetings/month across many jurisdictions.

## Reframe (what this is and is not)

The trigger was "how do I make summarization cheaper/better." The real product need underneath is **structure**, not prose: users want a meeting broken into agenda items / topics they can navigate, with who-spoke-on-what and (federally/state) how-they-voted. Summary faithfulness is a secondary, deprioritized concern.

There is **no external ground truth for summaries** — nobody publishes "the correct summary" of a hearing. But there *is* authoritative ground truth for the structure: official agendas, minutes, the Congressional Record, podcast chapters, and an interview's own question turns. This spec builds around aligning to that structure, which makes the output both better *and* benchmarkable.

## Core decisions (from grill-me)

1. **One anchor-first pipeline over a gradient of external-structure availability** — not separate "structured vs unstructured" code paths.
2. **Alignment is anchor-based with holes modeled** — never a positional chop.
3. **Two-pass, publish-then-reconcile** — publish fast against the pre-meeting agenda; correct against minutes when they land (days–weeks later). Published-then-corrected is acceptable.
4. **Generic data model** — `agenda item` with `bill`/`vote` as optional rich-tier fields (Federal/CA only). Local = segmentation-and-attention; votes a rare bonus (local vote data is spotty).
5. **Provenance-first ingestion** — identity is a property of the subscribed source, not inferred. Each jurisdiction is one adapter. The rollout list *is* the build backlog.
6. **Speaker-ID decoupled from segmentation** — segmentation ships day-1; attention ramps behind the confidence gate as voice profiles mature.
7. **Tier-6 (interviews/clips) is a distinct output branch**, not the bottom of the gradient — anchored on the interviewer's question, producing topic-tags + quotes (the existing quote product).
8. **Benchmark = self-labeling wherever external structure exists, calibrated by ~5 adversarial hand-labeled meetings per adapter**, gated on asymmetric per-error-class thresholds.

## Architecture

### The `AlignmentSource` gradient

A single confidence-ranked abstraction. Every input resolves to the strongest available source; the pipeline aligns to it and generates only to fill what's missing.

| Tier | AlignmentSource | Example | Self-labels? |
|---|---|---|---|
| 1 | Authoritative record (NON-verbatim, no timestamps) | Federal / CREC (`src/govinfo.py`, `src/crec_align.py`) | Partial — see note |
| 2 | Official agenda + minutes | CA / LA / council / committee | Yes (minutes) |
| 3 | Announced questions | Debate w/ moderator | Partial |
| 4 | Chapters / show-notes | Podcast (existing YT chapter work) | Partial |
| 5 | Interview question turns | News clip / interview | Partial (Q&A boundaries) |
| 6 | None | Monologue clip, no interviewer | No (human-sampled) |

This is the same "authoritative signal first, model to fill gaps" discipline as the existing CREC speaker-ID oracle — applied to *structure* instead of *identity*.

> **Tier-1 correction (verified 2026-07-18 against the code/fixtures).** CREC is a *speaker-identity* source: **non-verbatim, no timestamps** (`src/govinfo.py` docstring). It is NOT a free "the record gives you everything" tier. Three consequences that gate the Federal adapter and require a **discovery spike** before that adapter can be planned in detail:
> 1. **Floor days are mostly procedural.** Real granule titles are `MORNING-HOUR DEBATE`, `RECOGNITION OF THE MAJORITY LEADER`, `PERSONAL EXPLANATION` — not a clean bill list. "What is a Federal-floor agenda item?" is an unresolved design judgment (holes dominate). Granule list carries `{granuleId, granuleClass, title}` only — **no bill-number field**; bill refs must be parsed from titles/MODS.
> 2. **Item timing has no free mechanism.** The existing identity alignment (`src/crec_align.py`) *deliberately discards timestamps* (ADR-0001) and aggregates to *global per-label* identity, not per-occurrence — so item time-spans cannot be derived transitively from it. A **new, timestamp-preserving granule↔transcript alignment** is required, and its feasibility is unproven at the observed overlap ceiling (0.3–0.5 vs ASR captions).
> 3. **Votes are unbuilt** — no extraction, no fixture, CREC vote-granule shape unknown.
>
> See "Open items" → Federal discovery spike.

### Two output branches, selected by anchor type

- **Agenda branch** (tiers 1–2): align transcript spans to official agenda items → attention → votes.
- **Interview branch** (tiers 3–5): anchor on question turns → Q&A-pair segments → topic-tag + quote-extract.

Both share the front half (ingest → transcribe → diarize → speaker-role/ID) and diverge only at the structure stage.

## Components

### 1. Alignment mechanism (anchor-based, holes modeled)

Detect explicit spoken **anchors** (item/bill numbers, "the clerk will report…", chair's "next up is item 7", or — interview branch — the interviewer's question turn). Use the LLM only to (a) resolve fuzzy anchors ("the Native American fish-and-wildlife bill" → AB 53) and (b) bound spans between anchors. **Anchors first, LLM to disambiguate.**

Alignment is **many-to-many with holes** — this is a first-class part of the contract, not an edge case:

- an agenda item → **zero or more** transcript spans (zero = *pulled / not reached*; many = split across morning/afternoon or a consent block)
- a transcript span → **zero or one** agenda item (zero = *procedural / recess / public comment / off-agenda*)

The trust-killing failures live in the holes: showing "Councilmember X discussed the wildfire item" when it was **pulled and never heard**, or force-fitting a recess onto a bill. `not_reached` and `off_agenda` are explicit outputs.

**Interview-branch holes:** follow-ups (chain to prior question, don't split), multi-part questions (stay one segment), guest-asks-host (role inversion), banter / ad-reads / monologue intros (anchor to nothing).

### 2. Two-pass publish / reconcile

- **Pass 1 (at ingest, timely):** anchor-align to the *pre-meeting agenda*. Publish.
- **Pass 2 (when minutes land, days–weeks later):** reconcile against **minutes** — drop pulled items, correct order, attach actual votes.

Minutes are **PDFs** at the local level, so Pass 2 owns a PDF-extraction sub-project; the "free self-labeling benchmark" is free only *after* the PDFs are parsed. Budget this as real work.

Pass 2 doubles as the **at-scale benchmark oracle** (see Benchmark).

### 3. Per-jurisdiction adapter (provenance-first ingestion)

Each jurisdiction is one adapter bundling `{ video feed + agenda source + minutes source + roster/ID }`, conforming to one interface — the same pattern as the existing audio resolvers and the CREC oracle. **Subscribe to each body's specific feed** (Granicus channel, council YouTube, committee stream), so every item arrives with `{ body, date }` *by construction* and agenda-fetch is deterministic (known body + date → construct the Legistar/leginfo/GovInfo URL).

- Inference-based identity is reserved for orphan clips (already low on the gradient).
- **"Source not found" / low-confidence identity is a first-class logged event, never a silent downgrade** — it feeds the coverage metric.

### 4. Data model

Generic `agenda_item { item_id (jurisdiction-local), title, type (bill|ordinance|resolution|motion|appointment|proclamation|public_comment|other), canonical_legislative_ref?, vote?, spans[] }`.

Tiered essentials attachment:
- **Federal / CA:** item → bill → roll-call vote → politician record (uses existing voting-record data).
- **Local (LA City/County, Monroe Co IN, Bloomington IN):** item → topic + speakers ("who spoke on what"); vote only where the body publishes structured votes.

### 5. Speaker-ID cold-start + decoupled publishing

The composed local claim ("person P spoke on item I") is gated by speaker-ID, which is weak at cold-start (no roster, no CREC oracle, profiles build only from processed meetings). Break the loop:

- **Publish the two halves independently.** Segmentation is anchor-reliable from meeting one → ships day-1. Attention is profile-gated → ramps behind the existing **confidence gate** (see `meeting-confidence-gate-status`), speaker-by-speaker, as profiles mature. New body = trustworthy topic/agenda breakdown + hedged/absent attribution, lighting up over the first several meetings.
- **Cold-start profiles by mining the meeting's own structure:** **roll call is a self-labeling enrollment oracle** (name called → "present" → labeled voice sample every meeting), matched against the body roster. Same move as CREC, ported to local audio. Chair-addresses-by-name, self-intros, and on-screen placards are secondary anchors.

### 6. Interview branch (tier-6)

News clips and podcasts are almost always interviews. The anchor is **the interviewer's question** (intrinsic Q&A turn structure — more universal than chapters). Deliverable = **topic-tags + quote-extraction**, not agenda segmentation. This is the existing quote product (`publish-quotes` → `essentials.quotes` → Read & Rank).

- **Unit = Q&A pair.** Multi-part questions stay one segment.
- **Light dependency = speaker-*role* detection** (interviewer vs guest, far lighter than named speaker-ID; trivial in 2-person interviews) + question-vs-statement detection → this branch's segmentation ships *before* local voice profiles mature.
- **Two topic granularities:** the **segment** gets one topic label (navigation/attention); **quotes** get their own compass-topic via the existing `publish-quotes` flow. A multi-part answer → multiple quotes, each on its own topic — no info loss, no multi-label segments.
- **The question is carried as context for each answer's quote** — directly serving quote faithfulness (see `quote-how-differentiation-principle-gap`).
- Only the true residual (a monologue clip with no interviewer) is human-sampled best-effort.

## Benchmark protocol

The answer to the original "how do I know it's good" question.

- **Not C-SPAN/CalMatters as the source** — they don't summarize. They are one *optional cross-check* at the CA tier (Digital Democracy publishes comparable bill alignment).
- **Self-labeling wherever external structure exists** — minutes (agenda branch), chapters / announced questions / Q&A boundaries (interview branch), plus `audit-quotes` for quote faithfulness. This is the at-scale oracle.
- **Calibrated by ~5 *adversarial* hand-labeled meetings per adapter** — chosen to include a *pulled item, an out-of-order item, a consent block, and a procedural gap*. Five is enough to prove the auto-oracle tracks human judgment for that adapter; clean meetings hide the failures that matter.
- **Gated on asymmetric per-error-class thresholds**, not one accuracy number:
  - `not_reached` **recall** must be very high (showing a pulled item as discussed is the worst error).
  - `off_agenda` **precision** must be high (force-fitting a recess onto a bill is the second-worst).
  - boundary precision is loose (20s off barely matters to a user).
- **Composed claim scored jointly, published decoupled** — benchmark "person P on item I" as a unit (it fails if segmentation *or* speaker-ID is wrong); ship segmentation on a day-1 bar and attention on a profile-maturity bar.
- **Coverage is a headline metric** — "% of ingested meetings that reached the anchor tier they were eligible for." Identification failure and agenda-retrieval failure must show up here, explicitly.

Go/no-go per adapter: `not_reached recall ≥ X` AND `off_agenda precision ≥ Y` AND auto-oracle agrees with the 5 gold meetings within tolerance.

## Rollout = build backlog

One adapter per jurisdiction, in order:

1. **Federal** — CREC/GovInfo already in place; canonical bill/vote IDs; plugs into existing voting-record data. Proves the agenda branch end-to-end.
2. **CA state** — one source (leginfo/OpenStates); Digital Democracy as a ready cross-check.
3. **LA City** — Council File system.
4. **LA County** — Board of Supervisors agenda system.
5. **Monroe County, IN** — thin local; tests graceful degradation.
6. **Bloomington, IN city** — local council.

Each adapter is a bounded, copyable unit: `{ feed + agenda + minutes + roster }` + its 5 adversarial gold meetings.

## Open items / risks

- **Minutes-PDF extraction** is a real sub-project per local vendor; the self-labeling oracle depends on it below the federal level.
- **Local speaker-ID quality** caps the local *attention* product until profiles mature; roll-call enrollment shortens but does not eliminate cold-start.
- **Coverage vs accuracy** — a pipeline that aligns perfectly but only retrieves agendas for a small fraction of local meetings is a weak product; coverage will dominate UX before accuracy does.
- **Published-then-corrected UX** — confirmed acceptable, but the correction (e.g. "item was actually pulled") must be surfaced honestly, not silently swapped.

## Relationship to existing code

- Reuses the **CREC oracle** pattern (`src/crec_align.py`) as tier-1 and as the model for the anchor-first discipline.
- Reuses the **event-kind taxonomy** already in `GATE_THRESHOLDS` (`src/config.py`) to route inputs onto the gradient.
- Reuses the **confidence gate** (`meeting-confidence-gate-status`) for decoupled attention publishing.
- Reuses **`publish-quotes` / `audit-quotes`** as the interview branch's output and benchmark surface.
- Reuses the **resolver/adapter** pattern from podcast/audio ingestion for per-jurisdiction adapters.
- The `TOPIC_CLASSIFY` path (`src/topics.py`) becomes the interview branch's per-segment labeler.

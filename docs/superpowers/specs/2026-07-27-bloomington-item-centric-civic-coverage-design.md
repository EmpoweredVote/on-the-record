# Bloomington Item-Centric Civic Coverage — Design

**Date:** 2026-07-27
**Status:** Approved (brainstorming session)
**Relates to:** `2026-07-18-structure-alignment-pipeline-design.md` (this is its citizen-facing product face for the local tier)

## Problem

on-the-record currently covers meetings *after* they happen, meeting-centrically. For a
citizen, that misses both halves of what local civic coverage should do:

1. **Before:** know what's coming — which meetings, what's on the agenda, what might be
   voted on, whether they can speak. Local news used to do this; in Bloomington it is
   now essentially one person (B Square Bulletin). Official agendas exist but are
   written in government-speak and buried in PDFs.
2. **After:** know what happened *to the thing they care about* — outcome, who said
   what, how members voted — not just "here is a 3-hour meeting video."

## Personas (decided priority)

- **Primary: issue-driven citizen.** Cares about one concrete thing (a rezoning, the
  jail project, bike lanes). Engagement is episodic and topic-triggered. They think in
  keywords and places ("Hopewell", "17th Street"), never in ordinance numbers.
- **Second: civic generalist.** Wants a low-effort "what happened last night" digest.
- **Later: accountability-driven citizen** (tracks a specific member). Falls out as a
  query over data the primary product already produces.

**Accessibility rule:** the *system* translates between citizen language ("Hopewell")
and government language ("Ordinance 26-07"), never the reverse. Plain language is the
product surface; formal identifiers are metadata behind it.

## Elected-representative lens (empower pillar, kept in view, not built)

Working bet — to be validated with real representatives later: reps would value
**(A) their record, organized and citable** (statements/votes with video receipts they
can link) and **(B) protection from misrepresentation** (a neutral, faithful record
defends as well as holds accountable). Design consequences locked in now: item and
person pages are **neutral in voice, provenance-first, permalink-stable** — pages a
rep could cite without embarrassment. Nothing editorialized; everything traces to
video or the official record.

## Core concept: the agenda item is the atom

Each item on an agenda gets a **stable permalink page with two states**:

- **Upcoming state** (published days before the meeting):
  - Plain-language interpretation: what this item is, what is actually being decided.
  - Stage: first reading / public hearing / final vote / appointment / report.
  - Whether public comment is possible at this meeting, and how.
  - Link to official agenda + attachments (ordinance text, staff report).
- **Happened state** (after video processing):
  - Outcome: passed / failed / continued / pulled / no action.
  - Who spoke on it (attention), click-to-seek into that segment of the video.
  - Votes, when they occur, attached to the item.
  - `continued_from` / `continued_to` links when a matter reappears.

**Jurisdiction facts are adapter knowledge, not LLM inference.** Stage semantics and
public-comment rules are encoded per body in the adapter config. The LLM interprets
*content* (what the ordinance does); it never guesses *procedure*.

Around the atoms:

- **Upcoming meetings view** — per body, date/time/location, interpreted agenda.
- **Cross-agenda keyword search** — the "Hopewell" test: one search across upcoming
  and past agendas/items.
- **Meeting recap** — a meeting's item pages stacked; the digest for the generalist
  persona. Presentation only; no new data.

## Scope decisions (from the brainstorming session)

- **Tier: interpreted** (parse + plain-language layer), not calendar or
  structured-only. The interpretation layer is the product.
- **First body: Bloomington City Council only.** Prove the whole loop end to end. The
  adapter is shaped so additional bodies (Monroe County Commissioners, Monroe County
  Council, Bloomington Plan Commission) are configuration bundles, not new code.
  Plan Commission is the noted second body for the issue-driven persona (rezonings are
  decided there before council sees them).
- **No accounts, no email/push.** This build is the browsable/searchable experience
  ("C" in the delivery discussion). Keyword email alerts (no account) and
  account-based watchlists are explicit future layers over the same data — nothing
  here is throwaway when they arrive.
- **Matter-tracking deferred, seeded.** No matter entity. One lifecycle edge ships
  now: a nullable self-reference between item rows (`continued_from_item_id`). When an
  item reappears on a later agenda, linking the appearances turns item pages into
  matter pages incrementally.
- **Person pages deferred.** They are a query over items ("every item where member M
  spoke or voted") once the primary product exists.

## Data model (delta only)

New table **`meetings.agenda_items`**:

| column | notes |
|---|---|
| `id` | PK |
| `meeting_id` | FK → `meetings.meetings` |
| `item_number` | as printed on the agenda; ordering key |
| `title_raw` | verbatim agenda title (government-speak, preserved for provenance) |
| `kind` | ordinance / resolution / appointment / proclamation / report / public-comment / other |
| `legislation_ref` | e.g. "Ordinance 26-07"; nullable; extracted, not invented |
| `summary_plain` | plain-language "what this is" |
| `decision_plain` | "what is actually being decided"; nullable (reports decide nothing) |
| `stage` | from adapter rules; nullable |
| `public_comment` | bool + optional note; from adapter rules |
| `status` | upcoming / happened |
| `outcome` | passed / failed / continued / pulled / no-action; nullable until processed |
| `segment_start_seconds`, `segment_end_seconds` | nullable until video pass |
| `continued_from_item_id` | nullable self-FK; the matter-tracking seed |
| `source_url` | the agenda document this row was parsed from |

Existing table changes:

- **`meetings.meetings`**: support future meetings — a `scheduled` status alongside
  the current published state, plus scheduled date/time. (Exact column shape decided
  at migration time against the live schema.)
- **`meetings.votes`**: nullable `agenda_item_id` FK so votes hang off their item.

Everything else needed (votes, vote_records, segments/speakers) already exists.

## Pipeline: two passes

Mirrors the publish-then-reconcile philosophy of the structure-alignment design.

**Pass A — agenda side (new; runs when an agenda is posted, days pre-meeting):**

1. Bloomington adapter fetches the posted agenda. Per spike findings
   (`2026-07-27-bloomington-publishing-spike-findings.md`): enumerate meetings via
   the city's OnBoard JSON API, download the templated Agenda/Packet PDFs, poll from
   ~6 days out and re-poll through meeting time for addenda.
2. Parse to structured items (number, title, attachments, legislation refs).
3. LLM interpretation with a **faithfulness gate**: every sentence of
   `summary_plain`/`decision_plain` must be traceable to agenda or attachment text.
   Ungroundable content is omitted, not guessed.
4. Publish meeting as `scheduled` with its items. Idempotent re-runs handle agenda
   revisions (items added/pulled before the meeting).
5. Failed fetch/parse/interpret is a first-class logged event (coverage metric per the
   alignment design), never a silent skip.

**Pass B — video side (existing pipeline + local adapter):**

1. After the meeting, ingest video and run the standard pipeline
   (ASR/diarization/speaker-ID) with the published agenda as the `AlignmentSource` —
   the anchor-first alignment from the structure-alignment design, first local use.
2. Items receive segment bounds, outcome, attention. Votes are anchored from spoken
   announcements (roll-call/voice-vote), the same mechanism as federal Slice 2.
3. Item pages flip `upcoming → happened`. Later minutes reconciliation (Pass 2 of the
   alignment design) corrects outcomes/order when minutes publish.

**Adapter shape** (provenance-first, per the alignment design): one bundle per body =
{agenda source, video source, minutes source, roster, council procedure rules}. Body
№2 is a new bundle, not new code.

## Web (cross-repo split, as with prior features)

- **on-the-record** publishes to `meetings.*` (sole writer, as today).
- **ev-accounts** gains: migration for the schema delta; API endpoints for upcoming
  meetings, agenda items per meeting, item-by-id, and **agenda/item keyword search**
  (the static-export site cannot search client-side at useful scale).
- **web/** (static-export Next.js) gains: upcoming meetings page, item permalink page
  rendering both states, recap view, search UI. The site fetches live from the
  ev-accounts API at runtime (there is no publish-triggers-rebuild flow anymore), so
  upcoming pages are current the moment the pipeline publishes.

## Quality gates (two, because the failure modes differ)

1. **Interpretation faithfulness (Pass A).** A mis-summarized *pending* ordinance is
   worse than a mis-summarized past discussion — citizens act on it. Audit checks
   mirror the audit-quotes pattern: groundedness against source text, no invented
   legislation refs, no procedural claims from the LLM.
2. **Alignment correctness (Pass B).** Segment boundaries are load-bearing for the
   citizen product (wrong boundary = wrong video under "what happened"). The
   asymmetric per-error-class thresholds from the alignment design apply from day one:
   not-reached recall and off-agenda precision dominate; boundary precision is looser.
   Calibrate with ~5 adversarial hand-labeled Bloomington meetings (must include a
   pulled item, an out-of-order item, a consent block, a procedural gap).

## Phasing

- **Phase 0 — spike (DONE 2026-07-27).** Findings:
  `2026-07-27-bloomington-publishing-spike-findings.md`. Key results: OnBoard JSON
  API + templated agenda PDFs (adapter = API client + PDF parser); CATS video is
  **public domain** with direct MP4s + machine transcripts (Phase 3 unblocked);
  legislation detail pages carry sponsors and roll-call outcomes (a votes oracle);
  minutes lag 4–7 months (reconcile-only), session memo at ~1 week is a faster weak
  signal.
- **Phase 1 — agenda ingestion + upcoming pages.** Pass A end to end: adapter,
  parsing, interpretation + faithfulness gate, `scheduled` publish, upcoming-meetings
  page and item pages in upcoming state. **Citizen value ships here.**
- **Phase 2 — search.** ev-accounts search endpoint + web search UI across
  upcoming and past agendas/items.
- **Phase 3 — video pass.** Bloomington through the pipeline with agenda alignment;
  items flip to happened; votes + attention attach. Depends on rights-clean video
  (Phase 0 finding).
- **Phase 4 — recap + lifecycle.** Recap view; `continued_from` linking on
  reappearing items.

Phases 1–2 are independent of the video/licensing question entirely — a deliberate
consequence of the before-first ordering.

## Risks

- **CATS video licensing — RESOLVED (Phase 0):** CATS declares government-meeting
  footage public domain; direct MP4s + transcripts, no AI/ML clause. Prefer CATS blob
  over the city's YouTube (platform ToS).
- **Agenda format instability**: in-house city CMS pages can change without notice;
  the adapter treats parse failure as a loud, logged event and the coverage metric
  surfaces drift.
- **Local alignment unproven**: Pass B is the first non-federal use of the alignment
  design. The adversarial hand-labeled calibration set is the mitigation, and Phase 3
  is sequenced last so learning doesn't block citizen value.
- **Interpretation trust**: one bad plain-language summary of a pending ordinance
  could burn credibility with exactly the citizens (and reps) the product courts. The
  faithfulness gate is not optional polish; it gates publish.

## Success criteria

- A Bloomington resident can, without knowing any government vocabulary: see what the
  City Council will discuss next, understand each item in plain language, know whether
  they can speak on it, search "their word" across agendas, and — after the meeting —
  see what happened to the item, hear the discussion at one click, and see the vote.
- Every published claim traces to an official source (agenda, attachment, video,
  minutes).
- Coverage metric: % of posted agendas successfully ingested and interpreted; failures
  are logged events.
- Adding Monroe County Commissioners as body №2 requires only a new adapter bundle.

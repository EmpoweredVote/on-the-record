# Scaled Source Discovery — Design (v1: electoral, shared substrate)

**Date:** 2026-08-02
**Status:** Approved (design); implementation not started
**Owner repos:** on-the-record (discovery engine, GUI tab, skills), ev-accounts DB (new tables)
**Companion specs:** `read-rank/docs/superpowers/specs/2026-07-31-readrank-2026-content-pipeline-design.md` (race queue, source tiers), `docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md` (adapter-bundle concept, deliberative convergence target)

## Problem

Finding original-source candidate media (debates, forums, interviews, town halls) is the
bottleneck of the whole content operation. Today it is a fresh manual or agent-driven web hunt
per race; video shortlists land in `.pipeline/staged/<race>/ingest-candidates.json` files that
nothing consumes, and outlets that repeatedly prove good (AZ Clean Elections, LWV chapters,
PBS affiliates) are not recorded anywhere as watchable. This cannot scale to the 607-race
queue, let alone thousands of races across primaries, specials, and generals.

## Goal

Automate the *finding*; keep the *judgment* human. A daily unattended discovery job produces a
scored triage queue of candidate sources per race. Chris skims the queue in the GUI (seconds
per item), and one click routes an approved item into the existing batch ingestion pipeline
(video/audio) or marks it as a pre-vetted source for quote sourcing (text). Nothing is
downloaded or transcribed without a human click. LLM agents move from being the workflow to
being the exception path.

## Decision log (grill-me session, 2026-08-02)

1. **Electoral-first, shared substrate.** Electoral discovery (fuzzy search problem, hard
   deadlines) ships first; deliberative bodies (enumeration/adapter problem) get structural
   schema accommodation only. (Q1)
2. **Human gate at source approval.** Discovery is automatic; ingestion requires a click.
   Designed so per-outlet trusted auto-ingest is a later flag-flip. (Q2)
3. **Layered engine with a flywheel.** Channel watchlists (precision) + per-race search
   sweeps (recall) feed one queue; every approved discovery can promote its channel into the
   watchlist. Calendars and agent sweeps are later/fallback layers. (Q3)
4. **Two-stage matching, C+.** Free prefilter (names + duration) → cheap-model structured
   verdict, with a captions peek (discourse shape, not speaker ID) for mid-confidence items. (Q4)
5. **One queue, two routes.** `ingest` (video/audio) and `quote_source` (text). (Q5)
6. **State lives in the ev-accounts DB**, `essentials` schema, beside `readrank_race_pipeline`. (Q6)
7. **launchd daily job on the Mac + a Discovery tab in the existing FastAPI GUI.** Local
   residential IP avoids YouTube datacenter blocking; zero new infra. (Q7)
8. **Seeding: harvest proven channels now; rolling deadline-ordered per-state outlet packs;
   candidate channels via flywheel only. Outlets are active on insert** — the per-item gate is
   the protection. (Q8)
9. **v1 sweeps are YouTube-only; zero-source alarm (election ≤30 days, race still sourcing,
   no approved sources) triggers one-shot agent gap-fillers; discovery health is a GUI header
   strip**, no separate dashboard or notifications. (Q9)
10. **Deliberative accommodation is structural only** (nullable `chamber_id` anchors, open
    outlet `kind` enum); deficiencies and the v2+ build-out are documented below. (Q10)

## Architecture

```
                    ┌─ Layer 1: watchlist poll (outlet RSS — YouTube channels, podcasts)
  daily launchd ────┤
  discovery run     └─ Layer 2: per-race YouTube search sweeps (yt-dlp ytsearch, cadence by
                         election proximity)
                              │
                    stage 1: free prefilter (candidate-name match, duration signal)
                              │  (~95% of watchlist noise dies here)
                    stage 2: LLM verdict (Haiku-class, race-roster context;
                             captions peek for mid-confidence items)
                              │
                    essentials.discovered_sources  (dedup: source_key + existing meetings)
                              │
                    GUI Discovery tab (skim, scrub, approve/reject)
                       ├─ approve → ingest  ────────→ existing batch pool → full pipeline
                       ├─ approve → quote_source ───→ race-pipeline sourcing
                       ├─ reject (+reason) ─────────→ flywheel training data
                       └─ "watch this channel" ─────→ essentials.source_outlets (flywheel)

  Layer 4 (exception path): zero-source alarm → one-shot agent gap-filler
                            → rows with discovered_via='agent' into the same queue
```

## Data model (ev-accounts DB, `essentials` schema)

### `essentials.source_outlets` — the watchlist registry

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `name` | text | e.g. "KXAN", "AZ Clean Elections" |
| `kind` | text | `youtube_channel` \| `podcast_rss` \| `web_page`; open enum — deliberative kinds (`granicus`, `legistar`, `onboard`, `civic_page`) are future additions, not a new table |
| `feed_url` | text | the pollable URL (YouTube channel RSS: `youtube.com/feeds/videos.xml?channel_id=…`) |
| `external_channel_id` | text null | platform-native id (YouTube channel id) |
| `state` | char(2) null | geographic scope |
| `chamber_id` | uuid null FK | deliberative anchor; unused in v1 |
| `added_via` | text | `seed` \| `flywheel` \| `manual` |
| `active` | boolean | outlets are active on insert |
| `last_polled_at` | timestamptz null | |
| `notes` | text | |
| `created_at`, `updated_at` | timestamptz | |

### `essentials.discovered_sources` — the triage queue

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `source_key` | text unique | normalized identity from `src/source_key.py`; the dedup key |
| `url` | text | |
| `title`, `description_snippet` | text | |
| `outlet_id` | uuid null FK | null for search/agent finds from unregistered channels |
| `duration_seconds` | int null | |
| `published_at` | timestamptz null | |
| `matched_politician_ids` | uuid[] | primary anchor — quotes attach to politicians, so finds survive primary → general |
| `race_id` | uuid null FK | grouping convenience |
| `chamber_id` | uuid null FK | deliberative anchor; unused in v1 |
| `event_kind_guess` | text null | from `src/event_kinds.py` vocabulary |
| `source_tier_guess` | smallint null | curation-principles tier 1–4 (5 = auto-reject) |
| `route` | text | `ingest` \| `quote_source` |
| `confidence` | real | classifier score |
| `why` | text | classifier's evidence sentence — what makes the human skim fast |
| `discovered_via` | text | `watchlist` \| `search` \| `agent` |
| `status` | text | `pending` \| `auto_filtered` \| `approved` \| `rejected` \| `ingested` \| `superseded` |
| `status_reason` | text null | reject reason chips: `clip-not-original`, `wrong-person`, `tier-5`, `duplicate`, `other` |
| `reviewed_at` | timestamptz null | |
| `created_at` | timestamptz | |

### `essentials.discovery_race_state` — sweep bookkeeping

`{race_id pk, last_swept_at, last_alarm_at}`. Keeps sweep cadence and alarm state out of
`readrank_race_pipeline`, which this project does not modify.

### Invariants

- **Every stage-2-evaluated item is stored.** Below-floor verdicts land as `auto_filtered`
  (hidden in the GUI by default) so daily polls never rescore the same upload and the audit
  trail is complete. Stage-1 kills are not stored (free to re-kill).
- **Rejects are kept, never deleted** — they are the flywheel's training data and the
  re-surfacing suppressor.
- **Already-processed sources never resurface:** the poller checks `source_key` against both
  `discovered_sources` and existing meetings (`runner.find_meeting_by_source`).

## Discovery engine

One script in the `poll_agendas.py` mold (launchd daily; state/logs under
`~/CouncilScribe/discovery/`; `--race` and `--dry-run` flags for manual runs):

1. **Watchlist poll.** Fetch RSS for every active outlet (YouTube channel feeds and podcast
   feeds are free, keyless). New items → stage 1.
2. **Race sweeps (YouTube-only in v1).** For each race due by cadence — election >60 days out:
   weekly; 31–60 days: ~2×/week; ≤30 days: every 2–3 days — run yt-dlp `ytsearch` queries
   shaped `"<candidate full name>" <debate|forum|town hall|interview>` per rostered candidate.
   Results → stage 1.
3. **Stage 1 — free prefilter.** Normalized candidate-name/race-term matching against
   title + description (whitespace/diacritics/case normalization; name-collision-aware —
   require a second signal such as state, office term, or opponent name for short/common
   names). **Duration is a hard signal:** <8 min from a news channel is heavily downweighted
   (news package about, not speech by); ≥25 min is boosted (likely full event).
4. **Stage 2 — LLM verdict.** A Haiku-class model (model-swappable client, same pattern as the
   layer-3 speaker-ID identifier) receives item metadata plus the race roster and returns
   `{race_id, candidates_present, event_kind_guess, source_tier_guess, original_vs_clip,
   route, confidence, why}`.
   - **Captions peek** for mid-confidence items: fetch YouTube auto-captions (VTT, no video
     download) and judge **discourse shape** — sustained first-person policy speech,
     moderator/Q&A signatures ("you have sixty seconds", "Senator, my question is…") vs
     third-person anchor narration with soundbites. This separates original events from
     news packages *about* candidates.
   - The verdict ranks items for the human skim; it **never claims speaker identity**. Real
     speaker ID happens post-approval in the existing pipeline (diarization + anchored
     speaker-ID). Residual errors cost one wasted ASR run and are caught at transcript review.
   - Triage tunes for **recall**; the human gate owns precision. False positive ≈ seconds of
     skim; false negative ≈ a missed source.

## Triage GUI (new tab in the existing FastAPI app)

- Grouped by race; sorted by election proximity, then confidence. Row: thumbnail, title,
  outlet, duration, `why`, deep link to the source for a 10-second scrub.
- **Approve → ingest:** one-click enqueue into the existing batch pool with prefilled fields
  (event_kind, date, race, event_org from discovery metadata). An "edit first" secondary
  action opens the prefilled `/new` form for clip windows, guests, and other special cases.
- **Approve → quote-source:** marks the row for race-pipeline pickup as a pre-vetted source
  link.
- **Reject:** one click + reason chip (see status_reason above).
- **Flywheel:** approving an item whose channel is not in the registry offers one-click
  "watch this channel" → inserts a `source_outlets` row (`added_via='flywheel'`).
- **Header health strip:** zero-source alarm list (red), pending counts per race, watchlist
  feeds that failed to poll. No separate dashboard, no notifications — GUI-only by decision.

## Recall safety net

- **Zero-source alarm:** a race trips when election is ≤30 days away, its pipeline status is
  `needs_quotes` (roster done, sourcing incomplete — later statuses already have quotes), and
  it has zero approved items on either route.
- Tripped races surface in the health strip and each spawns a **one-shot agent gap-filler** —
  a deep web hunt (today's race-pipeline style, general web included) whose findings land as
  `discovered_sources` rows with `discovered_via='agent'`, into the same triage queue and the
  same human gate.

## Registry seeding

1. **Harvest proven channels (immediate).** One-time script extracts the ~55 distinct
   `source_channel` values from existing local meetings, resolves each to its YouTube channel
   id + RSS feed, inserts `source_outlets` rows (`added_via='seed'`).
2. **Per-state outlet packs (rolling, deadline-ordered).** As each state comes inside ~90 days
   of an election in the queue, a cheap-model agent researches and verifies a pack: local TV
   news channels, PBS + NPR affiliates, the LWV state chapter, Clean-Elections/civic-debate
   orgs, and the 1–2 biggest newspaper channels — realistically 8–15 outlets/state. Aug/Sep
   primary states first (MI, WI, MN, AK, FL, WY, MA, NH, RI, DE). **No big-bang 50-state
   buildout** — a pack built months early is mostly silent channels and pure agent spend.
3. **Candidate-owned channels: flywheel only.** Campaign channels enter when a sweep finds
   one worth approving; no up-front research across ~2,000 candidates.

## Error handling & guardrails

- Feed failures: logged, surfaced in the health strip, retried next run.
- yt-dlp bot-check/rate errors: exponential backoff; politeness sleeps between searches.
- Classifier API failure: item stays unclassified and is retried next run.
- DB unreachable: run aborts cleanly; launchd fires again next day (and on wake for missed
  runs — the laptop-asleep case).
- **Per-run spend cap** on classifier calls; when hit, the truncation is logged loudly
  (no silent caps) and remaining items carry to the next run.
- All writes idempotent on `source_key`.

## Testing

- **Unit:** name normalization + collision cases; source_key dedup against existing meetings;
  sweep cadence math; alarm query; verdict JSON parsing; duration heuristics.
- **Golden fixtures:** canned RSS payloads and ytsearch metadata → expected
  `discovered_sources` rows end-to-end through stage 1.
- **Classifier eval set:** labeled examples built from the 136 real ingested meetings'
  sources (positives) plus noise uploads from the same channels (negatives) — same
  eval-harness philosophy as the layer-3 speaker-ID eval. Measures: recall on true events,
  precision of `original_vs_clip`, calibration of `confidence`.

## v1 deficiencies → v2+ roadmap

Deliberate v1 gaps, in rough priority order for later build-out:

1. **YouTube-only sweeps.** v2: TV-station sites and embed players, Facebook/Vimeo, and/or a
   news-search API. Chris explicitly wants to move beyond YouTube.
2. **No calendar layer.** v2: scrape Ballotpedia race-page debate listings, Vote411/LWV event
   calendars, debate commissions — advance notice that an event exists *before* a recording
   does, enabling "recording expected, go hunt" tasks.
3. **Text route exists but thin.** Few text watchlist feeds at launch; later: newspaper
   opinion RSS, campaign newsroom feeds, statehouse press-release pages.
4. **No auto-ingest.** Mode C (per-outlet trusted auto-ingest for proven channels whose
   uploads match a tracked race) is a flag-flip once approve/reject history justifies it.
5. **No deliberative adapters.** Convergence path: a civic platform (Granicus, Legistar,
   OnBoard, CATS) becomes a new `source_outlets.kind` whose poller emits `discovered_sources`
   rows like everything else; the Bloomington adapter-bundle registers its video source as an
   outlet row. Deliberative bodies then inherit triage, dedup, and the GUI for free. The
   Bloomington agenda poller and runbook stay untouched until then.
6. **Laptop-bound scheduler.** Acceptable now (launchd + wake catch-up); later a small server
   with a residential proxy if reliability demands it.
7. **GUI-only alerting.** No push/email when the alarm trips or high-confidence items arrive.
8. **No cross-race dedup UI.** A multi-race forum (rare) matches one race in v1; the same
   source_key can't hold two race anchors — revisit if it bites.

## Out of scope

- Any change to `readrank_race_pipeline` semantics, publish-quotes, audit-quotes, or the
  ingestion pipeline itself.
- Auto-publishing anything. The confidence gate on meeting publishing is a separate system.
- C-SPAN sources (ToS bars AI/ML processing — standing exclusion; House/Senate floor stays on
  the public-domain House Clerk CDN / GovInfo path).
- Judicial/uncontested races (inherited from the content-pipeline spec).

## Success criteria

1. Per-race source-finding drops from hours of hunting to minutes of skimming: for a typical
   contested race, ≥1 approved tier-1/2 source within one triage session of the race entering
   `needs_quotes`.
2. Every queue race inside 30 days of its election has either approved sources or a tripped,
   visible alarm — zero silent coverage holes.
3. Flywheel effect is measurable: the share of approved items arriving via `watchlist` (vs
   `search`/`agent`) grows month over month.
4. Zero already-ingested sources ever resurface in the triage queue.
5. Discovery runs unattended for a week with no intervention beyond triage clicks.

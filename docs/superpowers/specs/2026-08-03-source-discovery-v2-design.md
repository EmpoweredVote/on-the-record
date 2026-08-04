# Scaled Source Discovery — v2 Design (operational trust + beyond-YouTube)

**Date:** 2026-08-03
**Status:** Approved (design); implementation not started
**Owner repos:** on-the-record (engine, GUI, runbooks), ev-accounts DB (one new table)
**Predecessor:** `docs/superpowers/specs/2026-08-02-source-discovery-design.md` (v1, shipped
2026-08-02 via PR #142). This spec builds out v1's §"v1 deficiencies → v2+ roadmap" to the
extent the first week of operating evidence supports — no further.
**Runbook:** `docs/runbooks/source-discovery.md` (updated by this work).

## Problem

v1 proved the matching→triage→route loop in one morning: a single real run queued 40 items,
Chris triaged all of them (15 approved/ingested, 44% approve rate), the flywheel and both
routes work, and race-pipeline consumes approved `quote_source` rows. But the operating
evidence exposes a different bottleneck than v1's roadmap assumed:

- **v1 has effectively run once, manually.** The launchd job has never demonstrably fired:
  no poll.log has ever been written, no DB fingerprints exist past the Aug 2 manual runs,
  and the plist points at the primary human checkout — the exact defect PR #144 fixed for
  `poll_agendas` the same week (branch coin-flip; launchd TCC cannot read `~/Documents`
  git metadata; logs vanish when the log dir doesn't pre-exist).
- **The sweep-cadence layer has never recorded a sweep** (`discovery_race_state` is empty;
  the only sweep ever was a forced `--race` run that hit cap/bot-check, so `record_sweep`
  correctly declined). Spend-cap frequency and cadence-in-practice are unobservable.
- **Alarm history doesn't exist.** `--print-alarms` computes live (3 alarms right now:
  WI Governor Aug 11, FL + WY Senate Aug 18) but `last_alarm_at` is never written.
- **The biggest observed noise class is staleness**: 8 of 19 rejects were "stale / old
  cycle" — watchlist backfill surfacing prior-cycle uploads that a `published_at` cutoff
  kills for free.
- **The eval set is still 8 synthetic examples**, while the DB now holds 34 real human
  verdicts — free labeled data nobody harvests.
- **Beyond-YouTube evidence is narrow**: pre-v1 agent shortlists contain 16 local
  TV-station-site videos (Gray/Nexstar/Scripps/Sinclair templated sites, largely
  yt-dlp-extractable) versus zero Facebook and zero Vimeo finds.

v2 therefore leads with operational trust, adds the one evidenced new source class
(TV-station / news-site RSS), and takes only ride-alongs the data demanded.

## Decision log (grill-me session, 2026-08-03)

1. **Slice 0 = operational trust, in-spec.** Scheduled discovery moves onto the PR-#144
   clone-wrapper pattern; runs are recorded and visible in the GUI; alarm state persists.
   Not an ad-hoc ops chore: every later layer inherits this failure mode otherwise. (Q1)
2. **Beyond-YouTube = TV-station chain sites only, as a watchlist kind.** News-search API
   is a separate later decision (revisit after outlet packs + gap-fillers are actually
   exercised); Facebook and Vimeo are dropped from v2 — zero evidence. (Q2)
3. **No coded calendar layer.** The gap-filler agent prompt gains a calendar-check line
   (Ballotpedia race page + Vote411); if agent runs keep finding scheduled events we'd
   otherwise miss, that evidence buys a v3 scraper. (Q3)
4. **Mode C (per-outlet auto-ingest) stays unbuilt.** v2 ships the evidence surface only:
   per-outlet reviewed/approved stats in the GUI, and the qualification bar written here —
   an outlet qualifies at ≥10 human-reviewed items, ≥90% approved, zero
   `wrong-person`/`clip-not-original` rejects; auto-ingest would still fire only on
   tracked-race matches ≥25 min and never bypasses the publish gate. (Q4)
5. **Text route: RSS via outlet packs only.** Outlet-pack agents may register text RSS
   feeds (newspaper opinion sections, campaign newsrooms that expose RSS) alongside
   YouTube channels; no-duration text items become first-class through stages 1–2; no
   scraping adapters for RSS-less campaign/statehouse pages. (Q5)
6. **Deliberative adapters stay deferred**, structural accommodation unchanged. Re-evaluate
   when the electoral lane has run unattended for a month or a second deliberative city is
   planned. (Q6)
7. **Ride-alongs: recency filter, eval harvest + calibration metric, yt-dlp backoff.**
   Notifications stay GUI-only (v1 posture unchanged); superseded-row recovery dropped
   (zero superseded rows); per-race pending visibility rides the Q4 stats work. (Q7)
8. **ToS posture for station sites: public syndication surfaces only.** Poll RSS/sitemap
   endpoints, honor robots.txt mechanically, per-domain politeness delays, no auth/paywall
   circumvention. Only an explicit C-SPAN-style AI/ML-processing bar disqualifies a
   station, checked once at outlet registration and recorded on the outlet row. (Q8)

## Slice 0 — operational trust

**Scheduler.** `poll_discovery.py` runs via the clone-wrapper pattern from
`~/CouncilScribe/automation-checkout` (fast-forwarded to origin/main each run), not the
primary human checkout. Generalize `scripts/run_scheduled_poll.sh` to take the target
script as an argument (one wrapper, one header explaining the TCC/clone rationale; the
agenda-poll plist passes `poll_agendas.py`, the discovery plist passes
`poll_discovery.py`); both plists' `ProgramArguments` change accordingly. The wrapper
`mkdir -p`s the job's log dir so `poll.log` accumulates from the first unattended run.

**Run records.** New table (the only new DDL in v2):

### `essentials.source_discovery_runs`

*(Implementation amendment 2026-08-03: originally specced as `discovery_runs`, renamed —
prod already has an unrelated `essentials.discovery_runs` table from migration 070, the
per-jurisdiction candidate-discovery run log. The `source_` prefix matches the v1 family.)*

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `started_at`, `finished_at` | timestamptz | `finished_at` null = crashed mid-run |
| `trigger` | text | `scheduled` \| `manual` \| `race` (forced `--race` run) |
| `items_examined` | int | stage-1 input count |
| `classified` | int | stage-2 calls made |
| `inserted_pending` | int | |
| `inserted_auto_filtered` | int | |
| `spend_capped` | int | items deferred by the cap |
| `failure_count` | int | |
| `failures` | text null | newline-joined failure summaries (truncated) |

The engine writes one row per run (insert at start, update at exit — a row with null
`finished_at` is itself a signal). The GUI health strip shows: **last scheduled run —
when · ok/failed/crashed · examined/classified/queued/capped**, and flags when no
scheduled run has completed in >36 h.

**Alarm history.** When the alarm query trips for a race, write
`discovery_race_state.last_alarm_at` (upserting the row if absent). `--print-alarms`
output is unchanged; history becomes queryable.

**Definition of done for slice 0:** 7 consecutive unattended daily runs recorded in
`source_discovery_runs` and visible in the GUI — v1's success criterion #5, finally measurable.

## Slice 1 — TV-station / news-RSS watchlist layer

**New outlet kind `web_rss`.** A generic RSS 2.0/Atom parser joins the two existing
dialects in `src/discovery/feeds.py` (YouTube Atom, podcast RSS). Items carry
title/description/link/`published_at` and **no duration**. This covers TV-station
politics-section feeds, newspaper feeds, and campaign newsrooms with RSS — one parser,
no per-chain code. The v2 parser handles RSS 2.0 and Atom only; news-sitemap XML is a
permitted surface by posture but not parsed in v2 — a station with no RSS at all simply
isn't registrable yet (noted in Out of scope). Chain knowledge (which feed URLs exist on Gray/Nexstar/Scripps/Sinclair
templates) lives in the outlet-pack runbook prompt, not in adapters.

**Politeness & ToS enforcement (per Q8).**
- Fetch only registered feed URLs (RSS/sitemap endpoints); never crawl page graphs.
- Check robots.txt per domain (cached per run) before fetching; a disallow suppresses the
  fetch and surfaces in the health strip like any feed failure.
- Per-domain politeness delay between fetches (config, ~2 s).
- No auth or paywall circumvention anywhere.
- Outlet registration (packs or flywheel) records the AI-bar check verdict in
  `source_outlets.notes`; a station with an explicit AI/ML-processing bar is not
  registered. C-SPAN remains hard-excluded.

**Stage 1.** Same name/collision matching. Duration heuristics are skipped when
`duration_seconds` is null (never treated as short-clip). The recency filter (below)
applies.

**Stage 2.** For mid-confidence web items, a **page peek** replaces the captions peek:
fetch the article page text (politely, same domain rules) and judge discourse shape —
does the page present a full recorded event (embedded full video, transcript-like body,
candidate-bylined text) or a news package *about* candidates? The verdict fields are
unchanged; `route` defaults to `quote_source` for web items, with `ingest` allowed when
the page signals a full embedded video.

**Approve → ingest for web items.** Before enqueueing into the batch pool, run a yt-dlp
extractability probe (`--dump-json`, no download) against the page URL. Success →
enqueue as usual (yt-dlp's Anvato/chain extractors cover most station embeds). Failure →
bounce to the Edit-first flow with the probe error shown, so unextractable embeds never
poison the batch pool. `source_key` already dedups arbitrary URLs (`url:host/path`);
no change.

**Outlet packs.** The runbook prompt gains: register station politics-section RSS and
newspaper/campaign text RSS alongside YouTube channels; perform and record the AI-bar
check; note the chain (Gray/Nexstar/Scripps/Sinclair) since feed-URL discovery is
templated per chain. Aug/Sep primary states remain first in line.

## Ride-alongs

- **Recency filter.** Stage-1 drop for items with `published_at` older than
  `DISCOVERY_MAX_ITEM_AGE_DAYS` (default 630 — reaches back past the previous general
  election; recalibrated 2026-08-04 from the original 420 after measuring the corpus:
  every observed human "stale" reject was ≥1622 days old, the oldest *ingested* item was
  329 days, and 7 high-confidence pending items sat in the 421–630 band — a 420 cutoff
  would have silently deleted wanted content). The check runs after the candidate-name
  match, so the counter reads "named a tracked candidate but too old" — an over-drop
  alarm — and each drop logs a per-item `STALE` line. Undated items pass (stage 2 owns
  them). Dropped counts land in the DONE line and `source_discovery_runs`.
- **Eval harvest + calibration.** `scripts/harvest_discovery_verdicts.py` exports
  human-triaged `discovered_sources` rows (approved/ingested → `gold_relevant: true`;
  rejected with reason `clip-not-original`/`wrong-person`/`tier-5` → `false`; `stale`,
  `duplicate` and `other` are excluded as non-relevance verdicts) into
  `tests/fixtures/discovery_eval_real.jsonl`, deduped on source_key, race context
  resolved from the row. `eval_discovery_classifier.py` reads both fixture files and
  adds a calibration section: approve-rate per confidence bucket + Brier score. 34
  examples exist today; re-run after each triage week.
- **yt-dlp backoff.** Bot-check/429/rate errors during sweeps retry with exponential
  backoff + jitter (bounded, ~3 retries) inside the run. After exhaustion the error still
  propagates exactly as today — exit 1, cadence clock not reset — so a hard bot-check
  wave still defers the sweep rather than burning the cadence slot.
- **Mode-C evidence surface.** GUI Discovery tab gains a per-outlet stats readout
  (reviewed count, approve rate, identity-class reject count — computed from existing
  tables, no schema change) and per-race pending counts. The Q4 qualification bar is
  documented here and in the runbook; no auto-ingest code path exists in v2.
- **Runbook edits.** Gap-filler prompt: add "check the Ballotpedia race page and Vote411
  for scheduled or recent debates/forums; record upcoming events as notes in `why`."
  Outlet-pack prompt: additions above. Daily-workflow section: read the last-run line
  before triaging.

## Testing

- **Unit:** generic RSS/Atom parsing (fixtures from real Gray/Nexstar/Scripps feeds);
  robots.txt gate; recency cutoff (boundary, undated, timezone); no-duration stage-1
  path; extractability-probe success/failure routing; run-record lifecycle (crash leaves
  null `finished_at`); alarm upsert.
- **Golden fixtures:** one station-RSS payload end-to-end through stage 1 → expected
  `discovered_sources` row shapes (route defaults, null duration).
- **Eval:** harvest script round-trip on a synthetic triaged set; calibration math on a
  known distribution.
- **Manual/E2E:** one real station item discovery → approve → probe → batch pool →
  meeting, before slice 1 is called done.

## Out of scope (v2)

- News-search API sweeps (revisit only after outlet packs + gap-fillers have actually
  been exercised and still leave holes).
- Facebook, Vimeo, and other embed hosts (zero evidence).
- Coded calendar layer (Ballotpedia/Vote411 scraping) — prompt-line only, per Q3.
- Mode-C auto-ingest execution path (evidence surface only, per Q4).
- Campaign-newsroom/statehouse page scraping (RSS-less text sources).
- News-sitemap XML parsing (RSS/Atom-less stations wait for the case to actually arise).
- Deliberative adapters (Granicus/Legistar/OnBoard) — accommodation unchanged, per Q6.
- Server/residential-proxy hosting; push/email notifications; superseded-row recovery
  UI; cross-race dedup UI.
- Everything out of scope in v1 (no `readrank_race_pipeline` changes, no auto-publish,
  C-SPAN exclusion, judicial/uncontested races).

## Success criteria

1. **7 consecutive unattended scheduled runs** recorded in `source_discovery_runs` and visible
   in the GUI health strip.
2. **≥1 station-site (non-YouTube) source** flows discovery → approve → ingest → meeting
   end-to-end.
3. **Stale/old-cycle rejects ≈ 0** after the recency filter (they were 8 of 19).
4. **Eval runs on ≥30 real-labeled examples** with calibration reported alongside
   recall/precision.
5. v1 carried criteria still hold: zero already-ingested resurfacing, alarms visible
   (now also persisted), flywheel share of approvals growing.

## Sequencing

Slice 0 → ride-alongs → slice 1. Slice 0 first because every other item's evidence
(spend-cap frequency, sweep cadence, flywheel share) is invisible until runs are
recorded. Urgent ops that must NOT wait for this code (run them from the runbook now):
gap-fillers for WI Governor (alarmed, election Aug 11) and FL/WY Senate (alarmed,
Aug 18, zero discovered rows); outlet packs for the remaining Aug/Sep primary states.

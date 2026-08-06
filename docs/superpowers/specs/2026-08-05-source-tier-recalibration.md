# Source Tier Recalibration — questioner independence as the axis

**Date:** 2026-08-05
**Status:** Approved (design, 2026-08-05); implementation not started
**Owner repos:** on-the-record (classifier prompt, GUI ordering, runbook, skills, consent
letters), essentials (`docs/QUOTE-CURATION-PRINCIPLES.md` §5 — the canonical ladder),
ev-accounts DB (one new column on `essentials.source_outlets`).
**Origin:** Chris's mid-triage rethink of 2026-08-05, working the ~282-item source-discovery
v2 queue (v2 shipped via PR #147). Six directions, resolved in the grill-me session below.
**Runbook:** `docs/runbooks/source-discovery.md` (updated by this work).

## Problem

The v2 discovery loop works — queue fills daily, triage routes items, race-pipeline consumes
approvals — but a week of real triage exposed that the loop ranks the wrong thing:

- **Confidence doesn't rank policy substance.** The classifier's `confidence` measures
  "relevant and original," so the triage queue (ordered `election_date, confidence,
  created_at`) surfaces well-matched fluff above probing substance.
- **The tiers are format-based and misplace the substance.** The current ladder (1
  debate/forum · 2 news interview · 3 prepared remarks *including town halls* · 4 bylined
  written) puts citizen-questioned town halls two rungs below a softball podcast that
  classifies as a "news interview." The thing the tiers should measure is **questioner
  independence** — how hard is it for the candidate to only say what they came to say.
- **Partisan podcasts over-credit.** Sympathetic hosts yield little policy substance — yet
  they are often the *only* surface where libertarian/independent candidates speak at all
  (the 2026 TX Senate independents have no other sourceable video). Any demotion needs a
  per-candidate escape hatch, not an exclusion.
- **The text route has no notion of the ideal text shape.** The best text sources found so
  far are Q&A-shaped — WyoFile's per-candidate questionnaire pages (unedited answers to a
  journalist's fixed questions, found by the WY gap-filler 2026-08-04) — but the classifier
  has no `questionnaire` kind and no guidance preferring Q&A shape over news stories.
- **County races are unreachable.** State/federal outlet packs don't cover county-level
  races; `source_outlets` has `state char(2)` and nothing finer.
- **Three barred chains have an explicit unlock.** Gray Media's TOU bars AI/ML tools from
  access/copy/store/reproduce of GLM Content *"without prior express written consent"* —
  consent is namable and obtainable. Hearst's and Nexstar's bars are AI-**training**-specific
  (which this pipeline does not do). One narrow consent letter is the highest-leverage
  source-side action available.

## Decision log (grill-me session, 2026-08-05)

1. **Keep the 1–5 numbering shell; redefine the rungs.** (Q1) Renumbering to six rungs would
   break "prefer 1–2 / justify 3–4 / 5 = auto-reject" everywhere it is encoded — classify.py,
   CHECKS.md, race-pipeline SKILL, the runbook's gap-filler prompt, the
   `source_tier_guess` smallint semantics on existing `discovered_sources` rows, and the
   DDL note "tier 1–4 (5 = auto-reject)". The shell survives; the contents move.
2. **A Starting Point and candidate questionnaires are tier 2, with caveats; the
   verbatim-sentence rule becomes a written-MEDIUM rule.** (Q2) Both have genuinely
   independent questioners and zero follow-up. §5's verbatim-sentence requirement currently
   rides on tier 4; it is restated to travel with the medium — any written source, any tier:
   verbatim sentences only, never curator summaries.
3. **Partisan-vs-independent is prompt-only judgment for now.** (Q3) Search and agent finds
   often have no outlet row (`outlet_id` null), so the classifier needs a judgment rule
   regardless; a `questioner_independence` outlet column is deferred until tier-accuracy
   evidence (decision 8) shows the prompt failing on repeat outlets.
4. **The only-surface exception is a written per-CANDIDATE rule; the justification note is
   the mechanism.** (Q4) Nothing at discovery time excludes by tier (`relevant` gates on
   original-vs-clip only), and the classifier cannot know a candidate's coverage. The rule
   lives in §5, the race-pipeline SKILL, and the gap-filler prompt: a tier-3
   sympathetic-host interview is never excluded when it is a candidate's only sourceable
   speech — and "only sourceable speech for this candidate" *is* the justification note.
5. **Triage reorders by tier, and pending rows are re-classified once at deploy.** (Q5)
   New order: `election_date asc → source_tier_guess asc → confidence desc → created_at
   desc`. A one-shot script re-runs the classifier over `status='pending'` rows so the
   queue sorts coherently from day one (metadata-only re-classification; the fetch-time
   captions peek is not re-run).
6. **`questionnaire` is a discovery-only event kind.** (Q6) Added to classify.py's
   `ALLOWED_KINDS` and the prompt's emit list. The ingestion vocabulary in
   `src/event_kinds.py` is untouched — it drives speaker-ID framing and role prompts for
   ingested recordings, and a questionnaire can never be one (route=`quote_source` by
   construction).
7. **County packs: add the column and the runbook section now, build packs on demand.** (Q7)
   Nullable `county text` on `essentials.source_outlets` (ev-accounts migration — fetch +
   `check-migration-numbers.mjs` first, per the numbering trap). Pack-building waits for a
   county race to actually enter the pipeline queue or alarm.
8. **Consent ask: narrow civic-use, chain-wide, with an express no-training clause.** (Q8)
   Plus deliverables confirmation (Q9): full list below including the `expected_tier`
   eval ride-along — the evidence stream that decides whether prompt-only partisan judgment
   (decision 3) suffices.

## The new ladder (canonical text lives in essentials §5)

| Tier | Contents | Change |
|---|---|---|
| 1 | **Debates, candidate forums, town halls** — independent or citizen questioning, live, follow-up possible | town halls move **3 → 1** |
| 2 | **Independent-questioner interviews & Q&A** — mainstream/local news interviews (network, affiliate, nonpartisan nonprofit newsrooms); A Starting Point (caveat: curated questions, zero follow-up — structured self-presentation; prefer a genuine press interview when both exist); candidate questionnaires (WyoFile archetype — unedited answers to an independent questioner's fixed questions; the best available TEXT source) | ASP placed; questionnaires promoted from implicit ~4 |
| 3 | **Sympathetic-questioner interviews** (partisan/ideological podcasts and shows) **and prepared public remarks** (stump/floor speeches, testimony) | partisan interviews move **2 → 3**; prepared remarks stay |
| 4 | **Candidate-bylined written** — op-eds, official platform pages | unchanged |
| 5 | **Hard-excluded** — hot-mic, private, secretly-recorded, off-guard "gotcha" | unchanged |

Rules that survive verbatim: strongly prefer 1–2; allow 3–4 *with a justification note*;
hard-exclude 5. New rules:

- **Written-medium rule (any tier):** a written source yields quotes only as verbatim
  sentences actually written by the candidate — never a curator-summarized bullet list.
  (Replaces the tier-4-scoped statement; also covers tier-2 questionnaires.)
- **Only-surface rule (per candidate):** a tier-3 sympathetic-host interview is never
  excluded when it is the candidate's only sourceable speech; the justification note says so.

## Classifier changes (`src/discovery/classify.py`)

- **"Source tiers:" block** rewritten to the ladder above, with judgment guidance:
  ideological/opinion podcasts, party-aligned shows, and candidate-friendly platforms →
  tier 3; established news organizations (network, local TV, nonpartisan nonprofit
  newsrooms) → tier 2; when the outlet's character cannot be determined from the visible
  signals, default to tier 2 (the tier is a guess field; triage corrects it). The
  `confidence` field keeps its existing relevance semantics — it is not overloaded.
- **`ALLOWED_KINDS` += `"questionnaire"`**; the emit list in the JSON contract gains it.
- **Text-route guidance:** Q&A-shaped page text — interviewer/panel back-and-forth or a
  per-candidate questionnaire with unedited answers — is the preferred `quote_source`;
  a questionnaire from independent press or a civic org is tier 2.
- **Eval:** re-run `scripts/eval_discovery_classifier.py` after the prompt change.
  Gate: recall / precision / Brier each within 0.05 absolute of the pre-change run
  (ship baseline: 0.84 / 1.00 / 0.117 on 24 examples — re-measure immediately before the
  change; the set accretes from triage harvests). Ride-along: fixtures gain
  `expected_tier` labels and the script reports **tier accuracy** (non-gating). Tier
  accuracy is the designated evidence for revisiting decision 3.

## Triage ordering + one-shot re-classify

- `gui/discovery.py` pending-queue ordering becomes
  `election_date asc nulls last, source_tier_guess asc nulls last, confidence desc nulls
  last, created_at desc`.
- `scripts/reclassify_pending.py` (one-shot, idempotent): re-runs the classifier over
  `status='pending'` rows with stored metadata (title/description/channel/duration —
  no captions re-fetch), updates `source_tier_guess`, `event_kind_guess`, `confidence`,
  `why`; logs a per-row old→new tier diff and a summary count. Runs once at deploy;
  harmless to re-run.

## Skills + runbook edits (on-the-record)

- **race-pipeline SKILL** (`needs_quotes` step): hierarchy line rewritten to the new
  ladder; only-surface bullet added.
- **audit-quotes CHECKS.md:** `source-summary` check rewords from "written / tier-4
  source" to written-medium at any tier; `source-tier-4` check text re-verified against
  the new rung contents ("prefer tier 1–2 spoken sources" still holds — tier 2 now
  includes questionnaire text, so the check's wording must not imply spoken-only).
- **Runbook** (`docs/runbooks/source-discovery.md`):
  - gap-filler prompt tier order line → "debates/forums/town halls, independent-press
    interviews & questionnaires, partisan interviews & prepared remarks,
    candidate-bylined written";
  - gap-filler prompt gains: hunt candidate questionnaires explicitly (Vote411, LWV
    chapters, WyoFile-style outlet questionnaire pages), and for minor-party/independent
    candidates search podcasts — the only-surface rule applies;
  - new **County source packs** section: LWV chapter YouTube channels first, hyperlocal
    news second, the chain/ToS registration gate applies (note the already-barred
    hyperlocals: Upslope Media's County17/Oil City/CapCity, County 10, Sweetwater Now);
    packs are built when a county race enters the pipeline queue or alarms — not
    speculatively.

## County column (ev-accounts)

`ALTER TABLE essentials.source_outlets ADD COLUMN county text NULL;` — nullable free text
("Monroe" with `state='IN'`), no backfill required. Migration numbering: fetch first, run
`check-migration-numbers.mjs`, then number. Discovery code treats it as pass-through
metadata; pack agents set it at registration.

## Consent letters (`docs/outreach/`)

Three drafts from one core letter; Chris signs as Empowered Vote (candrews@empowered.vote),
reviews, and sends — sending is a human action outside this spec's implementation.

- **The ask (narrow, chain-wide):** written consent for automated access, copying, storage,
  and processing of **election-related candidate-speech content** (debates, forums,
  interviews, candidate Q&A pages) using AI-assisted transcription and speaker-attribution
  tools, for **non-commercial voter education**, with attribution and deep links back to
  the chain's own pages/players, and **expressly excluding any use for AI model training**.
- **Gray Media** (primary): cites the TOU's own consent path ("without prior express
  written consent").
- **Hearst / Nexstar** (variants): their bars are AI-training-specific; the letter notes
  the pipeline does no training and requests consent to remove all doubt.
- **What we offer:** attribution + traffic via deep links; no republication of full video;
  scope limited to candidate speech; empowered.vote is genuinely non-commercial (no
  donations, no paid tier).
- **Interim posture unchanged:** the registration gate keeps all three chains barred until
  written consent exists (recorded on the outlet row on grant); Gray pages remain usable as
  citations for human-curated quotes; Gray-carried debates are often co-productions
  findable on partner/LWV channels.

## Success criteria

1. Eval re-run passes the regression gate; tier accuracy is reported with `expected_tier`
   labels on all fixtures.
2. The Discovery tab orders pending items by the new key, and the re-classify one-shot has
   run (old→new tier diff logged; pending queue internally consistent).
3. essentials §5, race-pipeline SKILL, CHECKS.md, and the runbook all state the same
   ladder — no document still says town halls are tier 3 or implies partisan podcasts are
   tier 2.
4. `source_outlets.county` is live in prod; the county-pack runbook section exists.
5. All three consent letters are drafted in `docs/outreach/` and reviewed by Chris.

## Non-goals / deferred

- **Outlet-level `questioner_independence` column** — deferred until tier-accuracy evidence
  shows prompt-only judgment failing on repeat outlets (decision 3).
- **GUI only-surface badge** (per-candidate zero-approved-source chip) — written rule only.
- **`scope` enum / city packs** — `state` + `county` covers the observed need.
- **Any tier-based exclusion at discovery time** — `relevant` stays original-vs-clip only;
  tiers order, never gate, the queue.
- **`src/event_kinds.py`** untouched.
- **Sending or negotiating the consent letters** — Chris's action; the repo holds drafts.

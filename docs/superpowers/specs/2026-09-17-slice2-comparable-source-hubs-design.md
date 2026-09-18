# Slice 2 — comparable-source hubs (polled), not an agentic hunt — design

Date: 2026-09-17
Status: Draft (pending spec review)
Area: Discovery engine (`src/discovery/`, `scripts/poll_discovery.py`) + a new hub registry in the ev-accounts `essentials` schema. Extends [[discovery-review-reorg-design]] (Slice 1, shipped/live) and is driven by [[source-priority-comparable-questions]].

## Origin: what the Slice-2 spike proved

Slice 2 began as "chase the primary source" — an agentic hunt triggered by a clip of a formal event. A spike (8-race hand-labeled eval set + a bakeoff harness comparing a Tavily search-agent loop, OpenRouter web search, and a hub-directed agent) produced three findings that redirected the design:

1. **Agentic hunting is low-yield here.** All engine variants found only ~1/5–1/3 of the comparable sources for a race (recall 0.10–0.34), with high run-to-run variance — the search-API vs OpenRouter-web choice was within the noise (both ≈0.21 recall on the definitive run).
2. **Naive hub-directing is worse.** Pointing the agent straight at hubs with `site:` searches surfaced *prior-cycle* pages: recall 0.10, precision 0.29, 5 stale finds across 8 races. Reaching the hub is not the problem; **verifying the current cycle is.**
3. **Sources live in a small, predictable set of hubs.** The eval showed comparable common-question sources concentrate in: Ballotpedia (Candidate Connection), VOTE411/LWV, state clean-elections / debate commissions, public media (LAist/local NPR-PBS, Community Impact), government voter pamphlets, and local newspaper voter guides. Richness tracks whether such a hub covered the race — not the office's size.

Conclusion: replace the agentic per-clip hunt with **systematic polling of a maintained hub registry**, and keep the LLM only for the narrow, verifiable step it is actually good at — confirming a fetched page is the current cycle and carries the candidates' own words. The eval set + bakeoff harness are retained as a **recall regression test**.

## Goal

For every tracked race, systematically discover the **comparable common-question sources** — where candidates answer the same questions in their own words — by polling a maintained registry of source hubs, and rank/surface them per Chris's value model. Feed the results into the existing Slice-1 review queue.

Non-goals / explicitly dropped:
- The agentic "chase the primary source" hunt (spike: low-yield, stale-prone).
- The narrow lane-2 clip-chase (comparable events are mostly already found whole as originals; clips remain weak leads handled by Slice 1).
- Scraping content whose ToS forbids it (VOTE411/LWV without permission, etc.) — pointer-only where required (see ToS).

## Part 1 — the hub registry + per-race polling

### The hub registry (a maintained, updatable table)
A new table, **`essentials.source_hubs`** (decided 2026-09-17 over extending `source_outlets`: hubs are *per-race-resolvable source types* with a scope + poll method — distinct from `source_outlets`'s "a global feed I poll"). Feed-type hubs may ALSO be registered as `source_outlets` to reuse Slice-1 polling. Fields:
- `name`, `scope` (`global` | `state:<XX>` | `local_type`), `kind` (`debate|forum|questionnaire|guide|pamphlet`), `poll_method` (`feed|scoped_search`), `domain` / `query_template`, `tos_bucket` (`ballotpedia|vote411-lwv|govt|public-media|newspaper-or-tv-chain|other`), `active`, `added_via` (`seed|flywheel|manual`), `notes`.
- Updatable by hand AND via a **flywheel**: when polling (or review) confirms a good new hub for a jurisdiction, it can be added — exactly like the Slice-1 outlet-trust flywheel. This is the "can we update the hubs?" answer: yes, forever.

Seed set (from the eval): GLOBAL — Ballotpedia, VOTE411, Vote Smart. PER-STATE — e.g. AZ Citizens Clean Elections + Arizona PBS/KJZZ/AZPM; OR Oregon Voters' Pamphlet + OPB; CA LAist + Voter's Edge + LWV; TX Community Impact + Austin Monitor + KUT; UT Utah Debate Commission + KUER. LOCAL_TYPES — local LWV chapter forum, local newspaper voter guide, local public radio/TV, chamber forum, government sample ballot / voter pamphlet.

### Per-race resolution
For a tracked race, applicable hubs = `global` ∪ `state:<elections.state>` ∪ `local_type` (instantiated with the race's locality via the Slice-1 geography derivation).

### Polling, by `poll_method`
- **`scoped_search`** (Ballotpedia, VOTE411, and local-type hubs) — decided 2026-09-17, over rigid URL templates (Ballotpedia URLs vary by office/state/year; VOTE411 uses opaque internal IDs): resolve via ONE bounded search restricted to the hub's `domain` or its `query_template` (e.g. `site:ballotpedia.org <race/candidates>`, or `"<locality>" League of Women Voters candidate forum <year>`), take the best hub-domain result, fetch it, and **mandatorily verify current-cycle + fill** (the spike's central lesson) before accepting. Deterministic — one search per hub per race, NOT an agentic loop. For Ballotpedia's Candidate Connection questionnaire, resolve **per candidate** (`ballotpedia.org/<Candidate Name>` is far more predictable than the race-page URL). **VOTE411 stays pointer-only** — its opaque IDs, 403-blocking, and LWV-permission ToS make automated resolution not worth it now.
- **`feed`** (public media, state clean-elections YouTube): register as a `source_outlets` feed and reuse Slice-1 polling; the Part-2 classifier + value model tag and rank the comparable content as it flows in.

### The kept LLM job: current-cycle + fill verification
Before accepting any hub result, confirm (a) it is the CURRENT election cycle for THIS race's candidates, and (b) it carries the candidates' own words (not an empty questionnaire form, not background reporting). This is the spike's central lesson — it is what separates a real find from a stale/partial trap.

### Disposition
Verified hub finds are inserted into `essentials.discovered_sources` as `pending`, tagged with `event_kind_guess` (incl. `questionnaire`) and a **high tier** (comparable common-question sources outrank generic clips), routed to the Slice-1 review page for the human. ToS gating (below) rides Slice-1's `ingest_barred` + pointer-only handling.

## Part 2 — teach the discovery cron the value model

Update the classifier/engine (`src/discovery/classify.py`, `lanes.py`, tier logic) with [[source-priority-comparable-questions]]:
- **Recognize `questionnaire` as a first-class `event_kind`** (add to `src/event_kinds.py::EVENT_KINDS`; today the classifier emits it but it isn't a valid kind and isn't a lane).
- **Rank comparable multi-candidate common-question sources at the top** — debates/town-halls/forums with multiple candidates, then interviews, then written questionnaires (which may outrank spoken). Always the candidate's own words.
- **Reject/deprioritize stale prior-cycle content** — a current-cycle check in the classifier (the spike's stale trap, generalized).
- Reflect this in `content_lane` / tier so the Slice-1 auto-approve + review treat comparable sources appropriately (a full comparable event is a lane-1 ingest candidate; a questionnaire is a high-value quote source).

## ToS (reuse Slice-1 + prior work)
- **VOTE411 / LWV**: written permission required; pointer-only interim lane already built ([[vote411-source-feasibility]], PR #223). Poll for existence/pointer, do not scrape without permission.
- **Ballotpedia**: its own terms — quote/pointer per their license; verify before ingest.
- **Newspaper / TV voter guides**: chain ToS → Slice-1 `ingest_barred` (quote/pointer may be OK even when hosting a transcript is not).
- **Government pamphlets, public media, clean-elections**: generally clean; public-domain-ish. Prefer these.

## Regression eval (retained asset)
The 8-race hand-labeled eval set + the bakeoff harness (currently in a scratchpad spike dir) are promoted to a repo eval (`scripts/` + a fixture), measuring **recall of comparable sources per race** as hubs are added and the classifier is tuned. Because the metric is noisy, average multiple runs; a change must beat the noise band (cf. the discovery-classifier eval lesson).

## Phasing
1. Hub registry table + seed + per-race resolution + the highest-yield hubs (Ballotpedia via `scoped_search` incl. per-candidate Candidate Connection; state clean-elections/public-media registered as `feed` outlets), with mandatory current-cycle+fill verification. Disposition into the Slice-1 queue. **← the B plan (2B) covers this phase.**
2. Part-2 classifier/value-model updates (`questionnaire` kind, ranking, stale rejection).
3. Local-type `search` hubs (bounded, verified) + the flywheel to grow the registry.
4. Promote the eval + harness into the repo as the recall regression test.

## Open questions
- How aggressively to run the `scoped_search` hubs (esp. local-type) given their lower yield and per-search cost — cadence + a per-race hub budget.
- Whether the lane-1 "ingest glance" from the original Slice-2 sketch is still worth a small task, or folds into the reranked review.

## Decided 2026-09-17 (were open questions)
- **Table:** new `essentials.source_hubs` (not an extension of `source_outlets`) — see above.
- **URL derivation:** `scoped_search` (one bounded domain/query-scoped search + mandatory current-cycle+fill verification), per-candidate for Ballotpedia Candidate Connection; VOTE411 pointer-only; feed hubs registered as `source_outlets` — see the Polling section.

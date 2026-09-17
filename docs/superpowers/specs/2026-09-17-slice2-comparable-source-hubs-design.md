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
A new table (proposed `essentials.source_hubs`; may instead extend `source_outlets` with a `hub` kind — decide in planning). Fields:
- `name`, `scope` (`global` | `state:<XX>` | `local_type`), `kind` (`debate|forum|questionnaire|guide|pamphlet`), `poll_method` (`url_template|feed|search`), `url_template` / `domain` / `search_hint`, `tos_bucket` (`ballotpedia|vote411-lwv|govt|public-media|newspaper-or-tv-chain|other`), `active`, `added_via` (`seed|flywheel|manual`), `notes`.
- Updatable by hand AND via a **flywheel**: when polling (or review) confirms a good new hub for a jurisdiction, it can be added — exactly like the Slice-1 outlet-trust flywheel. This is the "can we update the hubs?" answer: yes, forever.

Seed set (from the eval): GLOBAL — Ballotpedia, VOTE411, Vote Smart. PER-STATE — e.g. AZ Citizens Clean Elections + Arizona PBS/KJZZ/AZPM; OR Oregon Voters' Pamphlet + OPB; CA LAist + Voter's Edge + LWV; TX Community Impact + Austin Monitor + KUT; UT Utah Debate Commission + KUER. LOCAL_TYPES — local LWV chapter forum, local newspaper voter guide, local public radio/TV, chamber forum, government sample ballot / voter pamphlet.

### Per-race resolution
For a tracked race, applicable hubs = `global` ∪ `state:<elections.state>` ∪ `local_type` (instantiated with the race's locality via the Slice-1 geography derivation).

### Polling, by `poll_method`
- **`url_template`** (Ballotpedia, VOTE411): derive the race's hub URL from the race/jurisdiction, fetch it, verify current-cycle + fill, extract the comparable source (or the candidate answers). VOTE411 403-blocks fetchers → pointer-only / rely on permitted access (see ToS).
- **`feed`** (public media, clean-elections YouTube): reuse the existing Slice-1 feed-polling machinery (`source_outlets` youtube/web_rss), filtered to the race's candidates. Many hubs can simply be registered as outlets.
- **`search`** (local types with no fixed URL): a *bounded, current-cycle-verified* targeted search for the locality — the one place an agent is used, and only after the deterministic hubs.

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
1. Hub registry table + seed + per-race resolution + the highest-yield deterministic hubs (Ballotpedia `url_template`, government voter pamphlets, state clean-elections/public-media as feeds), with current-cycle+fill verification. Disposition into the Slice-1 queue.
2. Part-2 classifier/value-model updates (`questionnaire` kind, ranking, stale rejection).
3. Local-type `search` hubs (bounded, verified) + the flywheel to grow the registry.
4. Promote the eval + harness into the repo as the recall regression test.

## Open questions
- New `essentials.source_hubs` table vs. extending `source_outlets` with a `hub` kind + `poll_method`/`url_template` columns.
- Exact URL-template derivation for Ballotpedia / VOTE411 per race (needs the race → canonical hub-URL mapping; overlaps the Slice-1 geography work).
- How aggressively to run the local-type `search` hubs given their lower yield and cost.
- Whether the lane-1 "ingest glance" from the original Slice-2 sketch is still worth a small task, or folds into the reranked review.

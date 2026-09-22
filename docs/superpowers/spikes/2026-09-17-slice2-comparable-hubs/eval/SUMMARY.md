# Slice 2 hunt — eval set (ground truth), v1

8 races, hand-researched 2026-09-17. Each race has a detailed file in this dir.
Purpose: the labeled "what comparable common-question sources exist, and where"
ground truth the bakeoff (search-API agent loop vs OpenRouter web search) scores
recall / precision / neutrality / ToS against.

## Coverage matrix

| Race | Level | Debate/forum (T1) | Interview (T2) | Questionnaire/guide (T3) | Richness |
|---|---|---|---|---|---|
| AZ Governor | statewide | partial — CCEC debate Oct 6 but **Hobbs (frontrunner) declined** → 3 of 4; Police forum 2 of 4, narrow | one-sided (AZPM Hobbs; Biggs declined) | Ballotpedia **1 of 4** filled; VOTE411 **403**; CCEC guide **STALE 2022** | moderate/fragmented |
| AZ US House 6 | federal | yes — Clean Elections Sep 10, **excludes Peters** | none found | Ballotpedia **0 of 3**; VOTE411 blocked | thin–moderate |
| AZ Mine Inspector | statewide (obscure) | **yes** — both candidates, aired Aug 31 | none | Ballotpedia none; CCEC guide stale | thin but has the debate |
| LA Mayor | big-city local | yes — LAist/KPCC | **yes** — matched AirTalk interviews (both) | LAist own questionnaire (both) + Ballotpedia **both filled**; no VOTE411 | **richest** |
| Bend Mayor (OR) | small-city local | 3 neutral forums/debates | — | no Ballotpedia; no filled questionnaire; **govt Voters' Pamphlet filled** | moderate |
| Austin City Council D5 | mid-city local | yes — govt/LWV candidate panel | none clean (stale 2022 traps) | **Community Impact 4-Q, all 4 filled**; Ballotpedia + VOTE411 unfilled | moderate |
| Utah State Board of Ed D14 | state board | **none** | none | Ballotpedia only, **1 of 4** filled | **thinnest** |
| Princeton council (TX) | tiny town | **yes** — LWV forum, full video, both finalists | none | local **newspaper Q&A**, all candidates, same questions | **rich (surprise)** |

## Recurring neutral source hubs (where comparable sources actually live)
- **Public media** — LAist/KPCC, local NPR/PBS, Community Impact: highest yield; often supplies debate + interview + questionnaire in one place.
- **LWV chapters** — forums (video) + VOTE411 (but VOTE411 pages 403-block scrapers).
- **State clean-elections / debate commissions** (AZ CCEC) — debates + written guides.
- **Government election guides** — Oregon Voters' Pamphlet (filled, neutral, clean ToS); underrated.
- **Ballotpedia Candidate Connection** — page usually exists; **fill rate is low** (often 0–1 of N).
- **Local newspapers** — candidate Q&As (neutral when nonpartisan; chain ToS applies).

## Traps the hunt (and the scoring) must handle
1. **Stale prior-cycle content** — AZ CCEC + several Austin hits still serve 2022 content at right-looking URLs. Must verify the *current* cycle/candidates, not just find the page.
2. **Partial fill** — Ballotpedia/questionnaire pages exist but only some (often zero) candidates answered. "Page exists" ≠ "candidate's own words."
3. **Access-blocked** — VOTE411 returns 403 to fetchers. Ties to the LWV-permission / pointer-only lane already built (PR #223).
4. **Fragmented comparability** — frequently no single source has all candidates; approximating comparability means combining several.
5. **Advocacy vs neutral** — partisan "voter guides" (e.g. Arizona List PAC) are not comparable common-question sources.

## Richness ≠ office size
The tiny TX town (Princeton) is richer than a statewide UT school-board race. Richness tracks whether a local **LWV chapter / newspaper / public-media** outlet covered the race — not the office's level. Implication: the hunt must *always* probe local LWV, local newspaper, public media, and government guides, even for small races.

## Roster errors surfaced (→ Task B)
- **Bend Mayor:** Ron Boozell never qualified; the real race is Kebler vs. **Strome** (not in our roster).
- **AZ Governor:** the real general field (Hobbs/Biggs/Hourihan(No Labels)/Lombardo(Green)) differs from our roster (which listed Schweikert).
These reinforce that Task B (general-election roster refresh) is real and needed; the hunt should verify against the actual filed field.

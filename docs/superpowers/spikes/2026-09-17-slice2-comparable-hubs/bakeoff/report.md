# Source-Discovery Bakeoff Report

Generated: 2026-09-17 19:07 UTC
Mode: **LIVE RUN (real API calls were made)**

This is a SPIKE benchmark, not production. It compares 3 engine(s) for the source-discovery hunt (finding comparable common-question sources for a race) against an 8-race hand-labeled ground truth.

## Aggregate summary

| Engine | Races scored | Races unavailable | Recall | Precision | Stale | Advocacy | Hallucinated |
|---|---|---|---|---|---|---|---|
| Engine A (Tavily search-agent loop) | 8 | 0 | 0.21 | 0.60 | 2 | 0 | 0 |
| Engine B (OpenRouter web plugin) | 8 | 0 | 0.21 | 0.79 | 0 | 2 | 0 |
| Engine C (Tavily hub-directed loop) | 8 | 0 | 0.10 | 0.29 | 5 | 0 | 0 |

## Per-race detail

| Race | Engine | Status | Known | Found | Matched | Recall | Precision | Stale | Advocacy | Hallucinated |
|---|---|---|---|---|---|---|---|---|---|---|
| az-governor | Engine A (Tavily search-agent loop) | ok | 5 | 1 | 1 | 0.20 | 1.00 | 0 | 0 | 0 |
| az-governor | Engine B (OpenRouter web plugin) | ok | 5 | 0 | 0 | 0.00 | 0.00 | 0 | 0 | 0 |
| az-governor | Engine C (Tavily hub-directed loop) | ok | 5 | 1 | 0 | 0.00 | 0.00 | 1 | 0 | 0 |
| az-house-06 | Engine A (Tavily search-agent loop) | ok | 4 | 2 | 1 | 0.25 | 0.50 | 0 | 0 | 0 |
| az-house-06 | Engine B (OpenRouter web plugin) | ok | 4 | 2 | 1 | 0.25 | 0.50 | 0 | 1 | 0 |
| az-house-06 | Engine C (Tavily hub-directed loop) | ok | 4 | 2 | 1 | 0.25 | 0.50 | 0 | 0 | 0 |
| az-mine-inspector | Engine A (Tavily search-agent loop) | ok | 1 | 3 | 1 | 1.00 | 0.67 | 0 | 0 | 0 |
| az-mine-inspector | Engine B (OpenRouter web plugin) | ok | 1 | 4 | 1 | 1.00 | 1.00 | 0 | 0 | 0 |
| az-mine-inspector | Engine C (Tavily hub-directed loop) | ok | 1 | 1 | 0 | 0.00 | 0.00 | 0 | 0 | 0 |
| la-mayor | Engine A (Tavily search-agent loop) | ok | 5 | 3 | 1 | 0.20 | 0.67 | 0 | 0 | 0 |
| la-mayor | Engine B (OpenRouter web plugin) | ok | 5 | 5 | 1 | 0.20 | 0.80 | 0 | 0 | 0 |
| la-mayor | Engine C (Tavily hub-directed loop) | ok | 5 | 3 | 0 | 0.00 | 0.00 | 3 | 0 | 0 |
| bend-mayor-or | Engine A (Tavily search-agent loop) | ok | 4 | 0 | 0 | 0.00 | 0.00 | 0 | 0 | 0 |
| bend-mayor-or | Engine B (OpenRouter web plugin) | ok | 4 | 0 | 0 | 0.00 | 0.00 | 0 | 0 | 0 |
| bend-mayor-or | Engine C (Tavily hub-directed loop) | ok | 4 | 2 | 0 | 0.00 | 0.00 | 1 | 0 | 0 |
| austin-cc-d5 | Engine A (Tavily search-agent loop) | ok | 3 | 1 | 1 | 0.33 | 1.00 | 0 | 0 | 0 |
| austin-cc-d5 | Engine B (OpenRouter web plugin) | ok | 3 | 2 | 1 | 0.33 | 0.50 | 0 | 1 | 0 |
| austin-cc-d5 | Engine C (Tavily hub-directed loop) | ok | 3 | 1 | 1 | 0.33 | 1.00 | 0 | 0 | 0 |
| ut-sboe-14 | Engine A (Tavily search-agent loop) | ok | 3 | 1 | 0 | 0.00 | 0.00 | 1 | 0 | 0 |
| ut-sboe-14 | Engine B (OpenRouter web plugin) | ok | 3 | 3 | 1 | 0.33 | 0.67 | 0 | 0 | 0 |
| ut-sboe-14 | Engine C (Tavily hub-directed loop) | ok | 3 | 2 | 0 | 0.00 | 0.50 | 0 | 0 | 0 |
| princeton-council-tx | Engine A (Tavily search-agent loop) | ok | 4 | 4 | 1 | 0.25 | 0.50 | 1 | 0 | 0 |
| princeton-council-tx | Engine B (OpenRouter web plugin) | ok | 4 | 3 | 1 | 0.25 | 1.00 | 0 | 0 | 0 |
| princeton-council-tx | Engine C (Tavily hub-directed loop) | ok | 4 | 2 | 1 | 0.25 | 0.50 | 0 | 0 | 0 |

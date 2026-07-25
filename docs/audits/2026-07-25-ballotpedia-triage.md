# Ballotpedia quote triage — 2026-07-25

Scope: all **266** rows in `essentials.quotes` (ev-accounts) whose `source_url` points at
ballotpedia.org, across **105** politicians. Read-only classification, no writes. Companion data:
`2026-07-25-ballotpedia-triage.csv` (one row per quote, with its re-attribution target).

Context: this triage exists because ballotpedia.org was deliberately **excluded** from the same-day
aggregator purge that deleted 1,173 ontheissues.org and 273 en.wikipedia.org rows. Ballotpedia is
not equivalent to those — it footnotes originals and it publishes candidate-authored survey answers
itself. The question this answers is: of the 266, which can be re-attributed, which are legitimately
Ballotpedia-original, and which are unverifiable?

## Result

| verdict | total | live | 2026 cycle | stale cycle | has target URL |
|---|---:|---:|---:|---:|---:|
| `SURVEY_ORIGINAL` | 157 | 129 | 123 | 34 | 135 |
| `CAMPAIGN_SITE` | 77 | 70 | 51 | 26 | 71 |
| `UNKNOWN_BLOCK` | 10 | 10 | 0 | 10 | 6 |
| `NOT_ON_PAGE` | 22 | 17 | 0 | 0 | 0 |
| **total** | **266** | **226** | | **70** | |

- **`SURVEY_ORIGINAL` (157)** — the quote sits inside a Candidate Connection survey block. The
  candidate wrote it *for* Ballotpedia, so Ballotpedia **is** the original publisher and the
  citation is already correct. No re-attribution needed. Caveat: Ballotpedia states it "reserves the
  right to edit Candidate Connection survey responses," marking its edits with `[brackets]`.
- **`CAMPAIGN_SITE` (77)** — Ballotpedia reproduces the candidate's own campaign-site text verbatim,
  under an attribution line like `— Antony Barran's campaign website (November 12, 2025)`. These are
  the re-attribution candidates. **24** carry a footnote link straight to the original; for the other
  **47** Ballotpedia names the source but doesn't link it, so the target comes from the page's
  External links section instead.
- **`UNKNOWN_BLOCK` (10)** — found on the page but under no recognisable provenance marker, and all
  are pre-2026 (2012–2024). Needs a human eyeball; small enough to do by hand.
- **`NOT_ON_PAGE` (22, 17 live)** — the text is **not on the cited page today**. Split into
  **20 `ABSENT`** (no contiguous match at all) and **2 `EDITED_TEXT`** (long prefix then divergence —
  e.g. Hugh McTavish's fossil-fuels quote matches 84% then reads "allowed it *to be built*" where the
  page reads "allowed it *Tim Walz pledged*"). Either the curator paraphrased or the page has since
  changed. These are unverifiable as cited and are the clearest drop candidates.

Ten candidates are *entirely* unverifiable: Amy Roma (2), Lindsey Anderson (2), Bill Conlin,
Donavan McKinney, Hugh McTavish, Karen Ruth Bass, Kurtis Engle, Patrick Schmidt, Tom Weiler,
Victoria Broderick.

## Cycle staleness is the bigger issue

**70 of 266 quotes come from a pre-2026 cycle**, because Ballotpedia stacks every cycle on one page
(2026 / 2024 / 2022 / 2012) and the curator didn't always land on the current one:

- 2024: 50 · 2022: 10 · 2017: 4 · 2020, 2025: 2 each · 2012, 2018, 2019: 1 each

This cuts across the verdicts — **34 `SURVEY_ORIGINAL` rows (31 live) answer a survey from a prior
cycle** (20 from 2024, 9 from 2022). Ballotpedia is a legitimate publisher for those, so provenance
is fine; the problem is that a 2022 questionnaire is being presented as a 2026 candidate's position.
That is a curation call, not a sourcing one.

## Playability cost of each cleanup option

Baseline today: **149 playable races, 587 playable topics**.

| scenario | races | topics | Δ races | quotes dropped |
|---|---:|---:|---:|---:|
| baseline | 149 | 587 | 0 | 0 |
| drop `NOT_ON_PAGE` | 149 | 587 | 0 | 22 |
| drop `NOT_ON_PAGE` + `UNKNOWN_BLOCK` | 149 | 586 | 0 | 32 |
| drop all stale-cycle (<2026) | 148 | 570 | −1 | 70 |
| drop unverifiable + stale | 148 | 570 | −1 | 92 |
| purge every ballotpedia row | 139 | 532 | −10 | 266 |

**Dropping the unverifiable rows is free** — 0 races and 0 topics (adding `UNKNOWN_BLOCK` is what
costs the 1 topic). Confirmed by execution: the 22 were deleted on 2026-07-25 and playability stayed
at exactly 149 races / 587 topics. Even dropping every stale-cycle quote costs 1 race and 17 topics. A blanket purge would cost 10 races and 55 topics, which is the
case for not treating Ballotpedia like OnTheIssues.

## Where the 266 ended up

All of it reconciles: **266** → dropped 22 unverifiable → **244** → re-attributed 36 to campaign
sites → **208** → dropped 41 unverifiable → **167 rows still citing ballotpedia.org**, which is
exactly the 157 `SURVEY_ORIGINAL` (correctly cited — Ballotpedia is the publisher) plus the 10
`UNKNOWN_BLOCK` awaiting a hand-check. 63 rows deleted, 36 re-attributed, 0 races lost, 9 topics lost.

## Recommended sequence

1. **Drop the 22 `NOT_ON_PAGE` rows.** Unverifiable against the cited source, and free.
2. **Decide the 34 stale-cycle survey rows** — a pre-2026 questionnaire standing in for a 2026
   position. Recommend dropping; provenance is sound but currency isn't.
3. ~~**Re-attribute the 77 `CAMPAIGN_SITE` rows**~~ **Swept 2026-07-25 — 36 of 77 re-attributed
   and committed** (31 live), across 14 campaign hosts. **18 of the 36 verified on a deeper page than
   the one cited**, so the descend step was load-bearing, not defensive. Revert:
   `ev-db-backups/2026-07-25-campaign-site-reattribution-revert.sql`.
   The other **41 (39 live) could not be verified at any original** and still cite ballotpedia.org:
   - **28 `TEXT_NOT_FOUND`** — site reachable with substantial text, but the quote isn't on it
     (Antony Barran, Brinker Harding, Gerald Malloy, Luke Bronin, Robin Ficker, Van Hilleary, …).
   - **7 dead campaign sites**, confirmed by browser after the scraper flagged them: Jonny Larsen's
     6 (domain moved jonnylarsen.com → jonnyutah4congress.com and the site was rebuilt — every page
     now 1–3k chars, the platform text is gone) and John Hancock's 1 (hancock4liberty.com now serves
     a generic GoodParty.org placeholder).
   - **6 `NO_TARGET`** — David R. Ambrose II's 2024-cycle rows, which Ballotpedia neither footnotes
     nor pairs with an external campaign link.

   **All 41 were dropped 2026-07-25** — same test failed as the 22 before them. Cost was exactly as
   predicted: 0 races, 9 topics (587 → 578). Zero `compass_verdicts` referenced them. Backup:
   `ev-db-backups/2026-07-25-campaign-site-unverifiable-quotes.restore.sql` (+ `.csv`).
4. **Leave the 123 current-cycle `SURVEY_ORIGINAL` rows alone.** Correctly cited as-is.
5. **Hand-check the 10 `UNKNOWN_BLOCK` rows.**
6. ~~Separately: 3 rows carry `source_name = 'www.ricefordelegate.com'` pointing at Ballotpedia
   pages for *other* people.~~ **Done 2026-07-25 — and it was 16 rows, not 3.** Filtering for
   Ballotpedia URLs had shown only the 3 that surfaced in this triage; the real bug class was
   `source_name` stuck at `www.ricefordelegate.com` across **12 different hosts** (drahmadhassan.com,
   harris.house.gov, cbs2iowa.com, oilcity.news, smarter.vote, …). All 16 were reset to their URL's
   host per house convention; Andrew Rice's own 2 rows were correctly left alone. Revert script:
   `ev-db-backups/2026-07-25-ricefordelegate-source-name-revert.sql`. One cosmetic mismatch remains
   corpus-wide: Luke E. Torian's abortion quote says `facebook.com` where the host is
   `www.facebook.com`.

Unresolved and out of scope here: many of these are **platform bullets** rather than statements made
in the wild. Re-attribution fixes where a quote came from, not whether a manifesto bullet should be
ranked as a candidate's voice.

## Method notes

`scripts` used for this run are in the session scratchpad (`triage_ballotpedia.py`,
`summarise.py`); worth promoting into the `audit-quotes` skill if this becomes recurring. Four
things that will break a naive re-run:

- **`WebFetch` returns blank on ballotpedia.org**; plain `curl`/`requests` with a browser UA works.
- **Ballotpedia answers 200-with-empty-body under load.** The first run silently cached 63 empty
  pages and produced a plausible-looking but wrong classification. Validate that a response contains
  `mw-content-text` before caching, and back off.
- **Match punctuation-insensitively.** `get_text` emits `"Medicare , Medicaid"` around inline tags,
  curators type `--` where the page has an em-dash, and Ballotpedia brackets its own edits. Comparing
  alphanumeric skeletons recovered 5 quotes that otherwise looked missing.
- **Scope footnote lookup structurally**, not by character distance. A window-based search attached a
  Breitbart article to an abortion quote. Walk from the matched block up to the enclosing
  quotebox `table`/`blockquote` and stop there.

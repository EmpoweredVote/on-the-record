# Nancy Mace / taxes — unsourceable quote trace

**Date:** 2026-09-12
**Quote id:** `c8be0517-26e2-4e0a-9b4c-0cf7a67d8c4a`
**Disposition:** retired (hard delete) by ev-accounts migration `1860_merge_jim_priest_retire_mace_unsourceable_quote.sql`

## The row

| field | value |
|---|---|
| politician | Nancy Mace (`096ba968-82d5-46ce-86ab-4b387973978d`, external_id `-45001`) |
| topic_key | `taxes` |
| quote_text | "Every government dollar would be better spent by taxpayers." |
| source_url | https://www.atr.org/legislators/nancy-mace/ |
| readrank_selected | **true** (live) |
| question_id | NULL |
| editor_note | empty |

## Why it was retired

**1. The sentence is not on the cited page.** The ATR legislator page for Nancy Mace was fetched on
2026-09-12. It carries only biographical fields — state, district, incumbent status, pledge date —
plus ATR navigation. It contains no quotation from Mace at all. What it actually records is that she
signed the Taxpayer Protection Pledge.

**2. The sentence is not attested anywhere else.** An exact-phrase web search returned no instance of
it. Her documented positions on this subject are real and specific — a Penny Plan to balance the
budget, and an income-tax elimination proposal covered by FITSNews in April 2026 — but none of that
wording matches.

**3. It has the shape of a rewritten pledge summary.** This is the same failure mode migration 1809
recorded for Becerra / deportation: an advocacy group's framing of a position, rebuilt into a
first-person-sounding sentence and attributed to the politician.

This is not a judgment that Mace disagrees with the sentiment. It is that nothing supports the
citation, and under QUOTE-CURATION-PRINCIPLES an advocacy scorecard is a pointer, not a source.

## Why deleting a LIVE quote was acceptable here

Migration 1809 hard-deleted six unsourceable quotes but aborted if any was `readrank_selected`, on
the grounds that a live quote needs human review. This one is live. It was deleted anyway because
the reason for that caution does not apply:

- **Nancy Mace holds no `race_candidates` edge.** Read & Rank reaches quotes only through
  `race_candidates.politician_id = quotes.politician_id`, so this quote was unreachable and could not
  appear on any live card. No voter-facing surface changes.
- **Zero references.** Checked and empty in all four tables that carry quote ids:
  `essentials.readrank_questions.origin_quote_id`, `inform.compass_verdicts.quote_id`,
  `public.source_verifications.quote_id`, `compass_deprecated.quote_verdicts.quote_id`.

## Related finding — do not attach Mace to SC-01

Mace is **not** a 2026 candidate in South Carolina's 1st congressional district. She entered the
South Carolina governor's race on 2025-08-04 and does not appear on the SC-01 Republican primary
ballot of 2026-06-09. The SC-01 roster in `essentials` is correct as it stands: four candidates,
none marked incumbent.

An earlier pass in this session proposed linking her to SC-01 to make this quote visible. That would
have recorded a false fact **and** promoted an unsupported quote into a live comparison. Both errors
were caught before any write.

There is no SC Governor race row in `essentials`, although the `SC 2026 Statewide General` election
and the SC `Governor` office row both exist. Creating that race is roster work, not repair. Note for
whoever does it: Pamela Evette already holds three selected quotes, Ralph Norman holds one draft, and
Josh Kimbrell has no politician row at all.

## Sources

- ATR legislator page (fetched 2026-09-12): https://www.atr.org/legislators/nancy-mace/
- SC Daily Gazette, 2025-08-04: https://scdailygazette.com/2025/08/04/rep-nancy-mace-officially-enters-sc-governors-race/
- Ballotpedia: https://ballotpedia.org/Nancy_Mace

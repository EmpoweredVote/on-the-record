# VOTE411 pointer-only interim lane — design

**Date:** 2026-09-12
**Status:** Approved design (interim). Awaiting implementation plan.
**Owner:** Chris Andrews
**Scope:** A documented sub-procedure inside the existing `race-pipeline` skill. No new
code services, no new DB schema, no automation against LWV hosts.

## 1. Background — why "pointer-only"

VOTE411 (League of Women Voters Education Fund) publishes candidate questionnaire
answers in the candidates' own words. That is a strong primary source and, in the
race-pipeline source hierarchy, "candidate questionnaires" already sit at tier 2. We
would like to use it directly.

We cannot — yet. A read of the VOTE411 terms (vote411.org/legal, 2026-09-12) found that
each of the following is barred **without prior written permission from the League**:

- a non-profit distributing VOTE411 information to a third party,
- "automated queries of any sort" to the site or services,
- reformatting/displaying the information, or mirroring it, and
- copying or retransmitting site text (copyright asserted on site content; governing
  law: District of Columbia).

So both a scraper **and** a manual copy-paste are barred. The VOTE411 ballot tool runs
on a clean, versioned REST API (`api.thevoterguide.org/v1`), and the technical build
would be easy — but the blocker is the license, not the technology. A permission /
partnership request to the League is in flight separately (contact: national VOTE411
partnerships). This lane is what we do **in the meantime**, needing no permission.

(This section is a plain read of the terms, not legal advice.)

## 2. Goal and non-goals

**Goal.** Let a race-pipeline session use VOTE411 as a *lead* — to find who is running,
where their own campaign materials live, and which issues they address — while every
published quote is sourced from the candidate's **own original** and reproduces nothing
from VOTE411.

**Non-goals.**
- No scraping or automated access to `vote411.org` or `*.thevoterguide.org`.
- No storing of VOTE411 answer text, verbatim or paraphrased, anywhere.
- No new database schema and no new services.
- Not a replacement for the license ask. This lane has a known gap (§6) that only a
  license closes.

## 3. Compliance spine (the load-bearing rules)

These are hard constraints. If any cannot be met for a candidate, produce no quote for
that candidate rather than bend them.

1. **Human, in-browser, personal/educational reading only.** A person opens the VOTE411
   guide in a normal browser. No agent, subagent, script, or scheduled job fetches
   `vote411.org` or `*.thevoterguide.org`. This explicitly overrides race-pipeline's
   "fan out one agent per race" pattern for the VOTE411 reading step — that step is not
   delegated to an automated fetch.
2. **Store nothing from VOTE411's answers.** Do not paste VOTE411 answer text into
   `notes`, `discovered_sources.why`, `editor_note`, batch files, or any scratch file.
   The only things recorded are facts (see §5).
3. **VOTE411 is never a `source_url`.** `vote411.org` and `thevoterguide.org` are
   invalid sources. The remedy is *pointer-only* (source from the candidate's own
   materials), **not** "re-attribute to an original" — because the VOTE411 answer text
   is frequently original to VOTE411 and has no other page to re-attribute to.
4. **Every quote is verified against its own-source URL.** `source_url` points at the
   candidate's own original (campaign site, press release, their own post, their own
   video). It must pass `audit-quotes --verify-sources` (the quote string is present on
   re-fetch), same bar as every other quote.
5. **Only-on-VOTE411 positions are dropped.** If a candidate states a position only on
   VOTE411 and nowhere sourceable, create no quote. Do not paraphrase the VOTE411 answer
   to fill the gap. Absence is the correct, honest outcome.

## 4. Where it plugs in

The lane is folded into the existing `race-pipeline` skill; it introduces no new
lifecycle state. It touches two existing transitions:

- **`needs_roster → needs_quotes`.** When building the roster, the human may open the
  race's VOTE411 guide to confirm the ballot line-up and to read each candidate's own
  campaign URL from the guide's "Website" field. That URL is written to
  `essentials.race_candidates.website_url` (a field the roster step already populates).
  Confirm the ballot line-up against the Secretary of State list, exactly as today —
  VOTE411 is corroboration, not authority.
- **`needs_quotes → quotes_staged`.** The human uses what VOTE411 showed (which topics a
  candidate addresses) as a hint for *where to look*, then sources quotes from that
  candidate's own site and other tier 1–4 originals, per
  `QUOTE-CURATION-PRINCIPLES §5`. Staging, insertion, verification, and live-selection
  are unchanged (`publish-quotes` → `audit-quotes --verify-sources` → human selects).

Optional: when a candidate's own site carries sourceable statements, file a
`essentials.discovered_sources` row (`route='quote_source'`, `discovered_via='human'`,
`why` = "candidate campaign site" — no VOTE411 text) so it enters the normal discovery
queue the pipeline already consumes.

## 5. Captured vs never captured

| Captured (facts / ideas — not protectable) | Never captured |
| --- | --- |
| Candidate name; that they are on the ballot in race X | VOTE411 answer text — verbatim **or** paraphrased |
| Candidate's own campaign URL (`race_candidates.website_url`) | Any `*.thevoterguide.org` API data |
| A neutral topic hint (which Compass topic they seem to address) | VOTE411 / thevoterguide as a `source_url` |
| Optional `discovered_sources` lead pointing at the own site | VOTE411 screenshots, PDFs ("Keys to the Candidates"), or exports |

Rationale: candidate names, ballot presence, and URLs are facts; a topic label is an
idea. None is protectable expression. The candidate's *answer wording* is the protected
material, and it never leaves the browser.

## 6. Value and known limit

**Value.** Highest for local and down-ballot races, where YouTube/RSS discovery is thin
and VOTE411 is often the only index of who is running and where their campaign site is
(motivating case: Henry County, IN prosecuting attorney). It sharpens the roster step and
points the sourcing step at the right own-site pages fast.

**Limit (accepted).** When a candidate's position exists only on VOTE411 and nowhere
sourceable, this lane yields no quote for that position. This is exactly the value the
license would add, and the reason the ask is worth making.

## 7. Deliverable (folded into race-pipeline)

Three concrete edits, all documentation/guard — no services:

1. **`race-pipeline` skill (`.claude/skills/race-pipeline/SKILL.md`).** Add a short
   "VOTE411 as a pointer (interim)" subsection under the sourcing guidance. It states
   the compliance spine (§3), the two touch-points (§4), and the captured/never-captured
   rule (§5). It explicitly says the VOTE411 read is a human browser step, not an agent
   fetch.
2. **`audit-quotes` guard (`.claude/skills/audit-quotes/CHECKS.md` + the check's source
   list).** Add `vote411.org` and `thevoterguide.org` to the `invalid-source` check, but
   document the remedy as *pointer-only / candidate's own materials* (distinct from the
   ontheissues/wikipedia "re-attribute" remedy). Severity high, decision-required, same
   as the existing aggregator case. Confirm the exact list/pattern location in the check
   script during implementation and update it there too, not only in the doc.
3. **Original-sources doctrine (`essentials/docs/QUOTE-CURATION-PRINCIPLES.md`, where the
   aggregator/original-source rule is stated for curators).** One line: VOTE411 is a
   pointer, never a cited source, until a written League license says otherwise.

## 8. Success criteria

- A race-pipeline session can work a local race using VOTE411 as a pointer, and every
  resulting quote cites the candidate's own original and passes `--verify-sources`.
- `audit-quotes` flags any quote whose `source_url` is `vote411.org` or
  `thevoterguide.org` as `invalid-source`, with the pointer-only remedy text.
- No VOTE411 answer text appears in the DB, notes, batch files, or the repo.
- No code path fetches `vote411.org` or `*.thevoterguide.org`.

## 9. Open questions

None blocking. When the License lands, this lane is superseded by the real ingest lane;
the guard in §7.2 stays (a licensed ingest still cites the candidate/League properly, and
the guard prevents accidental raw-VOTE411-URL citations).

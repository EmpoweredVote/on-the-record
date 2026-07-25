# iSideWith purge — 2026-07-25

All **29** rows in `essentials.quotes` citing `www.isidewith.com` (6 politicians, 27 live) were
hard-deleted. This is the most serious sourcing defect found in the 2026-07-25 corpus sweep: the
rows were not merely badly sourced, they carried **fabricated attribution**.

Backup: `ev-db-backups/2026-07-25-isidewith-quotes.restore.sql` (+ `.csv` with candidate names).
Cost: **0 races dark**, topics 578 → 572. Two races degraded — IN-07 (6 topics → 2) and NC Senate
(4 → 2). Zero `inform.compass_verdicts` rows referenced any of them.

## Why deleted rather than re-attributed

**1. They are quiz answer options, not utterances.** Every string is a canned multiple-choice option
from iSideWith's questionnaire — "No, this is a violation of free speech", "Yes, but I would rather
privatize all education". No candidate ever spoke these words; iSideWith wrote them as answer
choices. There is no original to re-attribute *to*, which is what separated the Ballotpedia case
(reproduced campaign-site text with a footnote) from this one.

**2. Five strings were attributed to two candidates each, verbatim.** Including "No, it is immoral to
deny health insurance to people with pre-existing conditions" (Al Lemmo, Carlos Gimenez **and**
James Sceniak) and "Pro-choice, I don't agree but the government has no right to ban it" (Sceniak,
Shannon Bray). Blind ranking depends on two candidates' cards being *different*; no two of these
happened to collide in the same race+topic, but that was luck, not design.

**3. Some belonged to a different entity entirely.** iSideWith's candidate pages are comparison
tables: each issue carries several rows with different owners. On James Sceniak's page the 397 stance
rows broke down as:

| row owner | rows |
|---|---:|
| `PARTY'S SUPPORT BASE` (aggregate of surveyed party-affiliated voters) | 239 |
| `JAMES SCENIAK` (the candidate) | 71 |
| **`CHATGPT`** (AI-generated position) | 87 |

Of our 11 Sceniak quotes: **7** came from candidate-labelled rows, **3** from
`PARTY'S SUPPORT BASE` rows, and **1** from a **`CHATGPT`** row. Confirmed in the DOM — the text sits
in a `span.stance_text` inside a `<tr>` whose label is the row owner. The ChatGPT-sourced string
("Yes, but I would rather privatize all education") was in our DB attributed to *two* real
candidates.

So the corpus was presenting crowd-poll aggregates and LLM output as named candidates' positions.
OnTheIssues at least paraphrased real statements; this invented them.

## Evidence limitation

Row labels were verified in detail for **James Sceniak's page only** (11 of the 29 rows). iSideWith
began serving a bot challenge ("Are you human?") on the next candidate page and the check was stopped
there rather than circumvented. For the remaining 18 rows the conclusion rests on the identical
answer-option format and the strings shared across candidates — strong, but not per-row verified.
This did not change the decision: even the candidate-labelled rows are canned option text rather
than anything a candidate said.

## Follow-on

**Done 2026-07-25.** Rather than adding `isidewith.com` to the existing `invalid-source` host list,
the audit-quotes check was **split into two classes**, because the remedies are opposite
(`.claude/skills/audit-quotes/CHECKS.md` §2.1):

- `invalid-source` — aggregator (ontheissues.org, wikipedia.org): an original exists, **re-attribute**.
- `unquotable-source` — quiz/questionnaire site (isidewith.com): canned options, third-party
  aggregates and AI-generated stances side by side under one candidate's name, so *no* row on such a
  page is quotable regardless of which row it came from. Nothing to descend to — **delete**.

`ballotpedia.org` stays in neither class and is not flagged; see
`2026-07-25-ballotpedia-triage.md`.

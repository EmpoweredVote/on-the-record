# Quote Audit — U.S. Senate Michigan (MI, 2026-08-04)

**11 findings** — 5 high, 3 medium, 3 low

## Summary by race
- **race 4e369a24-0749-4d47-967f-978f933d32da** — 11 findings (5 high, 3 med, 3 low)

## race 4e369a24-0749-4d47-967f-978f933d32da
- `high` · `decision-required` · **source-summary** (quote) — Haley M. Stevens / climate-change [b92088bd-28ff-4411-9e94-df206b3f5b15]
    - Source is an LCV congressional scorecard page (lcv.org), which typically presents scored votes/ratings rather than a sentence the candidate actually said or wrote. 'US can lead the way in promoting a clean energy future' reads like curator-summarized boilerplate rather than a verbatim quote tied to a specific statement.
    - fix: Verify against the actual lcv.org page whether this is a quoted sentence attributed to Stevens or a scorecard summary; if the latter, re-source to an interview/floor statement/press release in her own words.
- `high` · `guided` · **note-missing** (quote) — Haley M. Stevens / climate-change [b92088bd-28ff-4411-9e94-df206b3f5b15]
    - editor_note is empty.
    - fix: Write a 1-2 sentence note: why this quote + Compass-stance alignment + any edits.
- `high` · `guided` · **partisan-tell** (quote) — Mallory McMorrow / healthcare [cba9271f-8da6-4334-a3aa-541d56fd655c]
    - Blind text contains a partisan/side tell: 'Republicans'.
    - fix: Drop the partisan word on the blind card (or neutralize to '[the current administration]'); draft, confirm, then apply.
- `high` · `guided` · **deid-dishonest** (quote) — Mallory McMorrow / taxes [baf6e2a6-b950-45a2-9b9f-773dc82a8033]
    - deidentified_text rewrites the sentence in different words instead of honest marking: 'I held a hearing on legislation called' -> 'There is legislation called', 'Michigan' -> 'the state', 'the SOAR fund' -> 'that fund'. None of these are marked with ellipses or brackets; the whole clause was paraphrased.
    - fix: Re-derive the blind text from the canonical quote using '...' for removed spans and '[state]'/'[the fund]' for substituted words, preserving the original sentence structure rather than rewriting it.
- `high` · `guided` · **deid-dishonest** (quote) — Abdul El-Sayed / taxes [fbe97f45-9290-4af0-9e48-dfc7f3f51f89]
    - deidentified_text paraphrases rather than marks: 'I would like to see us tax' -> 'We should tax', '7, 8%, you know' -> '7 or 8%, and you know'. These are rewordings, not honest ellipsis/bracket marking of the canonical quote.
    - fix: Re-derive the blind text via marking (retain first-person phrasing or use '[We]' bracket substitution) rather than a free rewrite.
- `medium` · `decision-required` · **coverage-skew** (portfolio) —  /  [4e369a24-0749-4d47-967f-978f933d32da]
    - Haley M. Stevens is live on 1/3 race topics (climate-change only); Mallory McMorrow and Abdul El-Sayed are each live on 2/3 (healthcare, taxes), absent from climate-change coverage on the other side. Stevens has no live quote on healthcare or taxes.
    - fix: Signal to investigate, not a defect to correct on its own: check whether curation effort for Stevens covered healthcare and taxes with the same diligence as the other two candidates, or whether she is genuinely less on-record on those topics.
- `medium` · `decision-required` · **not-rankable** (topic) —  / climate-change [climate-change]
    - Only 1 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.
- `medium` · `decision-required` · **non-differentiating-goal** (quote) — Haley M. Stevens / climate-change [b92088bd-28ff-4411-9e94-df206b3f5b15]
    - 'The US can lead the way in promoting a clean energy future' is an agreeable aspiration nobody in the race would contest and names no mechanism (no timeline, program, or policy lever), unlike the specific phase-out/market/rejection mechanisms in the topic's other chairs.
    - fix: Prefer a Stevens quote that names an actual energy-policy mechanism (a bill, a specific investment, a regulatory stance) if one exists in her record.
- `low` · `guided` · **note-too-long** (quote) — Abdul El-Sayed / healthcare [c8270483-4a32-4769-8114-aa98c644fd65]
    - editor_note is longer than 2 sentences.
    - fix: Tighten to <=2 sentences unless heavy editing must be explained.
- `low` · `guided` · **note-too-long** (quote) — Mallory McMorrow / taxes [baf6e2a6-b950-45a2-9b9f-773dc82a8033]
    - editor_note is longer than 2 sentences.
    - fix: Tighten to <=2 sentences unless heavy editing must be explained.
- `low` · `guided` · **note-too-long** (quote) — Abdul El-Sayed / taxes [fbe97f45-9290-4af0-9e48-dfc7f3f51f89]
    - editor_note is longer than 2 sentences.
    - fix: Tighten to <=2 sentences unless heavy editing must be explained.

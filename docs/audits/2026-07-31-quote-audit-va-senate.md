# Quote Audit — U.S. Senate Virginia (2026-08-04)

**5 findings** — 3 high, 2 medium, 0 low

## Summary by race
- **race e39860fd-5fba-4480-83fe-0f9f27c9a9d7** — 5 findings (3 high, 2 med, 0 low)

## race e39860fd-5fba-4480-83fe-0f9f27c9a9d7
- `high` · `guided` · **note-missing** (quote) — Kim Farington / immigration [6e3daf03-6e27-4fc6-b4cb-1ef477a6c6f4]
    - editor_note is empty.
    - fix: Write a 1-2 sentence note: why this quote + Compass-stance alignment + any edits.
- `high` · `guided` · **deid-dishonest** (quote) — Kim Farington / immigration [6e3daf03-6e27-4fc6-b4cb-1ef477a6c6f4]
    - deidentified_text silently substitutes "residents" for "Virginians/Americans" instead of marking the substitution with [brackets]; the edit is honest in intent (low identity-leak risk, since the whole race is already Virginia-scoped) but violates the marking convention.
    - fix: Re-render as "...if there are not enough [residents] to fill those positions." to mark the substitution explicitly, or restore "Virginians/Americans" if the edit was unnecessary.
- `high` · `guided` · **note-missing** (quote) — Bert Mizusawa / taxes [88da8843-d038-4b36-b4de-6d4854eaa67e]
    - editor_note is empty.
    - fix: Write a 1-2 sentence note: why this quote + Compass-stance alignment + any edits.
- `medium` · `decision-required` · **not-rankable** (topic) —  / immigration [immigration]
    - Only 1 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.
- `medium` · `decision-required` · **not-rankable** (topic) —  / taxes [taxes]
    - Only 1 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.


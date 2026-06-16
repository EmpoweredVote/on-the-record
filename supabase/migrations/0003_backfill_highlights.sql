-- Rename key_decisions → highlights in all existing summary JSONB rows.
-- Run this BEFORE deploying the web changes that expect the highlights field.
UPDATE meetings.meetings
SET summary = jsonb_set(
  summary - 'key_decisions',
  '{highlights}',
  summary->'key_decisions'
)
WHERE summary ? 'key_decisions'
  AND NOT (summary ? 'highlights');

-- 0004_clip_window.sql
-- Clip window provenance: the contiguous source slice that was transcribed.
-- NULL on both = the whole recording was processed (the default; existing rows
-- stay NULL — no backfill). Published segment/section timestamps are stored in
-- the FULL source's timeline regardless. See docs/adr/0001-clip-window-ingest-time-only.md.

ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS clip_start_seconds double precision,
  ADD COLUMN IF NOT EXISTS clip_end_seconds   double precision;

COMMENT ON COLUMN meetings.meetings.clip_start_seconds IS
  'Start (seconds, source timeline) of the transcribed slice; NULL = whole recording.';
COMMENT ON COLUMN meetings.meetings.clip_end_seconds IS
  'End (seconds, source timeline) of the transcribed slice; NULL = whole recording.';

-- 0005_meeting_thumbnail.sql
-- Public URL of the extracted-frame thumbnail (Supabase Storage), or null.
ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS thumbnail_url text;

COMMENT ON COLUMN meetings.meetings.thumbnail_url IS
  'Public URL of the extracted frame thumbnail (Supabase Storage); null if none.';

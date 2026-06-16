ALTER TABLE meetings.meetings
    ADD CONSTRAINT meetings_slug_unique UNIQUE (slug);

CREATE TABLE IF NOT EXISTS meetings.event_orgs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id  text NOT NULL REFERENCES meetings.meetings(slug) ON DELETE CASCADE,
    org_name    text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_event_orgs_meeting_id ON meetings.event_orgs(meeting_id);

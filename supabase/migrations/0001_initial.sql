-- CouncilScribe web data, in its own `civic` schema (shared E.V Backend project).
-- Covers council meetings, debates, candidate forums — any civic event.
-- Words/word-level timestamps deliberately stay out of the DB (they remain in
-- each meeting's transcript.json); segment-level timing is all the site needs.
--
-- NOTE: for the REST/JS API to reach this schema, `civic` must be added to
-- "Exposed schemas" in the dashboard (Project Settings → Data API).

create schema if not exists civic;

grant usage on schema civic to anon, authenticated, service_role;
alter default privileges in schema civic
  grant select on tables to anon, authenticated;
alter default privileges in schema civic
  grant all on tables to service_role;

create table civic.meetings (
  meeting_id     text primary key,
  city           text not null,
  body_slug      text,
  meeting_type   text not null,
  meeting_date   date not null,
  source_url     text,   -- citation link (page/permalink); null when source was a local file
  playback_kind  text,   -- 'youtube' | 'file' | 'hls' | null (extensible: 'vimeo', 'self_hosted'...)
  playback_url   text,   -- youtube: video id; file/hls: direct media URL
  duration_seconds numeric,
  summary        jsonb,
  processing_metadata jsonb,
  published_at   timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create table civic.people (
  politician_slug text primary key,  -- shared identifier with essentials.city
  politician_id   uuid unique,
  display_name    text not null,
  district_label  text,
  city            text
);

create table civic.meeting_speakers (
  meeting_id      text not null references civic.meetings on delete cascade,
  speaker_label   text not null,    -- diarization label, e.g. SPEAKER_03
  display_name    text,
  politician_slug text references civic.people,
  confidence      real,
  id_method       text,
  primary key (meeting_id, speaker_label)
);

create table civic.segments (
  meeting_id      text not null references civic.meetings on delete cascade,
  segment_id      integer not null,
  start_time      numeric not null,
  end_time        numeric not null,
  speaker_label   text not null,
  speaker_name    text,
  politician_slug text,             -- denormalized for one-scan "appearances"; no FK so publish order is free
  text            text not null,
  confidence      real,
  tsv tsvector generated always as (to_tsvector('english', text)) stored,
  primary key (meeting_id, segment_id)
);

create index segments_tsv_idx    on civic.segments using gin (tsv);
create index segments_person_idx on civic.segments (politician_slug, meeting_id, start_time);
create index meetings_date_idx   on civic.meetings (meeting_date desc);

-- Public read; writes only via service-role key (bypasses RLS).
alter table civic.meetings         enable row level security;
alter table civic.people           enable row level security;
alter table civic.meeting_speakers enable row level security;
alter table civic.segments         enable row level security;

create policy "public read" on civic.meetings         for select using (true);
create policy "public read" on civic.people           for select using (true);
create policy "public read" on civic.meeting_speakers for select using (true);
create policy "public read" on civic.segments         for select using (true);

-- Belt-and-braces explicit grants (default privileges cover future tables).
grant select on all tables in schema civic to anon, authenticated;
grant all    on all tables in schema civic to service_role;

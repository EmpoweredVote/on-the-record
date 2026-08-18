-- 1826_local_people_role_nullable.sql
--
-- DRAFT — NOT APPLIED. Scratch path in the on-the-record repo; a human moves this to
-- ev-accounts after review. Generated 2026-08-17.
--
-- NUMBER IS PROVISIONAL: 1826 was the next free number when this was written
-- (highest in ev-accounts/backend/migrations was 1825). Re-check with
-- check-migration-numbers.mjs before applying.
--
-- ORDERING: this migration MUST be applied BEFORE the matching on-the-record change
-- to src/publish.py::_upsert_local_people reaches a publish run. That change stops
-- coercing an unset local_role to 'candidate' and writes NULL instead; against the
-- current NOT NULL column every publish of a local person without a recorded role
-- would fail with a not-null violation.
--
-- WHY
--
-- meetings.local_people.role was created NOT NULL by migration 623, with the pipeline
-- expected to supply one of 'candidate' | 'moderator' | 'panelist'. In practice the
-- pipeline usually has no role to supply: the only code path that sets
-- SpeakerMapping.local_role is the terminal prompt in run_local.py, and the GUI review
-- path has no control for it at all. src/publish.py therefore filled the NOT NULL
-- column with `mapping.local_role or 'candidate'`.
--
-- The result is that "no role recorded" was published as the positive claim "candidate".
-- All 25 'candidate' rows currently in meetings.local_people are sitting members of the
-- U.S. House, published from federal floor proceedings by that default — none of them
-- was reviewed as a candidate. Moderators, panelists, staff and public commenters
-- reaching the same path are labelled the same way.
--
-- NULL is the honest representation of "review never recorded a role". Nothing
-- downstream needs a value: the ev-accounts API already types local_role as
-- `string | null` (it is a LEFT JOIN), and the web app carries it into
-- web/lib/types.ts without ever rendering it.
--
-- NOT DONE HERE — the existing CHECK is left exactly as it is:
--   * A CHECK is satisfied when it evaluates to NULL, so local_people_role_check
--     admits NULL as-is and needs no change for this migration.
--   * It does NOT need to stay as-is for the pipeline to work. Its three permitted
--     values are disjoint from the roles src/event_kinds.py actually offers for civic
--     meetings ('public_comment', 'staff', 'official', 'presenter'), and
--     resolve_local_role() can additionally emit normalised free text. Publishing any
--     council or school-board meeting that has local people therefore fails today on
--     local_people_role_check. That is a separate, pre-existing defect and a separate
--     decision (widen the enum, drop the CHECK, or constrain the vocabulary) and is
--     deliberately not folded in here. Reproduce it with:
--       INSERT INTO meetings.local_people (slug,name,role)
--       VALUES ('probe','Probe','public_comment');   -- CheckViolation
--
-- NO BACKFILL. The 25 mislabelled 'candidate' rows are deliberately left alone: this
-- migration cannot tell a defaulted 'candidate' from a reviewed one, and the correct
-- value for each row is a human judgment. The mislabelled group is identifiable, though:
-- every 'candidate' row has a 'congress-%' slug, and none was reviewed as a candidate.
-- A human decides whether to run:
--   UPDATE meetings.local_people SET role = NULL, updated_at = NOW()
--    WHERE role = 'candidate' AND slug LIKE 'congress-%';   -- 25 rows, NOT run here

BEGIN;

ALTER TABLE meetings.local_people
  ALTER COLUMN role DROP NOT NULL;

COMMENT ON COLUMN meetings.local_people.role IS
  'Role recorded for this local person during pipeline review '
  '(see src/event_kinds.py). NULL means no role was recorded — it is not a '
  'claim about the person. Never default it to a concrete value.';

INSERT INTO supabase_migrations.schema_migrations (version) VALUES ('1826') ON CONFLICT DO NOTHING;

COMMIT;

-- CA_0001_local_people_role_nullable.sql
--
-- DRAFT — NOT APPLIED. Scratch path in the on-the-record repo; a human moves this to
-- ev-accounts/backend/migrations/ after review. Generated 2026-08-17, renumbered 2026-08-21.
--
-- SLOT: CA_0001 — the first migration in Chris's `CA_` namespace, adopted in ev-accounts PR #128.
-- `CA_1` and `1` are different slots, so this cannot collide with the shared NNNN_ sequence, and
-- there is no shared max to read. Duplicates inside the namespace are still caught:
--     git -C <ev-accounts> fetch origin && npm run check:migrations --prefix backend
-- Cite it as CA_0001, never as "migration 1" — a bare number is ambiguous across namespaces.
--
-- Two earlier numbers were burned getting here, both from the shared sequence, and neither should
-- be resurrected: 1826 (taken by 1826_dc_shadow_senator_district.sql) and 1849 (free at the time,
-- but superseded by the namespace). The 1826 mistake came from reading a local ev-accounts working
-- copy that topped out at 1825 without fetching, when master was at 1848 — exactly the failure the
-- namespace removes.
--
-- HOUSE STYLE: no INSERT INTO supabase_migrations.schema_migrations — ev-accounts has no
-- schema_migrations table and no number-ordered runner (see its CLAUDE.md); the number is a
-- filename label for humans and each migration is applied once, ad hoc. Idempotent body plus a
-- post-verify DO $$ gate, per the same doc. Dry-run against prod first by swapping COMMIT for
-- ROLLBACK, and confirm the rollback actually reverted.
--
-- ORDERING: this migration MUST be applied BEFORE the on-the-record change in
-- src/publish.py::_upsert_local_people (merged to main as 400a01e, PR #156) reaches a publish
-- run. That change stops coercing an unset local_role to 'candidate' and writes NULL instead;
-- against the current NOT NULL column, publishing a local person without a recorded role raises
-- a not-null violation. Measured exposure: exactly one meeting in the 172-meeting local corpus,
-- 2026-07-16-house-floor, whose 25 local people all have no recorded role.
--
-- WHY
--
-- meetings.local_people.role was created NOT NULL by migration 623, with the pipeline expected to
-- supply one of 'candidate' | 'moderator' | 'panelist'. In practice the pipeline usually has no
-- role to supply: the only code path that sets SpeakerMapping.local_role is the terminal prompt in
-- run_local.py, and the GUI review path has no control for it at all. src/publish.py therefore
-- filled the NOT NULL column with `mapping.local_role or 'candidate'`.
--
-- The result is that "no role recorded" was published as the positive claim "candidate". All 25
-- 'candidate' rows currently in meetings.local_people are sitting members of the U.S. House,
-- published from federal floor proceedings by that default — none was reviewed as a candidate.
-- Moderators, panelists, staff and public commenters reaching the same path are labelled the same.
--
-- NULL is the honest representation of "review never recorded a role". Nothing downstream needs a
-- value: the ev-accounts API already types local_role as `string | null` (it is a LEFT JOIN), and
-- the web app carries it into web/lib/types.ts without ever rendering it.
--
-- NOT DONE HERE — the existing CHECK is left exactly as it is:
--   * A CHECK is satisfied when it evaluates to NULL, so local_people_role_check admits NULL as-is
--     and needs no change for this migration.
--   * It does NOT need to stay as-is for the pipeline to work. Its three permitted values are
--     disjoint from the roles src/event_kinds.py actually offers for civic meetings
--     ('public_comment', 'staff', 'official', 'presenter'), and resolve_local_role() can
--     additionally emit normalised free text. Publishing any council or school-board meeting that
--     has local people therefore fails today on local_people_role_check. That is a separate,
--     pre-existing defect and a separate decision (widen the enum, drop the CHECK, or constrain
--     the vocabulary), deliberately not folded in here. Reproduce it with:
--       INSERT INTO meetings.local_people (slug,name,role)
--       VALUES ('probe','Probe','public_comment');   -- CheckViolation
--
-- NO BACKFILL, AND PROBABLY NOT THE ONE YOU WANT. The 25 mislabelled 'candidate' rows are left
-- alone: this migration cannot tell a defaulted 'candidate' from a reviewed one. Note before
-- choosing a remedy that all 25 also carry a resolved essentials politician_id alongside their
-- congress-* local_slug, which violates migration 623's stated invariant ("either politician_slug
-- OR local_slug is set on a speaker row, never both") — prod counts are 25 both-set, 379
-- politician-only, 1 local-only. So the right fix is more likely deleting those 25 local_people
-- rows and nulling the speakers' local_slug than setting role = NULL on them. A human decides.

BEGIN;

ALTER TABLE meetings.local_people
  ALTER COLUMN role DROP NOT NULL;

COMMENT ON COLUMN meetings.local_people.role IS
  'Role recorded for this local person during pipeline review '
  '(see on-the-record src/event_kinds.py). NULL means no role was recorded — it is not a '
  'claim about the person. Never default it to a concrete value.';

-- post-verify gate
DO $$
DECLARE
  v_notnull boolean;
BEGIN
  SELECT attnotnull INTO v_notnull
    FROM pg_attribute
   WHERE attrelid = 'meetings.local_people'::regclass
     AND attname  = 'role'
     AND NOT attisdropped;

  IF v_notnull IS NULL THEN
    RAISE EXCEPTION 'meetings.local_people.role not found — wrong table or column renamed';
  END IF;
  IF v_notnull THEN
    RAISE EXCEPTION 'meetings.local_people.role is still NOT NULL — DROP NOT NULL did not take';
  END IF;
END $$;

COMMIT;

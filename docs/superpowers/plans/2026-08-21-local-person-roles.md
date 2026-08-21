# Local-Person Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make civic meetings publishable again with the roles reviewers already recorded, give the GUI the ability to create local people, and stop producing speakers with two identities.

**Architecture:** The role vocabulary moves fully into the application: the DB drops its value CHECK for a shape CHECK, and `resolve_local_role` is made to guarantee that shape. The terminal wizard's logic is extracted into `src/review.py` alongside every other speaker-state transition, then called by both the terminal and a new pair of GUI routes. `assign_local_person` clears any essentials identity, so the one-identity contradiction is never created rather than being suppressed at publish.

**Tech Stack:** Python 3 (pytest), FastAPI + Jinja2 for the local GUI, Postgres (Supabase) reached via psycopg2, SQL migrations applied by hand.

**Design spec:** `docs/superpowers/specs/2026-08-21-local-person-roles-design.md`

## Global Constraints

- **Always use `.venv/bin/python`**, never system `python3` — the system interpreter lacks project deps.
- Run tests from the repo root: `.venv/bin/python -m pytest`.
- The full suite is **2061 passed, 3 skipped** before this work. The 3 skips need `DATABASE_URL` exported and are expected.
- TDD throughout: write the failing test, watch it fail for the right reason, then implement.
- Migrations live in `ev-accounts/backend/migrations/`. **Chris's migrations use the `CA_` namespace**, so this one is `CA_0003`. Zero-pad to four digits.
- Migration house style: idempotent body, a `DO $$ … $$` post-verify gate that `RAISE EXCEPTION`s on a wrong result, and **no** `INSERT INTO supabase_migrations.schema_migrations` — that table does not exist in ev-accounts.
- **Dry-run every migration against prod first** by swapping `COMMIT` for `ROLLBACK`, and confirm the rollback actually reverted before the real run.
- `meetings.local_people.role` is already nullable (migration `CA_0001`).
- End commit messages with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Never commit `.env.local`.

---

### Task 1: `resolve_local_role` guarantees the shape the DB will require

**Files:**
- Modify: `src/event_kinds.py:61-79`
- Test: `tests/test_local_roles.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LOCAL_ROLE_PATTERN: str`, `LOCAL_ROLE_RE: re.Pattern`, and an unchanged signature `resolve_local_role(raw, event_kind) -> str` whose return value now always matches `LOCAL_ROLE_RE`.

Context: `resolve_local_role` normalises free text to `[a-z0-9_]`, which can produce a leading digit (`"123 Main St"` → `"123_main_st"`). Task 6 adds a DB CHECK requiring a leading letter, so the normalizer must guarantee it or publish will fail on inputs the prompt accepts.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_local_roles.py`:

```python
from src.event_kinds import LOCAL_ROLE_RE, resolve_local_role


def test_resolve_local_role_always_matches_the_db_shape():
    """The DB CHECK added by CA_0003 requires a leading letter, so every value
    this function can return must satisfy LOCAL_ROLE_RE — otherwise the prompt
    accepts roles that publish cannot store."""
    for raw in ["City Attorney", "123 Main St", "_leading", "!!!", "3rd party",
                "  ", "x" * 200, "Dept. Head!", "ZONING board"]:
        role = resolve_local_role(raw, "council")
        assert LOCAL_ROLE_RE.match(role), f"{raw!r} produced {role!r}"


def test_resolve_local_role_strips_leading_non_letters():
    assert resolve_local_role("123 Main St", "council") == "main_st"


def test_resolve_local_role_falls_back_when_nothing_survives():
    # all digits normalise away entirely -> the kind's default, not an empty role
    assert resolve_local_role("123", "council") == "public_comment"


def test_resolve_local_role_truncates_to_forty_chars():
    role = resolve_local_role("a" * 80, "council")
    assert len(role) == 40
```

Note `"123"` is `.isdigit()`, so it takes the numeric-option branch and returns `roles[0]` — `public_comment` for `council`. The assertion documents that, and holds either way.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_local_roles.py -v -k "shape or leading or survives or truncates"`
Expected: FAIL — `ImportError: cannot import name 'LOCAL_ROLE_RE'`.

- [ ] **Step 3: Implement**

In `src/event_kinds.py`, above `local_roles_for`, add:

```python
# Shape a local person's role must take to be storable. Kept in sync BY HAND with
# the CHECK in ev-accounts migration CA_0003 — a regex cannot be shared between
# Python and SQL, so that migration's comment quotes this constant by name.
LOCAL_ROLE_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
LOCAL_ROLE_RE = re.compile(LOCAL_ROLE_PATTERN)


def _shape_local_role(norm: str, default: str) -> str:
    """Coerce a normalized role into LOCAL_ROLE_PATTERN, or fall back to `default`.

    The pattern demands a leading letter, so strip leading digits/underscores;
    then bound the length. Anything that normalises away entirely becomes the
    caller's default rather than an empty role.
    """
    candidate = norm.lstrip("_0123456789")[:40].strip("_")
    return candidate if LOCAL_ROLE_RE.match(candidate) else default
```

Then replace the last line of `resolve_local_role`:

```python
    norm = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return _shape_local_role(norm, roles[0])
```

(was `return norm or roles[0]`)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_local_roles.py -v`
Expected: PASS, including the pre-existing cases — `"City Attorney"` → `city_attorney`, `"Dept. Head!"` → `dept_head`, `"clerk"` → `clerk` all still hold.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 2065 passed, 3 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/event_kinds.py tests/test_local_roles.py
git commit -m "fix(event_kinds): resolve_local_role guarantees a storable role shape

Normalisation could return a leading digit ('123 Main St' -> '123_main_st'),
which the CHECK in CA_0003 rejects. LOCAL_ROLE_PATTERN is now the canonical
shape and resolve_local_role coerces to it, falling back to the event kind's
default when nothing survives.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `LOCAL_SLUG_RE` and `default_local_slug` in `src/review.py`

**Files:**
- Modify: `src/review.py` (add after `make_unidentified_slug`, which ends at line 47)
- Test: `tests/test_review_local_people.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `LOCAL_SLUG_PATTERN: str`, `LOCAL_SLUG_RE: re.Pattern`, `default_local_slug(name: Optional[str], label: Optional[str]) -> str`.

Context: the slug regex is currently an inline literal at `run_local.py:2963` and duplicates ev-accounts' `SLUG_REGEX`; the kebab-case derivation is inline just above it. Both move here so the GUI cannot drift from the terminal.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review_local_people.py`:

```python
import pytest

from src.models import SpeakerMapping
from src.review import LOCAL_SLUG_RE, default_local_slug


def test_default_local_slug_kebab_cases_the_name():
    assert default_local_slug("Susan Brackney", "SPEAKER_04") == "susan-brackney"


def test_default_local_slug_falls_back_to_the_label():
    assert default_local_slug(None, "SPEAKER_04") == "speaker-04"
    assert default_local_slug("   ", "SPEAKER_04") == "speaker-04"


def test_default_local_slug_output_is_always_valid():
    for name, label in [("Susan Brackney", "S0"), ("!!!", "S0"), ("!!!", "!!!"),
                        ("O'Brien-Smith, Jr.", "S1"), ("x" * 300, "S2")]:
        slug = default_local_slug(name, label)
        assert LOCAL_SLUG_RE.match(slug), f"({name!r}, {label!r}) produced {slug!r}"


def test_default_local_slug_is_bounded_at_one_hundred():
    assert len(default_local_slug("x" * 300, "S2")) == 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review_local_people.py -v`
Expected: FAIL — `ImportError: cannot import name 'LOCAL_SLUG_RE' from 'src.review'`.

- [ ] **Step 3: Implement**

In `src/review.py`, after `make_unidentified_slug`:

```python
# A site-local person's slug. Mirrors ev-accounts SLUG_REGEX; was an inline
# literal in run_local.py's terminal wizard before the GUI needed it too.
LOCAL_SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,99}$"
LOCAL_SLUG_RE = _re.compile(LOCAL_SLUG_PATTERN)


def default_local_slug(name, label) -> str:
    """Kebab-case slug for a new local person, from the name or the diarized label.

    Always returns a value matching LOCAL_SLUG_RE so the caller can offer it as a
    prefilled default without validating first. Falls through name -> label ->
    'speaker', mirroring make_unidentified_slug's fallback chain.
    """
    for source in ((name or "").strip(), (label or "").strip()):
        slug = _re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")[:100].strip("-")
        if LOCAL_SLUG_RE.match(slug):
            return slug
    return "speaker"
```

`src/review.py` already imports `re` as `_re` (see `make_unidentified_slug`) — use that alias.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review_local_people.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/review.py tests/test_review_local_people.py
git commit -m "refactor(review): own the local-person slug shape and default

LOCAL_SLUG_PATTERN mirrors ev-accounts SLUG_REGEX and was an inline literal in
run_local.py's terminal wizard. default_local_slug always returns a valid slug,
so a caller can prefill it without validating first.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `assign_local_person` / `clear_local_person`

**Files:**
- Modify: `src/review.py` (add after `link_speaker`, which ends at line 290)
- Test: `tests/test_review_local_people.py`

**Interfaces:**
- Consumes: `LOCAL_SLUG_RE` from Task 2.
- Produces: `assign_local_person(mappings: dict, label: str, slug: str, role: Optional[str]) -> SpeakerMapping` (raises `ValueError`), and `clear_local_person(mappings: dict, label: str) -> Optional[SpeakerMapping]`.

Two invariants this enforces, both learned the hard way:

1. **One identity per speaker.** Migration 623 says either an essentials identity or a local person, never both. PR #160 suppresses the contradiction at publish; this stops creating it.
2. **Two diarized labels cannot be the same person.** The name-collision guard already exists; `local_slug` never had one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review_local_people.py`:

```python
from src.review import assign_local_person, clear_local_person


def test_assign_local_person_sets_slug_and_role():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Susan Brackney")}
    m = assign_local_person(mappings, "S0", "susan-brackney", "public_comment")
    assert (m.local_slug, m.local_role) == ("susan-brackney", "public_comment")


def test_assign_local_person_clears_any_essentials_identity():
    """One identity per speaker (migration 623). A local person is not a roster
    politician, so making someone local drops the essentials link rather than
    leaving publish to suppress the contradiction."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Marcy Kaptur",
                                     politician_id="uuid-mk", politician_slug="marcy-kaptur")}
    m = assign_local_person(mappings, "S0", "marcy-kaptur", "official")
    assert m.politician_id is None
    assert m.politician_slug is None


def test_assign_local_person_creates_a_mapping_for_an_unmapped_label():
    mappings = {}
    m = assign_local_person(mappings, "S7", "jane-doe", "staff")
    assert mappings["S7"] is m
    assert m.speaker_label == "S7"


def test_assign_local_person_rejects_an_invalid_slug():
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    for bad in ["Susan Brackney", "-leading", "_leading", "", "x" * 101, "UPPER"]:
        with pytest.raises(ValueError):
            assign_local_person(mappings, "S0", bad, "staff")


def test_assign_local_person_refuses_a_slug_held_by_another_label():
    """Two diarized labels cannot be the same person."""
    mappings = {
        "S0": SpeakerMapping(speaker_label="S0", local_slug="susan-brackney"),
        "S1": SpeakerMapping(speaker_label="S1"),
    }
    with pytest.raises(ValueError, match="already used"):
        assign_local_person(mappings, "S1", "susan-brackney", "public_comment")


def test_assign_local_person_allows_reassigning_the_same_label():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="susan-brackney",
                                     local_role="public_comment")}
    m = assign_local_person(mappings, "S0", "susan-brackney", "staff")
    assert m.local_role == "staff"


def test_clear_local_person_unsets_both_fields():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", local_slug="susan-brackney",
                                     local_role="public_comment")}
    m = clear_local_person(mappings, "S0")
    assert (m.local_slug, m.local_role) == (None, None)


def test_clear_local_person_on_unknown_label_is_a_noop():
    assert clear_local_person({}, "S9") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review_local_people.py -v -k "assign or clear"`
Expected: FAIL — `ImportError: cannot import name 'assign_local_person'`.

- [ ] **Step 3: Implement**

In `src/review.py`, after `link_speaker`:

```python
def assign_local_person(mappings, label, slug, role):
    """Make `label` a site-local person with `slug` and `role`. Mutates in place.

    Clears any essentials identity: migration 623's invariant is one identity per
    speaker, and a local person is not a roster politician. Enforcing it here means
    publish never has to suppress a contradiction it should not have received.

    Raises ValueError when `slug` fails LOCAL_SLUG_RE, or when a DIFFERENT label in
    this meeting already holds it — two diarized labels cannot be the same person.
    """
    from src.models import SpeakerMapping

    slug = (slug or "").strip()
    if not LOCAL_SLUG_RE.match(slug):
        raise ValueError(f"invalid local slug {slug!r}; must match {LOCAL_SLUG_PATTERN}")
    for other_label, other in mappings.items():
        if other_label != label and getattr(other, "local_slug", None) == slug:
            raise ValueError(f"local slug {slug!r} already used by label {other_label!r}")

    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    mapping.local_slug = slug
    mapping.local_role = role
    mapping.politician_slug = None
    mapping.politician_id = None
    mappings[label] = mapping
    return mapping


def clear_local_person(mappings, label):
    """Drop a speaker's local-person identity. Returns None if the label has no mapping."""
    mapping = mappings.get(label)
    if mapping is None:
        return None
    mapping.local_slug = None
    mapping.local_role = None
    return mapping
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review_local_people.py -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 2077 passed, 3 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/review.py tests/test_review_local_people.py
git commit -m "feat(review): assign_local_person / clear_local_person

One shared local-person transition for the terminal wizard and the GUI. It
enforces two invariants at the source: an essentials identity is cleared (623's
one-identity-per-speaker, which publish currently suppresses after the fact),
and a slug already held by another label in the meeting is refused, since two
diarized labels cannot be the same person.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Terminal wizard calls the shared unit

**Files:**
- Modify: `run_local.py:2951-2977`

**Interfaces:**
- Consumes: `default_local_slug`, `assign_local_person`, `LOCAL_SLUG_PATTERN` (Tasks 2-3); `resolve_local_role`, `local_roles_for` (Task 1, unchanged signatures).
- Produces: nothing new.

This is a pure refactor: no behaviour change, verified by the suite plus the unit tests from Tasks 2-3. The wizard is `input()`-driven and has no automated coverage of its own, which is exactly why its logic belongs in tested functions.

- [ ] **Step 1: Replace the inline logic**

Replace `run_local.py:2951-2977` (from the `# Auto-generate a kebab-case default slug` comment through the `print(f"  Local person: …")` line) with:

```python
    from src.event_kinds import local_roles_for, resolve_local_role
    from src.review import assign_local_person, default_local_slug

    default_slug = default_local_slug(name, label)
    slug_raw = input(f"  Slug [{default_slug}]: ").strip()
    slug = slug_raw or default_slug

    roles = local_roles_for(event_kind)
    options = "  ".join(f"{i + 1}) {r}" for i, r in enumerate(roles))
    print(f"  Role: {options}")
    print("  (pick a number, or type a custom role)")
    role = resolve_local_role(input(f"  [1={roles[0]}]: "), event_kind)

    try:
        assign_local_person(mappings, label, slug, role)
    except ValueError as exc:
        print(f"  {exc} — left unlinked.")
        return
    print(f"  Local person: {slug} ({role})")
```

Two changes beyond deduplication, both improvements the shared unit brings for free: the slug-collision case is now caught and reported instead of silently creating a second speaker with the same slug, and the essentials link is cleared. The old `re.match(...)` validation block and the `mapping.local_slug = …` / `mapping.local_role = …` assignments are removed — `assign_local_person` does both.

Note the surrounding function uses `mapping` for the current label's mapping; `assign_local_person` needs the whole `mappings` dict. Confirm which is in scope at that point and pass the dict — if only `mapping` is available, thread `mappings` in from the caller rather than reimplementing the collision check.

- [ ] **Step 2: Verify nothing regressed**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 2077 passed, 3 skipped.

- [ ] **Step 3: Verify the removed literal is really gone**

Run: `grep -n 'a-z0-9_-' run_local.py`
Expected: no match for the old slug pattern — the only definition now lives in `src/review.py`.

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "refactor(run_local): terminal wizard calls the shared local-person unit

Drops the inline slug regex, kebab-case derivation and field assignments in
favour of default_local_slug / assign_local_person. Two behaviour improvements
come along: a slug already held by another label is now reported instead of
silently duplicated, and any essentials link is cleared.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `identity_label` prefers `politician_id`

**Files:**
- Modify: `src/review.py:58-60`
- Test: `tests/test_review_local_people.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing new — `identity_label(mapping) -> str` keeps its signature.

Context: `politician_slug` is NULL for ~99.4% of essentials politicians, so a federal speaker with `politician_id` and a `congress-*` stash displays as `local:congress-XXXX`. `src/enroll.py:215` already gets this right and is the pattern to copy. Display-only — the enrollment key was never affected.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_review_local_people.py`:

```python
from src.review import identity_label


def test_identity_label_prefers_politician_id_over_a_local_slug():
    """politician_slug is NULL for ~99.4% of essentials politicians, so a federal
    speaker carrying politician_id plus the crec bioguide stash must not read as
    a local person. Mirrors src/enroll.py:215."""
    m = SpeakerMapping(speaker_label="S0", speaker_name="Marcy Kaptur",
                       politician_id="uuid-mk", local_slug="congress-K000009")
    assert identity_label(m) == "essentials:uuid-mk"


def test_identity_label_still_prefers_a_slug_when_present():
    m = SpeakerMapping(speaker_label="S0", politician_slug="marcy-kaptur")
    assert identity_label(m) == "essentials:marcy-kaptur"


def test_identity_label_reports_a_genuine_local_person():
    m = SpeakerMapping(speaker_label="S0", local_slug="susan-brackney")
    assert identity_label(m) == "local:susan-brackney"
```

- [ ] **Step 2: Run the tests to verify the first fails**

Run: `.venv/bin/python -m pytest tests/test_review_local_people.py -v -k identity_label`
Expected: `test_identity_label_prefers_politician_id_over_a_local_slug` FAILS with `assert 'local:congress-K000009' == 'essentials:uuid-mk'`. The other two pass.

- [ ] **Step 3: Implement**

In `src/review.py::identity_label`, insert before the `politician_slug` branch:

```python
    if mapping.politician_id:
        # Key on the stable UUID first: politician_slug is NULL for ~99.4% of
        # essentials.politicians. Mirrors resolve_mapping_enrollment (enroll.py).
        return f"essentials:{mapping.politician_id}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review_local_people.py -v -k identity_label`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 2080 passed, 3 skipped. If an existing test asserted `essentials:<slug>` for a mapping that has both an id and a slug, it will now fail — update it to expect the id and note why in the commit.

- [ ] **Step 6: Commit**

```bash
git add src/review.py tests/test_review_local_people.py
git commit -m "fix(review): identity_label prefers politician_id over local_slug

politician_slug is NULL for ~99.4% of essentials politicians, so a federal
speaker carrying politician_id plus the crec bioguide stash displayed as
local:congress-XXXX in the review table. Mirrors src/enroll.py:215, which
already had the right precedence.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `CA_0003` — shape CHECK replaces the value CHECK

**Files:**
- Create: `ev-accounts/backend/migrations/CA_0003_local_people_role_shape.sql`

**Interfaces:**
- Consumes: `LOCAL_ROLE_PATTERN` from Task 1 (by hand — the regex is duplicated between Python and SQL out of necessity).
- Produces: a `meetings.local_people.role` column that accepts every value `resolve_local_role` can emit.

**Task 1 must be merged before this is applied**, so no role that fails the new CHECK can be produced.

- [ ] **Step 1: Confirm the number is free**

```bash
git -C ~/Documents/GitHub/ev-accounts fetch origin
```

Then from a worktree on `origin/master`: `node backend/scripts/check-migration-numbers.mjs`. `CA_0003` is expected free — `CA_0001` and `CA_0002` are on master.

- [ ] **Step 2: Write the migration**

Create `backend/migrations/CA_0003_local_people_role_shape.sql`:

```sql
-- CA_0003_local_people_role_shape.sql
--
-- Replaces local_people_role_check with a SHAPE constraint, so the DB stops dictating the role
-- vocabulary.
--
-- WHY. Migration 623 constrained role to ('candidate','moderator','panelist'). on-the-record's
-- src/event_kinds.py offers council / school_board / community_meeting reviewers
-- 'public_comment', 'staff', 'official', 'presenter' — a disjoint set — and resolve_local_role()
-- can emit normalised free text besides. 38 of the 40 role values in the local corpus are
-- unpublishable, and because publish runs in one transaction, republishing 2026-02-04-council
-- (19 local people) aborts entirely. The value CHECK was never the real authority; it only ever
-- blocked what the application actually produced.
--
-- A shape CHECK still keeps garbage out — empty strings, spaces, mixed case, long pastes — without
-- deciding the vocabulary. Whether a role is ever shown to a reader is an open question, and this
-- migration deliberately does not answer it. If roles later become a display contract, a closed
-- enum becomes the better choice and this should be revisited.
--
-- The pattern is the SQL twin of LOCAL_ROLE_PATTERN in on-the-record src/event_kinds.py. A regex
-- cannot be shared between Python and SQL; they are kept in sync by hand, and Task 1 of the plan
-- makes resolve_local_role guarantee this shape so the pairing cannot silently drift into failure.
--
-- role stays NULLable (CA_0001): NULL means no role was recorded, which is not a claim about the
-- person. A CHECK is satisfied when it evaluates to NULL, so the guard below admits NULL as-is.
--
-- Dry-run against prod first by swapping COMMIT for ROLLBACK, and confirm the rollback reverted.

BEGIN;

ALTER TABLE meetings.local_people
  DROP CONSTRAINT IF EXISTS local_people_role_check;

DO $$ BEGIN
  ALTER TABLE meetings.local_people
    ADD CONSTRAINT local_people_role_shape
    CHECK (role IS NULL OR role ~ '^[a-z][a-z0-9_]{0,39}$');
EXCEPTION WHEN duplicate_object THEN
  NULL;
END $$;

-- post-verify gate
DO $$
DECLARE
  v_old int;
  v_new int;
  v_bad int;
BEGIN
  SELECT count(*) INTO v_old FROM pg_constraint
   WHERE conrelid = 'meetings.local_people'::regclass AND conname = 'local_people_role_check';
  IF v_old <> 0 THEN
    RAISE EXCEPTION 'local_people_role_check still present';
  END IF;

  SELECT count(*) INTO v_new FROM pg_constraint
   WHERE conrelid = 'meetings.local_people'::regclass AND conname = 'local_people_role_shape';
  IF v_new <> 1 THEN
    RAISE EXCEPTION 'local_people_role_shape missing (found %)', v_new;
  END IF;

  SELECT count(*) INTO v_bad FROM meetings.local_people
   WHERE role IS NOT NULL AND role !~ '^[a-z][a-z0-9_]{0,39}$';
  IF v_bad <> 0 THEN
    RAISE EXCEPTION '% stored role(s) fail the new shape', v_bad;
  END IF;
END $$;

COMMIT;
```

- [ ] **Step 3: Dry-run against prod**

```bash
cd ~/Documents/GitHub/on-the-record && ./.venv/bin/python - <<'PY'
import re, psycopg2
env = {}
for line in open('.env.local'):
    m = re.match(r'^(?:export\s+)?([A-Z0-9_]+)=(.*)$', line.strip())
    if m: env[m.group(1)] = m.group(2).strip('"\'')
sql = open('/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/CA_0003_local_people_role_shape.sql').read()
assert sql.rstrip().endswith("COMMIT;")
dry = sql.rstrip()[:-len("COMMIT;")] + "ROLLBACK;"
conn = psycopg2.connect(env['DATABASE_URL']); conn.autocommit = True
cur = conn.cursor(); cur.execute("SET lock_timeout='5s'")
cur.execute(dry)
print("DRY RUN passed the gate")
cur.execute("""SELECT conname FROM pg_constraint
               WHERE conrelid='meetings.local_people'::regclass AND conname LIKE 'local_people_role%'""")
print("constraints after rollback (must be local_people_role_check):", cur.fetchall())
conn.close()
PY
```

Expected: gate passes, and after rollback the constraint list shows `local_people_role_check` — i.e. the revert really happened. **If it shows `local_people_role_shape`, stop.**

- [ ] **Step 4: Apply for real**

Same script with `sql` instead of `dry`. Then verify:

```sql
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid='meetings.local_people'::regclass AND conname LIKE 'local_people_role%';
```

Expected: only `local_people_role_shape`, with the `~` pattern.

- [ ] **Step 5: Prove the blocker is gone**

Insert-and-rollback probe with the role that used to fail:

```sql
INSERT INTO meetings.local_people (slug, name, role) VALUES ('probe','Probe','public_comment');
```

Expected: accepted (roll it back). Before CA_0003 this raised `CheckViolation`.

- [ ] **Step 6: Commit, PR and merge in ev-accounts**

Header updated to `-- ✅ APPLIED TO PROD <date>` with the dry-run evidence, per the CA_0001/CA_0002 precedent. Run `node backend/scripts/check-migration-numbers.mjs` before pushing.

---

### Task 7: GUI review_api — create and clear a local person

**Files:**
- Modify: `gui/review_api.py` (add after `apply_unlink`, which ends at line 255)
- Test: `tests/test_gui_review.py`

**Interfaces:**
- Consumes: `assign_local_person`, `clear_local_person` (Task 3); `resolve_local_role` (Task 1); existing `_load_meeting_ctx`, `persist_review`.
- Produces: `apply_make_local_person(meeting_id: str, label: str, slug: str, role_raw: str) -> bool` (raises `ValueError`), `apply_clear_local_person(meeting_id: str, label: str) -> bool`.

The two failure modes are kept distinct so Task 8 can map them to different HTTP codes: `False` means unknown/unsafe meeting or label (404), `ValueError` means a bad or colliding slug (400).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_apply_make_and_clear_local_person(tagged_meeting_dir, tmp_meetings_dir):
    from gui.review_api import apply_clear_local_person, apply_make_local_person
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)

    assert apply_make_local_person("2026-02-04-council", "SPEAKER_01",
                                   "susan-brackney", "2") is True
    page = load_review_page("2026-02-04-council")
    card = [c for c in page.all_cards if c.label == "SPEAKER_01"][0]
    assert card.local_slug == "susan-brackney"
    assert card.local_role == "staff"      # option 2 for event_kind 'council'

    assert apply_clear_local_person("2026-02-04-council", "SPEAKER_01") is True
    card2 = [c for c in load_review_page("2026-02-04-council").all_cards
             if c.label == "SPEAKER_01"][0]
    assert card2.local_slug is None


def test_apply_make_local_person_accepts_a_custom_role(tagged_meeting_dir, tmp_meetings_dir):
    from gui.review_api import apply_make_local_person
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_make_local_person("2026-02-04-council", "SPEAKER_01",
                                   "jo-doe", "City Attorney") is True
    card = [c for c in load_review_page("2026-02-04-council").all_cards
            if c.label == "SPEAKER_01"][0]
    assert card.local_role == "city_attorney"


def test_apply_make_local_person_clears_an_essentials_link(tagged_meeting_dir, tmp_meetings_dir):
    from gui.review_api import apply_link, apply_make_local_person
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    apply_link("2026-02-04-council", "SPEAKER_01", "clerk-smith", "uuid-cs")
    apply_make_local_person("2026-02-04-council", "SPEAKER_01", "clerk-smith-local", "staff")
    card = [c for c in load_review_page("2026-02-04-council").all_cards
            if c.label == "SPEAKER_01"][0]
    assert card.is_linked is False


def test_apply_make_local_person_guards(tagged_meeting_dir, tmp_meetings_dir):
    import pytest
    from gui.review_api import apply_make_local_person
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    # unknown / unsafe -> False
    assert apply_make_local_person("ghost", "SPEAKER_00", "a-b", "staff") is False
    assert apply_make_local_person("2026-02-04-council", "SPEAKER_99", "a-b", "staff") is False
    assert apply_make_local_person("../x", "SPEAKER_00", "a-b", "staff") is False
    # bad slug -> ValueError, a different failure the route reports differently
    with pytest.raises(ValueError):
        apply_make_local_person("2026-02-04-council", "SPEAKER_00", "Susan Brackney", "staff")
    # slug already held by another label -> ValueError
    apply_make_local_person("2026-02-04-council", "SPEAKER_00", "taken-slug", "staff")
    with pytest.raises(ValueError, match="already used"):
        apply_make_local_person("2026-02-04-council", "SPEAKER_01", "taken-slug", "staff")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -v -k local_person`
Expected: FAIL — `ImportError: cannot import name 'apply_make_local_person'`.

- [ ] **Step 3: Implement**

In `gui/review_api.py`, after `apply_unlink`:

```python
def apply_make_local_person(meeting_id: str, label: str, slug: str, role_raw: str) -> bool:
    """Make a speaker a site-local person and persist.

    `role_raw` is whatever the reviewer typed or picked; it goes through
    resolve_local_role, which guarantees a storable shape, so a role can never be
    invalid here. Returns False on an unsafe/unknown meeting or label. Raises
    ValueError on a slug that is malformed or already held by another label —
    a distinct failure the route reports as 400 rather than 404.
    """
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review
    from src.event_kinds import resolve_local_role

    role = resolve_local_role(role_raw, meeting.event_kind)
    review.assign_local_person(meeting.speakers, label, slug, role)   # may raise ValueError
    persist_review(meeting, meeting_dir)
    return True


def apply_clear_local_person(meeting_id: str, label: str) -> bool:
    """Drop a speaker's local-person identity and persist. False on unsafe/unknown."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review

    review.clear_local_person(meeting.speakers, label)
    persist_review(meeting, meeting_dir)
    return True
```

These tests also depend on the card fields from Task 9. If Task 9 is not yet done, `card.local_slug` raises `AttributeError` — do Task 9 first, or land Tasks 7 and 9 together.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -v -k local_person`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): create and clear a local person from review

The GUI could not create local people at all — review_api never touched
local_slug or local_role, so the only path was the terminal wizard. Delegates
to the shared review.assign_local_person, and keeps the two failure modes
distinct so the route can answer 404 for an unknown label and 400 for a bad
slug.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: GUI routes

**Files:**
- Modify: `gui/app.py` (add after `unlink_speaker_route`, around line 388)
- Test: `tests/test_gui_review.py`

**Interfaces:**
- Consumes: `apply_make_local_person`, `apply_clear_local_person` (Task 7).
- Produces: `POST /meetings/{meeting_id}/speakers/{label}/local-person` and `…/local-person/clear`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`, following the TestClient pattern already used in this file:

```python
def test_local_person_routes(tagged_meeting_dir, tmp_meetings_dir):
    from fastapi.testclient import TestClient
    from gui.app import create_app
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app(), follow_redirects=False)

    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/local-person",
                    data={"slug": "susan-brackney", "role": "2"})
    assert r.status_code == 303
    card = [c for c in load_review_page("2026-02-04-council").all_cards
            if c.label == "SPEAKER_01"][0]
    assert card.local_slug == "susan-brackney"

    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/local-person/clear")
    assert r.status_code == 303
    card = [c for c in load_review_page("2026-02-04-council").all_cards
            if c.label == "SPEAKER_01"][0]
    assert card.local_slug is None


def test_local_person_route_rejects_a_bad_slug_with_400(tagged_meeting_dir, tmp_meetings_dir):
    """A silently-ignored submission is the worst outcome for a reviewer, so an
    invalid slug is a visible 400 rather than the no-op redirect set_speaker_name
    uses for empty input."""
    from fastapi.testclient import TestClient
    from gui.app import create_app
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app(), follow_redirects=False)
    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/local-person",
                    data={"slug": "Susan Brackney", "role": "staff"})
    assert r.status_code == 400


def test_local_person_route_unknown_label_is_404(tagged_meeting_dir, tmp_meetings_dir):
    from fastapi.testclient import TestClient
    from gui.app import create_app
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app(), follow_redirects=False)
    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_99/local-person",
                    data={"slug": "a-b", "role": "staff"})
    assert r.status_code == 404
```

Check how the existing tests in this file build the app — if they import a module-level `app` rather than `create_app()`, match that.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -v -k "local_person_route"`
Expected: FAIL with 404 from FastAPI — the routes do not exist.

- [ ] **Step 3: Implement**

In `gui/app.py`, after `unlink_speaker_route`:

```python
    @app.post("/meetings/{meeting_id}/speakers/{label}/local-person")
    def make_local_person_route(meeting_id: str, label: str,
                               slug: str = Form(""), role: str = Form("")):
        try:
            ok = review_api.apply_make_local_person(meeting_id, label, slug, role)
        except ValueError as exc:
            # Malformed or colliding slug. Reported, not silently ignored: the
            # form prefills a valid default, so this is a deliberate bad value.
            raise HTTPException(status_code=400, detail=str(exc))
        if not ok:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/local-person/clear")
    def clear_local_person_route(meeting_id: str, label: str):
        if not review_api.apply_clear_local_person(meeting_id, label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -v -k "local_person"`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_review.py
git commit -m "feat(gui): routes for creating and clearing a local person

400 on a malformed or colliding slug, 404 on an unknown label. The form
prefills a valid default, so a bad slug is a deliberate value and deserves a
visible error rather than the silent no-op redirect set_speaker_name uses.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Card fields, page role options, and the review UI control

**Files:**
- Modify: `gui/models.py` (`SpeakerCard` ~line 179-200, `ReviewPageData` ~line 260)
- Modify: `gui/review_api.py:419-440` (card construction) and `:446` (page construction)
- Modify: `gui/templates/panels/_macros.html:1` (macro signature) and the identity group (~line 44-57)
- Modify: `gui/templates/panels/review.html:36,42` (both `m.card(...)` call sites)
- Test: `tests/test_gui_review.py`

**Interfaces:**
- Consumes: `local_roles_for` (existing), `default_local_slug` (Task 2).
- Produces: `SpeakerCard.local_slug`, `.local_role`, `.default_slug`, `.has_local_person`; `ReviewPageData.local_role_options`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_review.py`:

```python
def test_review_page_exposes_local_person_fields(tagged_meeting_dir, tmp_meetings_dir):
    from src.event_kinds import local_roles_for
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    page = load_review_page("2026-02-04-council")

    # role options come from the meeting's event_kind ('council'), not a hardcoded list
    assert page.local_role_options == list(local_roles_for("council"))

    card = [c for c in page.all_cards if c.label == "SPEAKER_00"][0]
    assert card.local_slug is None
    assert card.has_local_person is False
    # prefilled from the speaker's name so the common path needs no typing
    assert card.default_slug == "mayor-johnson"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -v -k exposes_local_person`
Expected: FAIL — `AttributeError: 'ReviewPageData' object has no attribute 'local_role_options'`.

- [ ] **Step 3: Implement the dataclass changes**

In `gui/models.py`, add to `SpeakerCard` after `speaker_status`:

```python
    local_slug: Optional[str] = None
    local_role: Optional[str] = None
    default_slug: str = ""        # prefill for the make-local-person form
```

and add a property beside `is_linked`:

```python
    @property
    def has_local_person(self) -> bool:
        return bool(self.local_slug)
```

Add to `ReviewPageData` after `warnings`:

```python
    local_role_options: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Populate them**

In `gui/review_api.py`, in the `SpeakerCard(...)` call, after `speaker_status=...`:

```python
            local_slug=getattr(mapping, "local_slug", None) if mapping else None,
            local_role=getattr(mapping, "local_role", None) if mapping else None,
            default_slug=review.default_local_slug(v.current_name, v.label),
```

In the `ReviewPageData(...)` call, after `warnings=warnings`:

```python
        local_role_options=list(local_roles_for(meeting.event_kind)),
```

Add `from src.event_kinds import local_roles_for` to the module imports if absent. `meeting` is in scope at that point.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -v -k exposes_local_person`
Expected: PASS.

- [ ] **Step 6: Add the UI control**

Change the macro signature in `gui/templates/panels/_macros.html:1`:

```jinja
{% macro card(c, meeting_id, all_cards, local_role_options=[]) %}
```

The default keeps any other call site working. Update both call sites in `gui/templates/panels/review.html` (lines 36 and 42) to pass it:

```jinja
{{ m.card(c, page.meeting_id, page.all_cards, page.local_role_options) }}
```

In the identity group, after the `{% if c.is_linked %}…Unlink…{% endif %}` block:

```jinja
    {% if c.has_local_person %}
    <div class="local-person">
      <span class="localbadge">local: {{ c.local_slug }}{% if c.local_role %} · {{ c.local_role }}{% endif %}</span>
      <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/local-person/clear">
        <button type="submit" class="unlink">Clear local person</button>
      </form>
    </div>
    {% elif not c.is_linked %}
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/local-person"
          class="local-person">
      <input type="text" name="slug" value="{{ c.default_slug }}" required
             pattern="[a-z0-9][a-z0-9_-]{0,99}"
             title="lowercase letters, digits, hyphen or underscore">
      <input type="text" name="role" list="roles-{{ c.label }}" autocomplete="off"
             value="{{ local_role_options[0] if local_role_options else '' }}"
             placeholder="role…">
      <datalist id="roles-{{ c.label }}">
        {% for r in local_role_options %}<option value="{{ r }}"></option>{% endfor %}
      </datalist>
      <button type="submit">Not in essentials — make local person</button>
    </form>
    {% endif %}
```

The `{% elif not c.is_linked %}` guard matters: `assign_local_person` clears an essentials link, so offering the control on an already-linked speaker invites silent identity loss. The reviewer unlinks first, deliberately.

The `role` input is a datalist rather than a `<select>` so a custom role can be typed — the terminal advertises that and `test_apply_make_local_person_accepts_a_custom_role` covers it.

- [ ] **Step 7: Verify the page renders**

Start the GUI and load a meeting's review panel; confirm the control appears on an unlinked speaker, that submitting it shows `local: <slug> · <role>` with a Clear button, and that an already-linked speaker shows no control.

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 2088 passed, 3 skipped (2080 after Task 5, plus 4 from Task 7, 3 from Task 8, 1 here).

- [ ] **Step 8: Commit**

```bash
git add gui/models.py gui/review_api.py gui/templates/panels/_macros.html gui/templates/panels/review.html tests/test_gui_review.py
git commit -m "feat(gui): make-local-person control on the review card

SpeakerCard gains local_slug/local_role/default_slug and ReviewPageData gains
local_role_options, sourced from the meeting's event_kind rather than a
hardcoded list. The slug is prefilled from the speaker name so the common path
is pick-a-role-and-click, and the role input is a datalist so a custom role
stays possible — the terminal advertises that and it should not be the more
capable interface. The control is hidden for a speaker already linked to a
politician, since assigning would clear that link.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Republish `2026-02-04-council` and verify

**Files:** none — this is the end-to-end proof.

**Interfaces:**
- Consumes: everything above; `CA_0003` must be applied.

The meeting has 19 local people recorded locally (`public_comment` 13, `staff` 6) that have never reached the DB, because publish aborted on the old CHECK. Lisa Lehner (staff) is the only one of the 19 not already published by name, so hers is the single new name this adds.

- [ ] **Step 1: Confirm the preconditions**

`CA_0003` applied (Task 6 step 4), and the main checkout is on code including Tasks 1-5. Note the main checkout's local `main` carries unpushed commits and will not fast-forward — do not rebase it. Either run from a worktree at `origin/main` with `.env.local` symlinked in (verify `git check-ignore .env.local` first), or confirm the needed code is already present there.

- [ ] **Step 2: Republish**

```bash
cd ~/Documents/GitHub/on-the-record && ./.venv/bin/python run_local.py --publish-meeting 2026-02-04-council
```

Expected: completes without a `CheckViolation`, reporting segment and speaker counts.

- [ ] **Step 3: Verify the data**

```sql
-- 19 rows, all shapes valid, expected distribution
SELECT role, count(*) FROM meetings.local_people lp
 JOIN meetings.speakers sp ON sp.local_slug = lp.slug
 JOIN meetings.meetings m ON m.id = sp.meeting_id
 WHERE m.slug = '2026-02-04-council' GROUP BY role ORDER BY 2 DESC;
-- expect public_comment 13, staff 6

SELECT count(*) FROM meetings.local_people
 WHERE role IS NOT NULL AND role !~ '^[a-z][a-z0-9_]{0,39}$';
-- expect 0

SELECT count(*) FILTER (WHERE politician_id IS NOT NULL AND local_slug IS NOT NULL)
 FROM meetings.speakers;
-- expect 0 — no speaker carries two identities

SELECT slug, role FROM meetings.local_people WHERE slug = 'kathleen-donham';
-- expect ('kathleen-donham','moderator') — untouched
```

- [ ] **Step 4: Verify nothing else drifted**

Check the meeting's summary sections still align after the republish — a known hazard on republished meetings, to be checked rather than assumed. Then load the live meeting page and confirm those speakers still render correctly.

- [ ] **Step 5: Record the outcome**

No commit. Report the counts, and update the memory note for `local-people-role-and-identity-gaps` to record that civic publishing is unblocked and which meeting proved it.

---

## Notes for the implementer

**Do Task 9 before Task 7's tests, or land them together.** Task 7's tests assert on `card.local_slug`, which Task 9 adds.

**Out of scope, deliberately** — do not expand into these:
- Rendering the role anywhere on the public site. The display question is open.
- A closed role enum. Rejected: it decides the display question and breaks tested free-text behaviour.
- Transcript-level de-identification of public commenters. Their names are already published via `speakers.display_name`; withholding the role label buys no privacy. Separate project.
- The unguarded `meetingsService` LEFT JOIN on `local_people`. Harmless now only because publish no longer creates dual identities. Worth fixing, not here.
- `speaker_status` transitions in `assign_local_person`. Promoting an unidentified handle is a different transition with its own rules.

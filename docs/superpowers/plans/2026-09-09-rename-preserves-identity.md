# Rename Preserves Identity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fixing a typo in the review card's *Also → Display name* box must stop deleting the local person or roster link shown one line above it.

**Architecture:** The clearing stays in `src.review.rename_speaker`, which the terminal review depends on (there, rename *is* the identity flow). A new sibling function `src.review.rename_preserving_identity` snapshots the four identity fields, calls `rename_speaker`, and restores them verbatim when the speaker had an identity. The GUI route's `gui.review_api.apply_rename` calls the new function; the terminal keeps calling the old one.

**Tech Stack:** Python 3.14 (`/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`), pytest, FastAPI + `starlette.testclient.TestClient`, Jinja2 templates, hand-written CSS.

**Spec:** `docs/superpowers/specs/2026-09-09-rename-preserves-identity-design.md`

## Global Constraints

- Run Python only via `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`. This worktree has no `.venv` of its own; the one in the main checkout is the project venv. Never use system `python3`.
- **Baseline recorded before any change: `2463 passed, 3 skipped, 1 warning`, exit 0.** Every task ends with a full-suite run; the pass count may only go up.
- Do **not** modify `src.review.rename_speaker`. Branch `claude/great-allen-6f0746` is editing that function; this work must stay additive so the two merge cleanly.
- Do not weaken the one-identity-per-speaker invariant (ev-accounts migration 623) enforced in `src.review.link_speaker` and `src.review.assign_local_person`. A preserved identity must still be exactly one identity.
- Exact copy for the card's new line, verbatim: `Saving a name here keeps the current identity — use the chooser above to change who this is.` (em dash, not a hyphen).
- The new CSS class is `.ident-note`. The existing `.ident-cost` class stays in `style.css`; it is still used by the identity panels.

---

### Task 1: The route stops destroying the identity

The failing test comes first and goes through the **real HTTP route**, because that is the path the curator actually uses and the only one that proves the bug.

**Files:**
- Modify: `src/review.py` — add `rename_preserving_identity` immediately after `rename_speaker` (which ends at `src/review.py:220`, just before the `MergeResult` dataclass)
- Modify: `gui/review_api.py:156-172` — `apply_rename`
- Test: `tests/test_gui_review.py` (append at end of file)

**Interfaces:**
- Consumes: `src.review.rename_speaker(mappings, segments, label, new_name, *, roster=None) -> RenameResult`; `src.review.RenameResult(label, old_name, new_name, alias_suggestion)`
- Produces: `src.review.rename_preserving_identity(mappings, segments, label: str, new_name: str, *, roster=None) -> RenameResult` — same signature, same return type, used by Task 2's tests.

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_gui_review.py`. `apply_make_local_person`, `apply_link`, `_card_for`, `_write_meeting`, `tagged_meeting_dir`, `create_app` and `TestClient` are all already imported/defined at module level in this file.

```python
def test_renaming_through_the_route_keeps_a_local_person(
        tagged_meeting_dir, tmp_meetings_dir):
    """THE BUG. The card has two name fields. The local-person panel's Name
    renames then assigns, so the identity survives. The Also block's Display
    name box posted to /name -> apply_rename -> rename_speaker, which nulls
    local_slug/local_role on any changed name — so fixing one letter DELETED
    the local person shown one line above it on the same card.

    PR #203 only warned about this. Renaming is a name operation; the card
    already has a separate, explicit control for every identity outcome."""
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_make_local_person("2026-02-04-council", "SPEAKER_01",
                                   "frank-oconnor", "public_comment",
                                   name="Frank OConner") is True

    client = TestClient(create_app())
    resp = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/name",
                       data={"name": "Frank O'Connor"}, follow_redirects=False)
    assert resp.status_code == 303

    card = _card_for("2026-02-04-council", "SPEAKER_01")
    assert card.name == "Frank O'Connor"      # the typo IS fixed
    assert card.identity_kind == "local"      # and the person is still there
    assert card.local_slug == "frank-oconnor"
    assert card.local_role == "public_comment"


def test_renaming_through_the_route_keeps_a_roster_link(
        tagged_meeting_dir, tmp_meetings_dir):
    """The same deletion, on the other identity kind: a changed name nulled
    politician_slug/politician_id whenever no roster was loadable."""
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_link("2026-02-04-council", "SPEAKER_01", "", "uuid-becerra",
                      name="Xavier Becera") is True

    client = TestClient(create_app())
    resp = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/name",
                       data={"name": "Xavier Becerra"}, follow_redirects=False)
    assert resp.status_code == 303

    card = _card_for("2026-02-04-council", "SPEAKER_01")
    assert card.name == "Xavier Becerra"
    assert card.identity_kind == "roster"
    assert card.politician_id == "uuid-becerra"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k "renaming_through_the_route" -v
```

Expected: both FAIL. The local-person one on `assert card.identity_kind == "local"` (it is `"none"`, because `local_slug`/`local_role` were nulled); the roster one on `assert card.identity_kind == "roster"` (`politician_id` was nulled — `body_slug="x"` is not a loadable roster, so `_load_roster_for` returns `None` and `rename_speaker` takes its no-roster branch).

- [ ] **Step 3: Add `rename_preserving_identity` to `src/review.py`**

Insert immediately after `rename_speaker` returns (before the `@dataclass class MergeResult` block). Do not edit `rename_speaker` itself.

```python
def rename_preserving_identity(mappings, segments, label: str, new_name: str, *,
                               roster=None) -> RenameResult:
    """Rename a speaker WITHOUT disturbing an identity it already holds.

    rename_speaker treats a changed name as authoritative over any prior
    identity and drops it (see its own comment). That is right for the TERMINAL
    review, where rename IS the identity flow: run_local renames, then offers
    _prompt_link_politician / _prompt_create_local_person — and the link offer is
    only reachable because the link was cleared, since it returns immediately
    when politician_slug or politician_id is set (run_local.py:2979).

    It is wrong for the GUI review card, which carries a separate, explicit
    control for every identity outcome. There the Display name box was the only
    control whose name did not say what it would do: fixing one letter deleted
    the local person or roster link shown one line above it.

    So the identity is snapshotted and restored verbatim — but only when the
    speaker HAD one. Two things follow from restoring verbatim rather than
    re-deriving:

    - The one-identity-per-speaker invariant (ev-accounts migration 623) cannot
      break. The snapshot was exactly one identity when it was taken, so it is
      exactly one identity when it is put back; no new code has to re-enforce
      what link_speaker and assign_local_person enforce.
    - A rename cannot fail. Re-applying through assign_local_person would
      re-validate the slug against LOCAL_SLUG_RE and could raise on a slug that
      is already stored but no longer passes, turning a name edit into a 500.

    When the speaker had NO identity, rename_speaker's result stands untouched,
    so a roster-derived link for a freshly typed name still attaches.

    For an `unidentified` speaker this also fixes an enrollment bug rather than
    only preserving one: local_slug there is the synthetic
    unidentified-<meeting>-<label> handle whose whole purpose is keeping two
    distinct unknown speakers off one enrollment key. Nulling it dropped
    resolve_mapping_enrollment back to the name, silently merging unrelated
    strangers — the collision clear_local_person refuses to cause.
    """
    mapping = mappings.get(label)
    snapshot = None
    if mapping is not None and (mapping.politician_slug or mapping.politician_id
                                or mapping.local_slug or mapping.local_role):
        snapshot = (mapping.politician_slug, mapping.politician_id,
                    mapping.local_slug, mapping.local_role)

    result = rename_speaker(mappings, segments, label, new_name, roster=roster)

    if snapshot is not None:
        renamed = mappings[label]
        (renamed.politician_slug, renamed.politician_id,
         renamed.local_slug, renamed.local_role) = snapshot
    return result
```

- [ ] **Step 4: Point `apply_rename` at it**

In `gui/review_api.py`, replace the `apply_rename` docstring and its `review.rename_speaker(...)` call:

```python
def apply_rename(meeting_id: str, label: str, new_name: str) -> bool:
    """Rename a speaker (human-authoritative) and persist. Returns False on
    unsafe/unknown meeting, unknown label, or empty name (caller maps to 404/no-op).

    Uses rename_preserving_identity, NOT rename_speaker: this is the GUI's
    name-only path. The review card has a separate, explicit control for every
    identity outcome, so nothing here needs the rename to change one — and
    dropping the identity meant a curator fixing a typo silently deleted the
    local person or roster link shown one line above the box. The terminal
    review, which has no such controls and relies on the clearing to reach its
    re-link prompt, still calls rename_speaker directly.

    The roster is still passed: it normalises the typed name, and on a speaker
    with no identity at all it still derives a link from the new name.
    """
    name = (new_name or "").strip()
    if not name:
        return False
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, roster = ctx

    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False

    from src import review
    review.rename_preserving_identity(meeting.speakers, meeting.segments, label, name,
                                      roster=roster)
    persist_review(meeting, meeting_dir)
    return True
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k "renaming_through_the_route" -v
```

Expected: 2 passed.

- [ ] **Step 6: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2465 passed, 3 skipped`, exit 0. No failures. If anything fails, it is a real regression — fix it before committing, do not adjust the assertion.

- [ ] **Step 7: Commit**

```bash
git add src/review.py gui/review_api.py tests/test_gui_review.py
git commit -m "fix(review): renaming through the GUI keeps the speaker's identity

The Also block's Display name box posted to /name -> apply_rename ->
rename_speaker, which nulls local_slug/local_role/politician_* on any
changed name. Fixing a typo deleted the local person or roster link shown
one line above the box. PR #203 only warned about it.

rename_speaker keeps its semantics: the terminal review has no separate
identity controls and reaches _prompt_link_politician only because the
link was cleared. The GUI card has an explicit control for every outcome,
so its route now calls a new rename_preserving_identity, which snapshots
the four identity fields and restores them verbatim when the speaker had
an identity. Verbatim restore cannot break migration 623's one-identity
invariant and cannot fail.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Pin the enrollment consequence and the terminal contract

Task 1 proves the identity survives. This task proves the two things that make surviving *safe*: the voice profile still keys to the right person, and the terminal path did not move.

**Files:**
- Test: `tests/test_rename_clears_stale_link.py` (append; also extend the module docstring)
- Test: `tests/test_gui_review.py` (append at end of file)

**Interfaces:**
- Consumes: `src.review.rename_preserving_identity` from Task 1; `src.enroll.resolve_mapping_enrollment(mapping, roster=None) -> tuple[str, str | None, str | None]`; `gui.review_api._load_meeting_ctx(meeting_id) -> tuple[Meeting, Path, Roster | None] | None`
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Extend the module docstring of `tests/test_rename_clears_stale_link.py`**

Replace the closing line of the existing docstring so the file says which path it pins. The current docstring ends `...wrote each voice into the wrong person's profile.` — append a paragraph before the closing `"""`:

```
This file pins rename_speaker, which is the TERMINAL review's path
(run_local.py:3300, 3324). The GUI route deliberately does NOT behave this way:
it calls rename_preserving_identity, whose contrasting cases are at the bottom
of this file. If a change makes the tests above pass only by moving the terminal
onto the preserving behaviour, that is the regression, not the fix.
```

- [ ] **Step 2: Write the unit tests**

Append to `tests/test_rename_clears_stale_link.py`:

```python
# --- The GUI's path: rename_preserving_identity. The contrast with every test
# --- above is the point — same rename, opposite treatment of the identity.

from src.enroll import resolve_mapping_enrollment
from src.review import rename_preserving_identity


def test_preserving_rename_keeps_a_roster_link_a_plain_rename_would_drop():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Hopi Stosburg",
        politician_slug="hopi-h-stosberg", politician_id="uuid-stosberg")}
    rename_preserving_identity(mappings, [], "S0", "Hopi Stosberg", roster=None)
    m = mappings["S0"]
    assert m.speaker_name == "Hopi Stosberg"
    assert m.politician_slug == "hopi-h-stosberg"
    assert m.politician_id == "uuid-stosberg"


def test_preserving_rename_keeps_a_local_person():
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Frank OConner",
        local_slug="frank-oconnor", local_role="public_comment")}
    rename_preserving_identity(mappings, [], "S0", "Frank O'Connor", roster=None)
    m = mappings["S0"]
    assert m.local_slug == "frank-oconnor"
    assert m.local_role == "public_comment"
    # Still exactly ONE identity (ev-accounts migration 623).
    assert m.politician_slug is None and m.politician_id is None


def test_a_preserved_link_still_enrolls_under_the_linked_person():
    """THE HAZARD rename_speaker's comment names: resolve_mapping_enrollment keys
    on politician_id ahead of the name, so a link that survives a rename decides
    whose voice profile this speaker's embedding joins. For a spelling fix that
    is exactly right — the person the curator deliberately linked. Asserted
    directly rather than inferred from the rename, because the rename passing
    says nothing about which profile the voice lands in."""
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Isak Nti Asari",
        politician_slug="isak-nti-asare", politician_id="uuid-asare")}
    rename_preserving_identity(mappings, [], "S0", "Isak Nti Asare", roster=_roster())

    key, slug, pid = resolve_mapping_enrollment(mappings["S0"])
    assert key == "essentials:uuid-asare"   # the linked person, not a name slug
    assert (slug, pid) == ("isak-nti-asare", "uuid-asare")


def test_a_preserving_rename_does_not_relink_to_a_roster_lookalike():
    """The preserved link must WIN over rename_speaker's roster re-derivation.
    Renaming a speaker who is linked to Asare to the roster name 'Hopi Stosberg'
    re-derives uuid-stosberg inside rename_speaker; the snapshot must put the
    deliberate link back, so enrollment cannot silently move person."""
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Isak Nti Asare",
        politician_slug="isak-nti-asare", politician_id="uuid-asare")}
    rename_preserving_identity(mappings, [], "S0", "Hopi Stosberg", roster=_roster())
    assert mappings["S0"].politician_id == "uuid-asare"
    assert resolve_mapping_enrollment(mappings["S0"])[0] == "essentials:uuid-asare"


def test_a_preserving_rename_keeps_an_unidentified_handle():
    """local_slug on an unidentified speaker is the synthetic
    unidentified-<meeting>-<label> handle whose whole purpose is keeping two
    distinct unknown speakers off one enrollment key. Nulling it dropped
    resolve_mapping_enrollment back to the name, merging unrelated strangers —
    the collision clear_local_person refuses to cause. So this is a fix, not
    merely a preservation."""
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Unidentified Speaker",
        local_slug="unidentified-2026-02-04-council-s0",
        speaker_status="unidentified")}
    rename_preserving_identity(mappings, [], "S0", "Man in the red jacket", roster=None)
    m = mappings["S0"]
    assert m.local_slug == "unidentified-2026-02-04-council-s0"
    assert resolve_mapping_enrollment(m)[0] == "local:unidentified-2026-02-04-council-s0"


def test_a_speaker_with_no_identity_still_gets_one_from_the_roster():
    """The guard on the snapshot. Restoring unconditionally would wipe the link
    rename_speaker derives for a freshly typed roster name — a real feature, and
    the only way a GUI rename attaches an identity on its own."""
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Unknown")}
    rename_preserving_identity(mappings, [], "S0", "Isak Nti Asare", roster=_roster())
    assert mappings["S0"].politician_id == "uuid-asare"
    assert mappings["S0"].politician_slug == "isak-nti-asare"


def test_a_preserving_rename_still_renames_the_segments():
    """Everything rename_speaker does to the NAME must be untouched — the
    snapshot restores identity fields only."""
    from src.models import Segment

    segs = [Segment(segment_id=0, start_time=0.0, end_time=5.0,
                    speaker_label="S0", text="hi", speaker_name="Old Name")]
    mappings = {"S0": SpeakerMapping(
        speaker_label="S0", speaker_name="Old Name", local_slug="old-name",
        local_role="staff")}
    res = rename_preserving_identity(mappings, segs, "S0", "New Name", roster=None)
    assert segs[0].speaker_name == "New Name"
    assert mappings["S0"].id_method == "human_review"
    assert mappings["S0"].confidence == 1.0
    assert res.alias_suggestion == "Old Name"
```

- [ ] **Step 3: Write the route-level enrollment test**

Append to `tests/test_gui_review.py`. `_load_meeting_ctx` is already imported at module level (line 273).

```python
def test_a_route_rename_leaves_the_voice_keyed_to_the_linked_person(
        tagged_meeting_dir, tmp_meetings_dir):
    """The constraint the preserving rename has to answer for end to end: after
    a typo fix through the real route, the speaker's embedding must still enroll
    under the politician the curator linked — not a name-derived local slug, and
    not somebody else."""
    from src.enroll import resolve_mapping_enrollment

    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_link("2026-02-04-council", "SPEAKER_01", "", "uuid-becerra",
                      name="Xavier Becera") is True

    client = TestClient(create_app())
    client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/name",
                data={"name": "Xavier Becerra"}, follow_redirects=False)

    meeting, _meeting_dir, _roster = _load_meeting_ctx("2026-02-04-council")
    key, slug, pid = resolve_mapping_enrollment(meeting.speakers["SPEAKER_01"])
    assert key == "essentials:uuid-becerra"
    assert pid == "uuid-becerra"
```

- [ ] **Step 4: Run the new tests**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_rename_clears_stale_link.py tests/test_gui_review.py -k "preserv or keyed_to_the_linked or no_identity_still_gets" -v
```

Expected: all PASS against Task 1's implementation. These are guard tests, not drivers — Task 1's failing route tests were the drivers. If any FAILS, the Task 1 implementation is wrong; fix `src/review.py`, not the test.

- [ ] **Step 5: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2473 passed, 3 skipped`, exit 0.

- [ ] **Step 6: Commit**

```bash
git add tests/test_rename_clears_stale_link.py tests/test_gui_review.py
git commit -m "test(review): pin the enrollment key and the terminal rename contract

resolve_mapping_enrollment keys on politician_id ahead of the name, so a
link that survives a rename decides whose voice profile the embedding
joins. Asserted directly, unit and through the route.

Also pins the contrast: rename_speaker still clears and re-derives, so a
later change cannot quietly move the terminal review onto the preserving
behaviour and lose its re-link prompt. And the unidentified handle now
survives a rename, which stops resolve_mapping_enrollment falling back to
the name and merging unrelated strangers.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Correct the card — the warning becomes true, the prefill returns

**Files:**
- Modify: `gui/templates/panels/_macros.html:211-229` (the `class="rename"` form)
- Modify: `gui/static/style.css:52-55` (stale comment) and `:210-211` (add `.ident-note` beside `.ident-cost`)
- Test: `tests/test_gui_review.py:1873-1890` (rewrite `test_the_also_rename_box_warns_only_when_an_identity_would_be_dropped`)

**Interfaces:**
- Consumes: `SpeakerCard.identity_kind` (`'roster' | 'local' | 'unidentified' | 'non_speaker' | 'none'`) and `SpeakerCard.name` (`str | None`) from `gui/models.py`
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Rewrite the render test**

Replace the whole of `test_the_also_rename_box_warns_only_when_an_identity_would_be_dropped` (`tests/test_gui_review.py:1873-1890`) with:

```python
def test_the_also_rename_box_says_the_identity_survives_and_prefills(
        tagged_meeting_dir, tmp_meetings_dir):
    """PR #203 gave this box an amber warning — "Saving a different name here
    drops the current identity." — and stripped its value= prefill, because a
    changed name really did null local_slug/local_role/politician_*. The route
    now preserves the identity, so BOTH mitigations are wrong: a warning about
    something that no longer happens is worse than no warning, and the blank box
    made a one-letter fix mean retyping the whole name.

    The line stays on the same `identity_kind != 'none'` condition, which is
    accurate for all four kinds: rename never touched speaker_status either, so
    a marked card keeps its mark too."""
    body = _linked_body(tagged_meeting_dir)
    old = "Saving a different name here drops the current identity."
    new = ("Saving a name here keeps the current identity — "
           "use the chooser above to change who this is.")

    linked = _card_html(body, "SPEAKER_00")   # roster-linked, named "Mayor Johnson"
    assert old not in linked
    assert new in linked
    assert 'value="Mayor Johnson"' in _rename_form(linked)   # edit one letter

    plain = _card_html(body, "SPEAKER_01")    # no identity, no name
    assert new not in plain                   # nothing to reassure about
    assert 'value=""' in _rename_form(plain)  # nameless card: still a blank box
```

The slicing helper goes immediately above that test. It is load-bearing: the
local-person panel ALSO renders `name="name" value="Mayor Johnson"` (pinned by
`test_the_local_person_panel_asks_for_a_name_and_a_role`), so an assertion on the
whole card would pass on that input and prove nothing about the rename box.

```python
def _rename_form(card_html):
    """The Also block's rename form, sliced out of one card's HTML.

    The local-person panel carries its own `name="name"` input with the same
    prefill, so a `value="..."` assertion against the whole card would be
    satisfied by that one instead — passing whether or not the rename box
    prefills at all."""
    parts = card_html.split('class="rename"', 1)
    assert len(parts) == 2, "no rename form in this card"
    return parts[1].split("</form>", 1)[0]
```

- [ ] **Step 2: Run it to verify it fails**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k "also_rename_box" -v
```

Expected: FAIL on `assert old not in linked` — the old warning is still rendered.

- [ ] **Step 3: Rewrite the rename form in the template**

In `gui/templates/panels/_macros.html`, replace the block from `<form method="post" ... class="rename">` through its closing `</form>` (currently lines 211-229) with:

```html
      <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/name" class="rename">
        <label>Display name
          {# Prefilled, and safe to prefill. This posts to /name -> apply_rename
             -> rename_preserving_identity, which keeps local_slug/local_role/
             politician_slug/politician_id across the rename. PR #203 stripped
             the prefill and added an amber warning because rename_speaker used
             to null all four, putting a real identity one typo-fix away from
             silent deletion; the route no longer does that, so a blank box
             would only mean retyping a whole name to change one letter.
             `c.name or ''` because name is None on an unnamed speaker and the
             placeholder must not be submitted as a real name. #}
          <input type="text" name="name" value="{{ c.name or '' }}"
                 placeholder="Type a name…" autocomplete="off"></label>
        {% if c.identity_kind != 'none' %}
        {# Not a cost: a statement of what the save will and won't touch, so a
           curator who wants to change WHO this is goes to the chooser instead
           of expecting the name box to do it. True for all four kinds — the
           rename leaves speaker_status alone too, so a marked card keeps its
           mark. #}
        <p class="ident-note">Saving a name here keeps the current identity — use the chooser above to change who this is.</p>
        {% endif %}
        <button type="submit">Save</button>
      </form>
```

- [ ] **Step 4: Add the `.ident-note` style**

In `gui/static/style.css`, immediately after the `.ident-cost` rule (line 210-211), add:

```css
/* Neutral sibling of .ident-cost, for the rename box's "keeps the current
   identity" line: it reports what a save leaves alone, so it must not wear
   the amber warning colour that means "this will destroy something". */
.ident-note { margin: 0; font-size: 0.8rem; color: #6b7480; }
```

And update the now-stale comment at lines 52-55 so it names the class actually on that row:

```css
/* The rename form carries an .ident-note line (it used to be .ident-cost)
   alongside its label and button; let that row wrap onto its own line instead
   of forcing the card wider. */
.actions .rename { flex-wrap: wrap; }
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k "also_rename_box" -v
```

Expected: PASS.

- [ ] **Step 6: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2473 passed, 3 skipped`, exit 0. Watch for other render tests that asserted `'name="name" value=""'` on a NAMED card — if one fails, it was pinning the PR #203 mitigation and its assertion needs the same correction, with a docstring saying why.

- [ ] **Step 7: Verify the rendered page in a browser**

Start the GUI and look at a review page with a roster-linked or local speaker: the Display name box shows the current name, and the grey line below it reads the new sentence. Confirm the identity pill in the card head is unchanged after saving a one-letter edit.

`.claude/launch.json` already has a `gui` entry on port 8000, but its `runtimeExecutable` is the relative `.venv/bin/python`, which does not resolve in this worktree — point it at `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python` for the run (do not commit that absolute path). Then `mcp__Claude_Browser__preview_start` with `{"name": "gui"}`.

`~/CouncilScribe/meetings` holds 181 processed meetings, so a review page is available; open one whose card shows a `roster` or `local` pill. Take a screenshot of that card as evidence. If the server will not start or no card has an identity, say so plainly rather than claiming a visual check that did not happen — the render test in Step 5 is then the only evidence, and the report must say that.

- [ ] **Step 8: Commit**

```bash
git add gui/templates/panels/_macros.html gui/static/style.css tests/test_gui_review.py
git commit -m "fix(review): the rename box now tells the truth, and prefills again

PR #203's amber warning described a deletion that no longer happens, and
a warning about nothing is worse than no warning. It becomes a neutral
.ident-note line saying the save keeps the identity and pointing at the
chooser for changing who the speaker is.

The value= prefill returns for the same reason: it was stripped to keep a
real identity out of one keystroke's reach, and fixing one letter should
not mean retyping the name.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Correct the identity-picker spec

Two paragraphs in the September 8 spec are now false. PR #203 already corrected an inaccurate claim in this same section; whatever replaces them must leave the section true.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-08-speaker-identity-picker-design.md:148-169` (section "The name travels with the identity")

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Read the section as it stands**

```bash
sed -n '119,170p' docs/superpowers/specs/2026-09-08-speaker-identity-picker-design.md
```

Note what must stay true: the lines above (119-146) describe `_reset_and_rename`'s clear-status -> rename -> assign ordering. That ordering is **unchanged** — `_reset_and_rename` still calls `rename_speaker` directly and still depends on its clearing. Do not touch lines 119-146.

- [ ] **Step 2: Replace the two false paragraphs**

Replace everything from `The **Display name** box in the *Also* block stays.` through the end of the paragraph beginning `The fix:` (lines 148-169) with:

```markdown
The **Display name** box in the *Also* block stays. It is the general escape
hatch — the only way to name a roster-linked or marked speaker.

At the time this spec was written the box was a hazard: it posts to `/name` ->
`apply_rename` -> `rename_speaker`, which on a CHANGED name nulls
`local_slug`/`local_role`/`politician_slug`/`politician_id`. Fixing a typo
through it silently deleted the local person or roster link shown one line
above. This spec's mitigation — dropping the `value=` prefill and adding an
amber "Saving a different name here drops the current identity." line — narrowed
the blast radius without closing the hole.

**Superseded 2026-09-09** by
`2026-09-09-rename-preserves-identity-design.md`. `apply_rename` now calls
`review.rename_preserving_identity`, which snapshots the four identity fields,
renames, and restores them verbatim when the speaker had an identity. Renaming
through the GUI is a name-only operation; changing WHO a speaker is goes through
the chooser above. `rename_speaker` itself is unchanged, because the terminal
review has no chooser — there, rename is the identity flow, and clearing the
link is what makes `_prompt_link_politician` reachable at all. Both mitigations
are therefore reverted: the box prefills again, and the amber line is replaced
by a neutral `.ident-note` reading "Saving a name here keeps the current
identity — use the chooser above to change who this is."

The local-person panel's Name field is unchanged: it posts to `/local-person` ->
`apply_make_local_person`, which assigns the local identity together with the
name in one write via `_reset_and_rename` (clear status -> rename -> assign),
and that path still calls `rename_speaker` directly. The panel labels its input
`Name` and says it is the name readers will see, because for a local person that
name is the published one.
```

- [ ] **Step 3: Check the section reads true end to end**

```bash
sed -n '119,175p' docs/superpowers/specs/2026-09-08-speaker-identity-picker-design.md
```

Confirm: the `_reset_and_rename` ordering paragraphs still describe live behaviour; nothing in the section still claims the Display name box drops an identity; the cross-reference filename matches the file actually committed.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-08-speaker-identity-picker-design.md
git commit -m "docs(spec): the Display name box no longer drops the identity

Marks the two paragraphs describing PR #203's mitigation as superseded and
points at the 2026-09-09 spec. The _reset_and_rename ordering rules above
them are untouched: that path still calls rename_speaker directly and
still depends on its clearing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full suite: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q` — expect `2473 passed, 3 skipped`, exit 0, against the `2463 passed, 3 skipped` baseline.
- [ ] `grep -rn "rename_speaker" src/ gui/ run_local.py` — confirm `gui/review_api.py` calls it only from `_reset_and_rename`, and `run_local.py` still calls it at both terminal sites.
- [ ] `git diff main --stat` — confirm `src/review.py`'s diff is purely additive (a new function), with no change inside `rename_speaker`.
- [ ] `grep -rn "drops the current identity" gui/ docs/ tests/` — the only survivors should be historical notes that say so in the past tense.

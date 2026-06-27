# `--relink-person` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-interactive `run_local.py --relink-person "<name>"` subcommand that links a speaker to an essentials politician across every meeting they appear in, re-keys their voice profile (cheap fold, no audio), and re-publishes the affected meetings.

**Architecture:** A new focused pure module `src/relink.py` (resolve target, relink mappings, re-key profile) composed by a thin I/O orchestrator `_relink_person` in `run_local.py`, dispatched like the existing `--fix-transcripts` / `--publish-meeting` maintenance subcommands. Pure logic is unit-tested; the orchestrator is smoke-tested via `--dry-run`.

**Tech Stack:** Python, pytest. Reuses `src/review.link_speaker`, `src/enroll.promote_unidentified_handle`, `src/essentials_client.search_politicians`, `src/publish.publish_meeting`, `src/models.Meeting`.

**Spec:** `docs/superpowers/specs/2026-06-26-relink-person-design.md`

---

## Conventions (this repo)

- **Python:** run everything via `.venv/bin/python` — never system `python3`. Tests: `.venv/bin/python -m pytest <args>`.
- **Branch:** `feat/relink-person` (already created off `main`).
- `Meeting(meeting_id, city, date, ...)` — `meeting_id`, `city`, `date` are required; `speakers: dict[str, SpeakerMapping]`.
- Transcript writer convention: `json.dump(meeting.to_dict(), f, indent=2)`.
- `search_politicians(q, *, limit=10, base_url=None) -> list[dict]`; each dict has `politician_id`, `politician_slug`, `full_name`, `office_title`, `district_label`, `is_incumbent`, `government_name`.

## File Structure

- **Create** `src/relink.py` — `ResolvedTarget`, `RelinkAmbiguous`, `relink_in_meeting`, `resolve_link_target`, `rekey_profile_for_link`. One responsibility: the pure relink logic.
- **Create** `tests/test_relink.py` — unit tests for all of the above.
- **Modify** `run_local.py` — add the `--relink-person` argparse group, the `_relink_person(args)` orchestrator, a `_trigger_render_deploy()` helper, and the dispatch block.

---

## Task 1: `relink_in_meeting` + result/error types (TDD)

**Files:**
- Create: `src/relink.py`
- Create: `tests/test_relink.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_relink.py`:

```python
from __future__ import annotations

from src.models import Meeting, SpeakerMapping
from src.relink import relink_in_meeting


def _meeting(speakers: dict[str, SpeakerMapping]) -> Meeting:
    return Meeting(meeting_id="m1", city="Bloomington", date="2026-04-01", speakers=speakers)


def test_relink_matches_by_name_case_insensitive_and_sets_both_fields():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="steve hilton")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug == "steve-hilton"


def test_relink_sets_id_when_slug_is_none():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Steve Hilton")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", None)
    assert changed == ["SPEAKER_00"]
    assert m.speakers["SPEAKER_00"].politician_id == "uuid-hilton"
    assert m.speakers["SPEAKER_00"].politician_slug is None


def test_relink_no_match_returns_empty_and_leaves_mappings_untouched():
    m = _meeting({"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Jane Doe")})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == []
    assert m.speakers["SPEAKER_00"].politician_id is None


def test_relink_already_linked_is_noop():
    m = _meeting({"SPEAKER_00": SpeakerMapping(
        speaker_label="SPEAKER_00", speaker_name="Steve Hilton",
        politician_id="uuid-hilton", politician_slug="steve-hilton",
    )})
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert changed == []


def test_relink_matches_multiple_labels_for_same_person():
    m = _meeting({
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="Steve Hilton"),
        "SPEAKER_03": SpeakerMapping(speaker_label="SPEAKER_03", speaker_name="Steve Hilton"),
    })
    changed = relink_in_meeting(m, "Steve Hilton", "uuid-hilton", "steve-hilton")
    assert sorted(changed) == ["SPEAKER_00", "SPEAKER_03"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_relink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.relink'`.

- [ ] **Step 3: Create `src/relink.py` with the core types + `relink_in_meeting`**

```python
"""Non-interactive relink of a speaker to an essentials politician.

Pure logic backing `run_local.py --relink-person`: resolve a target politician,
set the link on matching speaker mappings, and fold the person's voice profile
onto the id key. No file or network I/O except the essentials name search in
resolve_link_target. The orchestrator in run_local.py does the file/DB I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.essentials_client import search_politicians


@dataclass
class ResolvedTarget:
    politician_id: str
    politician_slug: Optional[str]
    full_name: str


class RelinkAmbiguous(Exception):
    """A name query resolved to zero or several politicians; caller must pick."""

    def __init__(self, query: str, candidates: list[dict]):
        self.query = query
        self.candidates = candidates
        super().__init__(
            f"'{query}' matched {len(candidates)} politicians; pass --to-id to disambiguate"
        )


def relink_in_meeting(meeting, speaker_name, politician_id, politician_slug) -> list[str]:
    """Set politician identity on every mapping whose name matches speaker_name.

    Returns the labels actually changed (case-insensitive name match; skips
    mappings already linked to the same id+slug). Mutates meeting.speakers in
    place via review.link_speaker. Segments carry no politician fields, so
    publish derives them from these mappings — mappings are the only source.
    """
    from src.review import link_speaker

    want = speaker_name.strip().lower()
    changed: list[str] = []
    for label, mapping in list(meeting.speakers.items()):
        name = (mapping.speaker_name or "").strip().lower()
        if name != want:
            continue
        if mapping.politician_id == politician_id and mapping.politician_slug == politician_slug:
            continue  # already linked — no change
        link_speaker(meeting.speakers, label, politician_slug, politician_id)
        changed.append(label)
    return changed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_relink.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/relink.py tests/test_relink.py && git commit -m "$(cat <<'EOF'
feat(relink): relink_in_meeting core + result/error types

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `resolve_link_target` (TDD)

**Files:**
- Modify: `src/relink.py`
- Modify: `tests/test_relink.py`

- [ ] **Step 1: Add failing tests** (append to `tests/test_relink.py`)

```python
import pytest

from src.relink import RelinkAmbiguous, ResolvedTarget, resolve_link_target


def _cand(pid, slug, name):
    return {"politician_id": pid, "politician_slug": slug, "full_name": name,
            "office_title": "Candidate", "district_label": "", "is_incumbent": False,
            "government_name": ""}


def test_resolve_single_match(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [_cand("uuid-1", "steve-hilton", "Steve Hilton")])
    t = resolve_link_target("Steve Hilton")
    assert t == ResolvedTarget("uuid-1", "steve-hilton", "Steve Hilton")


def test_resolve_zero_matches_raises(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians", lambda q, **kw: [])
    with pytest.raises(RelinkAmbiguous) as ei:
        resolve_link_target("Nobody Here")
    assert ei.value.candidates == []


def test_resolve_multiple_matches_raises_with_candidates(monkeypatch):
    cands = [_cand("uuid-1", "a", "John Smith"), _cand("uuid-2", "b", "John Smith")]
    monkeypatch.setattr("src.relink.search_politicians", lambda q, **kw: cands)
    with pytest.raises(RelinkAmbiguous) as ei:
        resolve_link_target("John Smith")
    assert len(ei.value.candidates) == 2


def test_resolve_explicit_id_uses_search_record_for_display(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [_cand("uuid-1", "a", "Other"),
                                         _cand("uuid-2", "steve-hilton", "Steve Hilton")])
    t = resolve_link_target("Steve Hilton", explicit_id="uuid-2")
    assert t == ResolvedTarget("uuid-2", "steve-hilton", "Steve Hilton")


def test_resolve_explicit_id_tolerates_no_search_hit(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians", lambda q, **kw: [])
    t = resolve_link_target("Steve Hilton", explicit_id="uuid-9")
    assert t == ResolvedTarget("uuid-9", None, "Steve Hilton")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_relink.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_link_target'`.

- [ ] **Step 3: Implement `resolve_link_target`** (append to `src/relink.py`)

```python
def resolve_link_target(
    query: str, *, explicit_id: Optional[str] = None, base_url: Optional[str] = None
) -> ResolvedTarget:
    """Resolve a name (and/or explicit id) to a single essentials politician.

    With explicit_id: use it; look it up in the search results only to fill in
    a display slug/name (tolerating no hit). Without: require exactly one search
    match, else raise RelinkAmbiguous carrying the candidate list.
    """
    try:
        matches = search_politicians(query, base_url=base_url)
    except Exception:
        matches = []

    if explicit_id is not None:
        for m in matches:
            if m.get("politician_id") == explicit_id:
                return ResolvedTarget(explicit_id, m.get("politician_slug"), m.get("full_name") or query)
        return ResolvedTarget(explicit_id, None, query)

    if len(matches) == 1:
        m = matches[0]
        return ResolvedTarget(m["politician_id"], m.get("politician_slug"), m.get("full_name") or query)

    raise RelinkAmbiguous(query, matches)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_relink.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/relink.py tests/test_relink.py && git commit -m "$(cat <<'EOF'
feat(relink): resolve_link_target with ambiguity refusal

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `rekey_profile_for_link` (TDD)

**Files:**
- Modify: `src/relink.py`
- Modify: `tests/test_relink.py`

- [ ] **Step 1: Add failing tests** (append to `tests/test_relink.py`)

```python
import numpy as np

from src.enroll import EmbeddingRecord, ProfileDB, StoredProfile
from src.relink import rekey_profile_for_link


def _profile(key, name, pid=None, slug=None):
    return StoredProfile(
        speaker_id=key, display_name=name,
        embeddings=[EmbeddingRecord(np.array([1.0, 2.0]), "m1", 3)],
        meetings_seen=["m1"], total_segments_confirmed=3,
        politician_slug=slug, politician_id=pid,
    )


def test_rekey_folds_name_keyed_profile_into_essentials_id():
    db = ProfileDB(profiles={"hilton_steve": _profile("hilton_steve", "Steve Hilton")})
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug="steve-hilton",
                                 full_name="Steve Hilton")
    assert key == "essentials:uuid-hilton"
    assert "hilton_steve" not in db.profiles
    target = db.profiles["essentials:uuid-hilton"]
    assert target.politician_id == "uuid-hilton"
    assert target.politician_slug == "steve-hilton"
    assert len(target.embeddings) == 1  # embeddings carried over


def test_rekey_returns_none_when_no_source_profile():
    db = ProfileDB(profiles={})
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug=None,
                                 full_name="Steve Hilton")
    assert key is None
    assert db.profiles == {}


def test_rekey_noop_when_already_essentials_keyed():
    db = ProfileDB(profiles={
        "essentials:uuid-hilton": _profile("essentials:uuid-hilton", "Steve Hilton",
                                           pid="uuid-hilton", slug="steve-hilton"),
    })
    key = rekey_profile_for_link(db, "Steve Hilton",
                                 politician_id="uuid-hilton", politician_slug="steve-hilton",
                                 full_name="Steve Hilton")
    assert key == "essentials:uuid-hilton"
    assert len(db.profiles) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_relink.py -v`
Expected: FAIL — `ImportError: cannot import name 'rekey_profile_for_link'`.

- [ ] **Step 3: Implement `rekey_profile_for_link`** (append to `src/relink.py`)

```python
def rekey_profile_for_link(db, speaker_name, *, politician_id, politician_slug, full_name):
    """Fold the person's existing voice profile onto the essentials:<id> key.

    Finds the source profile by name-slug, then by exact display-name or an
    already-matching politician_id, and folds it via promote_unidentified_handle
    (carries embeddings/meetings, no audio). Returns the target key, or None
    when no source profile exists (the DB link still publishes regardless).
    """
    from src.enroll import _name_to_slug, promote_unidentified_handle

    target_key = f"essentials:{politician_id}"

    handle_key = None
    name_slug = _name_to_slug(speaker_name)
    if name_slug in db.profiles:
        handle_key = name_slug
    else:
        want = speaker_name.strip().lower()
        for k, p in db.profiles.items():
            if k == target_key:
                continue
            if p.politician_id == politician_id or (p.display_name or "").strip().lower() == want:
                handle_key = k
                break

    if handle_key is None:
        return target_key if target_key in db.profiles else None
    if handle_key == target_key:
        return target_key

    promote_unidentified_handle(
        db, handle_key, target_key,
        display_name=full_name, politician_slug=politician_slug, politician_id=politician_id,
    )
    return target_key
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_relink.py -v`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/relink.py tests/test_relink.py && git commit -m "$(cat <<'EOF'
feat(relink): rekey_profile_for_link — cheap fold onto essentials:<id>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `run_local.py` — flags, orchestrator, deploy helper, dispatch

**Files:**
- Modify: `run_local.py`

(No unit test for the orchestrator — it is file/DB/publish I/O, matching the codebase convention for `_fix_transcripts` / `_publish_meeting_standalone`. Verified via `--dry-run` smoke in Task 5.)

- [ ] **Step 1: Add the argparse flags**

In `run_local.py`, in the `# Utilities` argparse block (immediately after the `--merge-profiles` argument, ~line 3033):

```python
    parser.add_argument("--relink-person", metavar="NAME",
                        help="Link a speaker (by transcript name) to an essentials politician across "
                             "every meeting they appear in, re-key their voice profile, and re-publish")
    parser.add_argument("--to-id", metavar="POLITICIAN_ID",
                        help="Target essentials politician_id for --relink-person (skips name search)")
    parser.add_argument("--to-name", metavar="NAME",
                        help="Search essentials by this name instead of --relink-person's value")
    parser.add_argument("--meeting", metavar="MEETING_ID",
                        help="Restrict --relink-person to a single meeting")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview --relink-person changes without writing anything")
    parser.add_argument("--deploy", action="store_true",
                        help="After --relink-person publishes, trigger the Render web rebuild")
```

- [ ] **Step 2: Add the deploy helper + orchestrator**

In `run_local.py`, immediately after `_publish_meeting_standalone` (ends ~line 1862), add:

```python
def _trigger_render_deploy() -> None:
    """POST the Render deploy hook (RENDER_DEPLOY_HOOK_URL), loading .env.local if needed."""
    import os
    import urllib.request

    hook = os.environ.get("RENDER_DEPLOY_HOOK_URL")
    if not hook:
        env_path = ROOT / ".env.local"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("RENDER_DEPLOY_HOOK_URL="):
                    hook = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not hook:
        print("  --deploy: RENDER_DEPLOY_HOOK_URL not set; skipping deploy.")
        return
    try:
        with urllib.request.urlopen(hook, data=b"", timeout=30) as resp:
            print(f"  Render deploy triggered (HTTP {resp.status}).")
    except Exception as exc:  # noqa: BLE001 - best-effort deploy ping
        print(f"  --deploy failed: {exc}")


def _relink_person(args) -> None:
    """Link a person to an essentials politician across all their meetings."""
    from src import config
    from src.checkpoint import PipelineState
    from src.enroll import load_profiles, save_profiles
    from src.models import Meeting
    from src.relink import RelinkAmbiguous, relink_in_meeting, rekey_profile_for_link, resolve_link_target

    name = args.relink_person

    # 1. Resolve the target politician (refuse on ambiguity).
    try:
        target = resolve_link_target(args.to_name or name, explicit_id=args.to_id)
    except RelinkAmbiguous as exc:
        print(f"Could not resolve '{exc.query}' to a single politician.")
        if exc.candidates:
            print("Candidates — re-run with --to-id <uuid>:")
            for c in exc.candidates:
                line = f"  {c.get('politician_id')}  {c.get('full_name')}"
                extra = " ".join(x for x in (c.get("office_title"), c.get("district_label")) if x)
                print(f"{line} — {extra}" if extra else line)
        else:
            print("  No essentials matches. Pass --to-id <uuid> explicitly.")
        sys.exit(2)

    print(f"Target: {target.full_name}  id={target.politician_id}  "
          f"slug={target.politician_slug or '(none)'}")

    # 2. Find appearances and compute the relink (in memory).
    changed: list[tuple] = []  # (meeting_dir, Meeting, labels)
    dirs = sorted(d for d in config.MEETINGS_DIR.iterdir()
                  if d.is_dir() and not d.name.startswith("."))
    for mdir in dirs:
        if args.meeting and mdir.name != args.meeting:
            continue
        named = mdir / "transcript_named.json"
        if not named.exists():
            continue
        with open(named, "r", encoding="utf-8") as f:
            meeting = Meeting.from_dict(json.load(f))
        labels = relink_in_meeting(meeting, name, target.politician_id, target.politician_slug)
        if labels:
            changed.append((mdir, meeting, labels))

    if not changed:
        print(f"No meetings have an unlinked '{name}' speaker (nothing to do).")
        return

    print(f"\nWill relink '{name}' in {len(changed)} meeting(s):")
    for mdir, _meeting, labels in changed:
        print(f"  {mdir.name}: {', '.join(labels)}")

    if args.dry_run:
        print("\n(dry run — no transcript, profile, publish, or deploy writes)")
        print(f"  would fold the '{name}' voice profile into essentials:{target.politician_id}")
        print(f"  would re-publish: {', '.join(m.name for m, _x, _y in changed)}")
        print(f"  would deploy: {'yes' if args.deploy else 'no'}")
        return

    # 3. Persist the edited transcripts.
    for mdir, meeting, _labels in changed:
        with open(mdir / "transcript_named.json", "w", encoding="utf-8") as f:
            json.dump(meeting.to_dict(), f, indent=2)
    print(f"  Saved {len(changed)} transcript(s).")

    # 4. Re-key the voice profile (cheap fold, no audio).
    db = load_profiles()
    key = rekey_profile_for_link(
        db, name,
        politician_id=target.politician_id, politician_slug=target.politician_slug,
        full_name=target.full_name,
    )
    if key:
        save_profiles(db)
        print(f"  Profile re-keyed -> {key}")
    else:
        print(f"  No existing voice profile for '{name}' — skipped (DB link still published).")

    # 5. Re-publish each changed meeting (respect the gate; skip blocked ones).
    for mdir, _meeting, _labels in changed:
        state = PipelineState(mdir)
        if not _may_publish(state.review_status, args.publish_anyway):
            print(f"  skip publish {mdir.name}: gate verdict '{state.review_status}' "
                  f"(re-run with --publish-anyway)")
            continue
        _publish_meeting_standalone(mdir.name, args.publish_anyway)

    # 6. Optional web redeploy.
    if args.deploy:
        _trigger_render_deploy()
```

- [ ] **Step 3: Add the dispatch**

In `run_local.py`'s `main()`, after the `--publish-meeting` dispatch block (~line 3246, right after `_publish_meeting_standalone(...)` / `return`), add:

```python
    if args.relink_person:
        _relink_person(args)
        return
```

- [ ] **Step 4: Smoke — argparse loads + dispatch wired**

Run: `.venv/bin/python run_local.py --help 2>&1 | grep -E "relink-person|--to-id|--dry-run|--deploy"`
Expected: the new flags appear in help output (confirms no argparse conflict and the module imports).

- [ ] **Step 5: Commit**

```bash
git add run_local.py && git commit -m "$(cat <<'EOF'
feat(relink): run_local --relink-person orchestrator + deploy hook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: End-to-end verification (dry-run) + full suite

**Files:** none (verification)

- [ ] **Step 1: Full test suite (no regressions)**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, including the 13 new `tests/test_relink.py` tests (baseline before this work was 482 passing).

- [ ] **Step 2: Dry-run against the real Steve Hilton meeting**

Run:
```bash
.venv/bin/python run_local.py --relink-person "Steve Hilton" --dry-run
```
Expected: prints `Target: ...` (resolved from essentials, or an ambiguity list if his name isn't a unique match — in which case re-run with `--to-id <uuid>` taken from the printed candidates), then `Will relink 'Steve Hilton' in 1 meeting(s): 2026-04-01-ca-courier-stevehiltoninterview: SPEAKER_00`, then the dry-run plan. **No files change** — confirm with `git status` (clean) and that `transcript_named.json` is untouched.

- [ ] **Step 3: (Optional, user-driven) the real run**

When ready to actually link him (this writes transcripts, the profile DB, and re-publishes):
```bash
.venv/bin/python run_local.py --relink-person "Steve Hilton" [--to-id <uuid>]
# then verify:
curl -s "https://accounts-api.empowered.vote/api/people" | grep -i hilton
```
Expected: Steve Hilton appears in the roster with his `politicianId`; `--deploy` (or a later batched deploy) refreshes the web. This step is operational and left to the user.

---

## Self-review notes (for the executor)

- The orchestrator reuses `_publish_meeting_standalone` (which correctly reloads `topics.json` into `section_topics` so re-publish doesn't wipe topic tags) rather than publishing the in-memory `Meeting`. The pre-check with `_may_publish` avoids that function's `sys.exit(2)` aborting the loop on a gated meeting.
- `relink_in_meeting` edits only speaker mappings; `Segment` has no politician fields, and `publish` derives segment identity from the mappings — so this is complete.
- `--dry-run` must perform zero writes (no transcript save, no `save_profiles`, no publish, no deploy).

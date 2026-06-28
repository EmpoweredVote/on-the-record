# One-Command Maintenance (`--republish-all`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `run_local.py --republish-all` subcommand that re-publishes every already-published meeting (a resync of live data), optionally rebuilds the voice-profile DB (`--reenroll`), and fires one Render deploy at the end — replacing the hardcoded `republish_all.sh`.

**Architecture:** A small `trigger_deploy` flag on `publish_meeting` lets a batch suppress the per-publish deploy hook. A behavior-preserving loader is extracted from `_publish_meeting_standalone` so the batch can load + publish without that function's `sys.exit` paths. The orchestrator intersects discovered transcripts with the set of published slugs, re-publishes each (continue-on-error, `publish_anyway`, deploy suppressed), optionally runs `reenroll_profiles.py` as a subprocess, and triggers one deploy.

**Tech Stack:** Python 3, pytest, psycopg2. Run with the repo venv: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`. Work on branch `claude/republish-all` (already created off `main`; the spec is committed there).

**Spec:** `docs/superpowers/specs/2026-06-27-republish-all-design.md`

---

## File Structure

- **Modify `src/publish.py`** — add `trigger_deploy: bool = True` to `publish_meeting`; the final `_trigger_deploy_hook()` runs only when true.
- **Modify `run_local.py`** — extract `_load_meeting_and_body(meeting_dir)` from `_publish_meeting_standalone` (and use it there); add `_published_meeting_slugs()`; add `_republish_all(args)`; register `--republish-all` / `--reenroll` / `--no-deploy` argparse options; dispatch in `main`.
- **Test `tests/test_publish.py`** — `trigger_deploy` behavior.
- **Test `tests/test_republish_all.py`** (new) — `_published_meeting_slugs`, the loader, and the `_republish_all` orchestrator.

---

## Task 1: `trigger_deploy` flag on `publish_meeting`

**Files:**
- Modify: `src/publish.py` — `publish_meeting` (def at line ~538; the `_trigger_deploy_hook()` call at line ~570).
- Test: `tests/test_publish.py`

Context: `publish_meeting(meeting, body_slug=None)` runs the publish transaction then unconditionally calls `_trigger_deploy_hook()` at the end. A bulk re-publish must be able to suppress the N per-publish hooks and fire one at the end.

- [ ] **Step 1: Write the failing test** — Append to `tests/test_publish.py`:

```python
def test_publish_meeting_suppresses_deploy_when_trigger_deploy_false(monkeypatch):
    import src.publish as publish

    calls = {"deploy": 0}
    monkeypatch.setattr(publish, "_trigger_deploy_hook", lambda: calls.__setitem__("deploy", calls["deploy"] + 1))
    # Stub the whole DB transaction so we only exercise the deploy decision.
    monkeypatch.setattr(publish, "_require_db_url", lambda: "postgresql://x")

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchone(self): return ("muid",)
        def fetchall(self): return []
    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()
        def close(self): pass
    monkeypatch.setattr(publish.psycopg2, "connect", lambda *a, **k: _Conn())
    # Stub the per-step helpers so publish_meeting reaches the deploy decision.
    for fn in ("_upsert_meeting", "_upsert_event_orgs", "_upsert_local_people",
               "_reconcile_event_races", "_replace_topics"):
        monkeypatch.setattr(publish, fn, lambda *a, **k: "muid")
    monkeypatch.setattr(publish, "_upsert_speakers", lambda *a, **k: {})
    monkeypatch.setattr(publish, "_replace_segments", lambda *a, **k: 0)

    from src.models import Meeting
    m = Meeting(meeting_id="m1", city="X", date="2026-04-01")

    publish.publish_meeting(m, None, trigger_deploy=False)
    assert calls["deploy"] == 0
    publish.publish_meeting(m, None)  # default True
    assert calls["deploy"] == 1
```

- [ ] **Step 2: Run to verify it fails** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_publish.py::test_publish_meeting_suppresses_deploy_when_trigger_deploy_false -v` — Expected: FAIL with `TypeError: publish_meeting() got an unexpected keyword argument 'trigger_deploy'`.

- [ ] **Step 3: Add the param** — In `src/publish.py`, change the signature and the final hook call:

```python
def publish_meeting(
    meeting: Meeting, body_slug: Optional[str] = None, trigger_deploy: bool = True
) -> PublishResult:
```

and replace the unconditional `_trigger_deploy_hook()` (line ~570) with:

```python
    if trigger_deploy:
        _trigger_deploy_hook()
```

(Leave the rest of the function unchanged.)

- [ ] **Step 4: Run to verify it passes** — same pytest command — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add src/publish.py tests/test_publish.py
git commit -m "feat(republish-all): publish_meeting trigger_deploy flag (suppress per-publish hook)"
```

---

## Task 2: Extract `_load_meeting_and_body` from `_publish_meeting_standalone`

**Files:**
- Modify: `run_local.py` — `_publish_meeting_standalone` (line ~1820).
- Test: existing `tests/` (behavior-preserving; verify nothing breaks).

Context: `_publish_meeting_standalone` loads the transcript + topics + `PipelineState` then publishes, with `sys.exit(1)` on a missing transcript and `sys.exit(2)` on a gate block. The batch needs the *load* part without the `sys.exit` paths. Extract the load into a helper both can call; the `sys.exit` checks stay in `_publish_meeting_standalone`.

- [ ] **Step 1: Add the loader** — In `run_local.py`, add this function directly above `_publish_meeting_standalone`:

```python
def _load_meeting_and_body(meeting_dir):
    """Load a meeting + its body_slug for (re)publishing. Assumes the
    transcript_named.json exists (caller checks). Carries topic tags and the
    pipeline-state body_slug, mirroring the standalone publish loader."""
    from src.checkpoint import PipelineState
    from src.models import Meeting, SectionTopic

    with open(meeting_dir / "transcript_named.json", "r", encoding="utf-8") as f:
        meeting = Meeting.from_dict(json.load(f))
    topics_path = meeting_dir / "topics.json"
    if topics_path.exists():
        with open(topics_path, "r", encoding="utf-8") as f:
            meeting.section_topics = [SectionTopic.from_dict(d) for d in json.load(f)]
    state = PipelineState(meeting_dir)
    if meeting.race_id is None:
        meeting.race_id = state.race_id
    return meeting, state.body_slug
```

- [ ] **Step 2: Use it in `_publish_meeting_standalone`** — Replace the body of `_publish_meeting_standalone` from the `with open(named_path...)` block through the `body_slug`/`race_id` assignment (lines ~1834-1848) with a call to the loader, keeping the existing missing-file and gate `sys.exit` checks. The function becomes:

```python
def _publish_meeting_standalone(meeting_id: str, publish_anyway: bool = False) -> None:
    """Publish an already-processed meeting to Supabase (backfill workhorse)."""
    from src import config
    from src.checkpoint import PipelineState
    from src.publish import publish_meeting

    meeting_dir = config.MEETINGS_DIR / meeting_id
    named_path = meeting_dir / "transcript_named.json"
    if not named_path.exists():
        print(f"No transcript_named.json found for meeting ID: {meeting_id}")
        print(f"  Expected at: {named_path}")
        sys.exit(1)

    meeting, body_slug = _load_meeting_and_body(meeting_dir)

    state = PipelineState(meeting_dir)
    if not _may_publish(state.review_status, publish_anyway):
        print(f"Refusing to publish {meeting_id} — gate verdict is "
              f"'{state.review_status}'.")
        print("  Review it (python run_local.py --review "
              f"{meeting_id}) and re-run, or pass --publish-anyway to override.")
        sys.exit(2)

    print(f"Publishing {meeting_id} to Supabase...")
    result = publish_meeting(meeting, body_slug)
    print(f"  Meeting:  {result.meeting_id}")
    print(f"  Segments: {result.segments}")
    print(f"  Speakers: {result.speakers}")
```

(The `Meeting`/`SectionTopic` imports move into the loader, so drop them from `_publish_meeting_standalone`'s local imports as shown.)

- [ ] **Step 3: Verify behavior is preserved** — Run the publish-related tests + import check:

```
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -c "import run_local" && /Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_publish.py tests/test_event_entities.py -v
```
Expected: import OK; tests pass (the extraction is behavior-preserving).

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "refactor(republish-all): extract _load_meeting_and_body from standalone publish"
```

---

## Task 3: `_published_meeting_slugs()` helper

**Files:**
- Modify: `run_local.py` — add the helper near `_publish_meeting_standalone`.
- Test: `tests/test_republish_all.py` (new)

Context: the orchestrator re-publishes only meetings already in `meetings.meetings`. This helper returns that slug set. It follows the connection pattern of `_resolve_debate_race_id` (open via `_require_db_url`, use a cursor) — testable by stubbing `psycopg2.connect`.

- [ ] **Step 1: Write the failing test** — Create `tests/test_republish_all.py`:

```python
from __future__ import annotations

import run_local


class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.executed = sql
    def fetchall(self): return self._rows


class _Conn:
    def __init__(self, rows): self._rows = rows
    def cursor(self): return _Cur(self._rows)
    def close(self): pass


def test_published_meeting_slugs(monkeypatch):
    import psycopg2
    monkeypatch.setattr("src.publish._require_db_url", lambda: "postgresql://x")
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: _Conn([("m1",), ("m2",)]))
    assert run_local._published_meeting_slugs() == {"m1", "m2"}
```

- [ ] **Step 2: Run to verify it fails** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_republish_all.py -v` — Expected: FAIL with `AttributeError: module 'run_local' has no attribute '_published_meeting_slugs'`.

- [ ] **Step 3: Add the helper** — In `run_local.py`, add above `_republish_all` (which Task 4 adds; for now place it after `_publish_meeting_standalone`):

```python
def _published_meeting_slugs() -> set[str]:
    """Slugs already present in meetings.meetings (the resync target set)."""
    import psycopg2

    from src.publish import _require_db_url

    conn = psycopg2.connect(_require_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT slug FROM meetings.meetings WHERE slug IS NOT NULL")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
```

- [ ] **Step 4: Run to verify it passes** — same pytest command — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_republish_all.py
git commit -m "feat(republish-all): _published_meeting_slugs DB helper"
```

---

## Task 4: `_republish_all(args)` orchestrator

**Files:**
- Modify: `run_local.py` — add `_republish_all`.
- Test: `tests/test_republish_all.py`

Context: discover transcripts in `config.MEETINGS_DIR`, intersect with `_published_meeting_slugs()`, re-publish each via the loader + `publish_meeting(..., trigger_deploy=False)` (continue-on-error), optionally reenroll (subprocess), one deploy at the end. `run_local` has module-level `from src import config`, `import json`, `import sys`, and `_REPO_DIR` (used by `_trigger_render_deploy`). `args` carries `dry_run`, `reenroll`, `no_deploy`.

- [ ] **Step 1: Write the failing integration test** — Append to `tests/test_republish_all.py`:

```python
import json
import argparse
from src.models import Meeting, SpeakerMapping


def _write_meeting(meeting_dir, mid):
    meeting_dir.mkdir(parents=True, exist_ok=True)
    m = Meeting(meeting_id=mid, city="X", date="2026-04-01",
                speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="A")})
    (meeting_dir / "transcript_named.json").write_text(json.dumps(m.to_dict()))


def _args(**over):
    ns = argparse.Namespace(dry_run=False, reenroll=False, no_deploy=False)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _common(monkeypatch, tmp_path, published, publish_rec, deploy_rec, fail=()):
    meetings_root = tmp_path / "meetings"
    for mid in ["m1", "m2", "m3"]:
        _write_meeting(meetings_root / mid, mid)
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    monkeypatch.setattr(run_local, "_published_meeting_slugs", lambda: set(published))

    def fake_publish(meeting, body_slug=None, trigger_deploy=True):
        if meeting.meeting_id in fail:
            raise RuntimeError("boom")
        publish_rec.append((meeting.meeting_id, trigger_deploy))
    monkeypatch.setattr("src.publish.publish_meeting", fake_publish)
    monkeypatch.setattr(run_local, "_trigger_render_deploy", lambda: deploy_rec.append(1))


def test_republish_all_publishes_only_published_meetings(tmp_path, monkeypatch):
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1", "m3"], pub, dep)
    run_local._republish_all(_args())
    assert {p[0] for p in pub} == {"m1", "m3"}          # m2 (unpublished) skipped
    assert all(p[1] is False for p in pub)              # per-publish deploy suppressed
    assert dep == [1]                                   # exactly one deploy at the end


def test_republish_all_dry_run_writes_nothing(tmp_path, monkeypatch):
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1"], pub, dep)
    run_local._republish_all(_args(dry_run=True))
    assert pub == [] and dep == []


def test_republish_all_no_deploy(tmp_path, monkeypatch):
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1"], pub, dep)
    run_local._republish_all(_args(no_deploy=True))
    assert {p[0] for p in pub} == {"m1"} and dep == []


def test_republish_all_continues_past_failure_and_exits_nonzero(tmp_path, monkeypatch):
    import pytest
    pub, dep = [], []
    _common(monkeypatch, tmp_path, ["m1", "m2", "m3"], pub, dep, fail=("m2",))
    with pytest.raises(SystemExit) as ei:
        run_local._republish_all(_args())
    assert {p[0] for p in pub} == {"m1", "m3"}          # m2 failed but others ran
    assert dep == [1]                                   # deploy still fires
    assert ei.value.code != 0


def test_republish_all_reenroll_runs_subprocess(tmp_path, monkeypatch):
    pub, dep, sub = [], [], []
    _common(monkeypatch, tmp_path, ["m1"], pub, dep)
    monkeypatch.setattr(run_local.subprocess, "run",
                        lambda *a, **k: sub.append(a) or type("R", (), {"returncode": 0})())
    run_local._republish_all(_args(reenroll=True))
    assert sub  # reenroll subprocess invoked
```

Note: the last test references `run_local.subprocess`. Verified: `run_local.py` already has module-level `import subprocess`, `import sys`, `import json`, and `_REPO_DIR = Path(__file__).resolve().parent` — no new imports needed.

- [ ] **Step 2: Run to verify it fails** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_republish_all.py -v` — Expected: FAIL with `AttributeError: module 'run_local' has no attribute '_republish_all'`.

- [ ] **Step 3: Implement** — `run_local.py` already has module-level `import subprocess`/`import sys`/`import json` and `_REPO_DIR` (verified), so no new imports are needed. Add `_republish_all` after `_published_meeting_slugs`:

```python
def _republish_all(args) -> None:
    """Re-publish every already-published meeting (resync), optionally reenroll,
    one deploy at the end. Continue-on-error; non-zero exit if any failed."""
    from src import config
    from src.publish import publish_meeting

    dirs = sorted(
        d for d in config.MEETINGS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
        and (d / "transcript_named.json").exists()
    )
    published = _published_meeting_slugs()
    to_publish = [d for d in dirs if d.name in published]
    skipped = [d for d in dirs if d.name not in published]
    local_names = {d.name for d in dirs}
    missing = sorted(s for s in published if s not in local_names)

    print(f"republish-all: {len(to_publish)} published meeting(s) to resync, "
          f"{len(skipped)} unpublished (skip), "
          f"{len(missing)} published with no local transcript.")

    if args.dry_run:
        print("\n(dry run — nothing published, reenrolled, or deployed)")
        for d in to_publish:
            print(f"  would re-publish: {d.name}")
        if skipped:
            print("  would skip (not published): " + ", ".join(d.name for d in skipped))
        print(f"  would reenroll: {'yes' if args.reenroll else 'no'}")
        print(f"  would deploy: {'no' if args.no_deploy else 'yes'}")
        return

    failed = []
    for d in to_publish:
        try:
            meeting, body_slug = _load_meeting_and_body(d)
            publish_meeting(meeting, body_slug, trigger_deploy=False)
            print(f"  ✅ {d.name}")
        except Exception as exc:  # noqa: BLE001 - continue-on-error; report + collect
            print(f"  ❌ {d.name}: {exc}")
            failed.append(d.name)

    if args.reenroll:
        print("Reenrolling voice profiles...")
        result = subprocess.run([sys.executable, "reenroll_profiles.py"], cwd=_REPO_DIR)
        if result.returncode != 0:
            print("  reenroll failed (see output above) — publishes are unaffected.")

    if not args.no_deploy:
        _trigger_render_deploy()

    print(f"\nDone: {len(to_publish) - len(failed)} published, {len(failed)} failed.")
    if skipped:
        print("  skipped (not published): " + ", ".join(d.name for d in skipped))
    if missing:
        print("  published but no local transcript (not re-published): " + ", ".join(missing))
    if failed:
        print("  FAILED: " + ", ".join(failed))
        sys.exit(1)
```

- [ ] **Step 4: Run to verify it passes** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_republish_all.py -v` — Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add run_local.py tests/test_republish_all.py
git commit -m "feat(republish-all): _republish_all orchestrator (resync published meetings)"
```

---

## Task 5: argparse + dispatch wiring

**Files:**
- Modify: `run_local.py` — register `--republish-all` / `--reenroll` / `--no-deploy`; dispatch in `main`.
- Test: `--help` parse check.

Context: argparse options are registered in the big block (near `--bulk-relink-scan`, ~line 3212+); dispatch happens in `main` as `if args.X: _handler(); return` (the `--bulk-relink-apply` dispatch is a good neighbor). Note `--no-deploy` becomes `args.no_deploy`; `--reenroll` → `args.reenroll`; `--republish-all` → `args.republish_all`. (`--dry-run` already exists from earlier work — reuse it.)

- [ ] **Step 1: Register the options** — In `run_local.py`, after the `--bulk-relink-apply` / `--out` argument registrations, add:

```python
    parser.add_argument("--republish-all", action="store_true",
                        help="Re-publish every already-published meeting (resync live data), "
                             "then trigger one web deploy. Add --reenroll to also rebuild the "
                             "voice-profile DB; --no-deploy to skip the rebuild.")
    parser.add_argument("--reenroll", action="store_true",
                        help="With --republish-all: also rebuild the voice-profile DB "
                             "(runs reenroll_profiles.py before the deploy)")
    parser.add_argument("--no-deploy", action="store_true",
                        help="With --republish-all: skip the single Render rebuild at the end")
```

- [ ] **Step 2: Dispatch** — In `main`, immediately after the `--bulk-relink-apply` dispatch block, add:

```python
    if args.republish_all:
        _republish_all(args)
        return
```

- [ ] **Step 3: Verify the CLI parses** — Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py --help` — Expected: help text includes `--republish-all`, `--reenroll`, `--no-deploy`.

- [ ] **Step 4: Dry-run smoke against real data** — Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
set -a; . ./.env.local 2>/dev/null; set +a
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python run_local.py --republish-all --dry-run
```
Expected: prints the count line (N published to resync / K unpublished skipped) and a `would re-publish:` list, writing nothing. (Needs DATABASE_URL; that's in `.env.local`.)

- [ ] **Step 5: Commit**

```bash
git add run_local.py
git commit -m "feat(republish-all): wire --republish-all / --reenroll / --no-deploy"
```

---

## Task 6: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q`
Expected: all tests pass (was 533 before this plan; this adds ~7 tests).

- [ ] **Step 2: Commit any fixups** (skip if already green)

```bash
git add -u && git commit -m "test(republish-all): align tests"
```

---

## Self-Review Notes (reconciled against the spec)

- **Spec coverage:** dynamic discovery ∩ published slugs (Task 4) · skip + report unpublished/missing (Task 4) · `publish_anyway` resync — note: the batch calls `publish_meeting` directly (which has no gate; the gate lives only in `_publish_meeting_standalone`/`_may_publish`), so publishing every already-published meeting is inherently "publish-anyway" with no extra flag needed · suppress per-publish deploy + one at end (Tasks 1, 4) · `--reenroll` subprocess (Task 4) · `--no-deploy` / `--dry-run` (Tasks 4, 5) · continue-on-error + non-zero exit (Task 4) · new subcommand (Task 5). Reenroll failure doesn't roll back publishes (Task 4). `republish_all.sh` left in place (untracked).
- **Note on the gate:** the spec frames the resync as `publish_anyway=True`. In practice the batch bypasses the gate by calling `publish_meeting` directly (the gate is only enforced in `_publish_meeting_standalone`). Same outcome — already-published meetings always re-publish — without threading a flag. This is intentional and documented here.
- **Type/name consistency:** `_load_meeting_and_body(meeting_dir) -> (meeting, body_slug)`, `_published_meeting_slugs() -> set[str]`, `publish_meeting(meeting, body_slug, trigger_deploy=True)`, and `args.republish_all/reenroll/no_deploy/dry_run` are used consistently across tasks.

# Source Discovery v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v1 discovery system trustworthy unattended (recorded runs, clone-based scheduler, persisted alarms) and extend the watchlist beyond YouTube to TV-station/news RSS feeds, plus the evidence-demanded ride-alongs (recency filter, real-labeled eval + calibration, yt-dlp backoff, mode-C evidence surface).

**Spec:** `docs/superpowers/specs/2026-08-03-source-discovery-v2-design.md` (approved 2026-08-03).

**Architecture:** Slice 0 first — one new DB table (`essentials.discovery_runs`), run-record wiring in `scripts/poll_discovery.py`, and a generalized `scripts/run_scheduled_poll.sh` that runs any `scripts/*.py` from the automation-checkout clone. Ride-alongs are small, independent changes to prefilter/search/eval/GUI. Slice 1 adds a generic `web_rss` outlet kind: one RSS/Atom parser + robots.txt gate in `feeds.py`, a page-text peek replacing the captions peek for web items (rename `captions_fetcher` → `peek_fetcher`), and a yt-dlp extractability probe on approve→ingest.

**Tech Stack:** Python 3 (`.venv/bin/python` ALWAYS — system python3 lacks deps), pytest, psycopg2, yt-dlp, FastAPI + Jinja2 (existing GUI), launchd, ev-accounts Postgres (Supabase; `DATABASE_URL` from `.env.local`, auto-loaded by `gui.env.load_env_local`).

**Conventions that bind every task:**
- Tests use fakes/monkeypatching, never the network or the real DB (see `tests/test_discovery_db.py` `_FakeCursor`, `tests/test_discovery_engine.py` `_patch_db`).
- Run tests with `.venv/bin/python -m pytest <file> -v`.
- Engine log lines are UPPERCASE-verb prefixed (`QUEUED`, `FAILED`, `SPEND CAP`).
- GUI data layer (`gui/discovery.py`) is best-effort: no `DATABASE_URL` or any DB error → empty defaults, never a crash. Engine data layer (`src/discovery/db.py`) is strict: cursor-in, raises.
- Commit after every task (not every step) unless a task says otherwise.

**Deviations from spec wording (deliberate, small):**
1. The `discovery_runs` column is named `trigger_kind`, not `trigger` (`TRIGGER` is a Postgres keyword; avoids quoting hazards).
2. The spec said the new table is "the only new DDL" — extending `web_rss` into `source_outlets.kind` requires swapping that CHECK constraint too (v1's migration constrained the "open enum"). Both ride in one migration file.

---

## File structure

**ev-accounts repo (`../ev-accounts`):**
- Create: `backend/migrations/1534_discovery_runs_web_rss.sql` — run-record table + kind check swap.

**on-the-record repo:**

| File | Change |
|---|---|
| `src/config.py` | 4 new `DISCOVERY_*` constants |
| `src/discovery/db.py` | `insert_run`, `finish_run`, `record_alarms` |
| `src/discovery/engine.py` | `RunStats.recency_filtered`, stale-drop, hydration gated to YouTube, `peek_fetcher` rename |
| `src/discovery/prefilter.py` | `is_stale()` |
| `src/discovery/search.py` | `with_backoff()` |
| `src/discovery/feeds.py` | `parse_news_feed`, `_robots_allowed`, `fetch_page_text`, `web_rss` in `fetch_outlet_items` |
| `src/discovery/classify.py` | `peek_fetcher` (plain-text excerpts), prompt-label tweak |
| `src/discovery/eval.py` | `calibration()` |
| `scripts/poll_discovery.py` | `--trigger`, run records, alarm persistence, `_peek_fetcher`, backoff wiring |
| `scripts/run_scheduled_poll.sh` | script-argument generalization (back-compat), log-dir mkdir |
| `scripts/launchd/vote.empowered.poll-discovery.plist` | ProgramArguments → wrapper |
| `scripts/harvest_discovery_verdicts.py` | new: triage verdicts → real eval fixtures |
| `scripts/eval_discovery_classifier.py` | second fixture file + calibration output |
| `gui/discovery.py` | `health()` last-run/overdue, `outlet_stats()`, `probe_extractable()` |
| `gui/app.py` | probe on approve→ingest; `outlet_stats` into template context |
| `gui/templates/discovery.html` | last-run pill, overdue pill, group counts, outlet-evidence table |
| `docs/runbooks/source-discovery.md` | wrapper/plist steps, calendar line, outlet-pack additions, last-run habit |
| Tests | `tests/test_discovery_db.py`, `test_discovery_engine.py`, `test_discovery_prefilter.py`, `test_discovery_search.py`, `test_discovery_feeds.py`, `test_discovery_classify.py`, `test_discovery_eval.py`, `test_gui_discovery.py`, new `tests/test_harvest_discovery_verdicts.py` |

---

# Slice 0 — operational trust

### Task 1: Migration 1534 — `discovery_runs` table + `web_rss` outlet kind

**Files:**
- Create: `../ev-accounts/backend/migrations/1534_discovery_runs_web_rss.sql`

- [ ] **Step 1: Confirm the kind CHECK constraint's actual name**

Run (from on-the-record root; `gui.env` auto-loads `.env.local`):

```bash
.venv/bin/python - <<'PY'
import os, psycopg2
from gui.env import load_env_local
load_env_local()
conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")
cur = conn.cursor()
cur.execute("""
    select conname, pg_get_constraintdef(oid)
    from pg_constraint
    where conrelid = 'essentials.source_outlets'::regclass and contype = 'c'
""")
for r in cur.fetchall(): print(r)
conn.close()
PY
```

Expected: a row like `('source_outlets_kind_check', "CHECK (kind = ANY (ARRAY['youtube_channel'...])")`. If the name differs, use the real name in Step 2's `drop constraint`.

- [ ] **Step 2: Write the migration**

Create `../ev-accounts/backend/migrations/1534_discovery_runs_web_rss.sql`:

```sql
-- Source discovery v2, slice 0: run records (unattended-operation evidence)
-- + slice 1: 'web_rss' outlet kind (TV-station / news-site feeds).
-- Spec: on-the-record/docs/superpowers/specs/2026-08-03-source-discovery-v2-design.md
-- Column is trigger_kind, not trigger: TRIGGER is a Postgres keyword.

create table if not exists essentials.discovery_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,          -- null on a crashed run: itself a signal
  trigger_kind text not null check (trigger_kind in ('scheduled','manual','race')),
  items_examined integer not null default 0,
  classified integer not null default 0,
  inserted_pending integer not null default 0,
  inserted_auto_filtered integer not null default 0,
  spend_capped integer not null default 0,
  failure_count integer not null default 0,
  failures text                      -- newline-joined summaries, truncated
);

create index if not exists discovery_runs_started_idx
  on essentials.discovery_runs (started_at desc);

alter table essentials.source_outlets
  drop constraint if exists source_outlets_kind_check;
alter table essentials.source_outlets
  add constraint source_outlets_kind_check
  check (kind in ('youtube_channel','podcast_rss','web_page','web_rss'));
```

- [ ] **Step 3: Apply to prod (additive DDL only) and verify**

```bash
.venv/bin/python - <<'PY'
import os, psycopg2
from pathlib import Path
from gui.env import load_env_local
load_env_local()
sql = Path("../ev-accounts/backend/migrations/1534_discovery_runs_web_rss.sql").read_text()
conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")
cur = conn.cursor()
cur.execute(sql)
conn.commit()
cur.execute("select count(*) from essentials.discovery_runs")
print("discovery_runs rows:", cur.fetchone()[0])
cur.execute("savepoint s")
cur.execute("insert into essentials.source_outlets (name, kind, feed_url, added_via) "
            "values ('__kindtest__','web_rss','https://kindtest.invalid/rss','manual')")
cur.execute("rollback to savepoint s")
conn.rollback()
print("web_rss kind accepted")
conn.close()
PY
```

Expected: `discovery_runs rows: 0` and `web_rss kind accepted`. If the insert raises a check-constraint error, a second (differently-named) old check survived — re-run Step 1, drop it by its real name, re-apply.

- [ ] **Step 4: Commit (ev-accounts repo)**

```bash
git -C ../ev-accounts add backend/migrations/1534_discovery_runs_web_rss.sql
git -C ../ev-accounts commit -m "migration 1534: discovery_runs table + web_rss outlet kind

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 2: `db.py` run-record + alarm-persistence helpers

**Files:**
- Modify: `src/discovery/db.py` (append after `alarm_races`)
- Test: `tests/test_discovery_db.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discovery_db.py` (the `_FakeCursor` class at the top of the file is reused; note its `fetchone` returns `rows[0]`):

```python
class _Stats:
    examined = 40
    classified = 30
    inserted_pending = 25
    inserted_auto_filtered = 5
    spend_capped = 3
    failures = ["outlet X: boom", "search 'q': bot check"]


def test_insert_run_returns_id_and_binds_trigger_kind():
    cur = _FakeCursor(rows=[("run-1",)])
    run_id = db.insert_run(cur, "scheduled")
    assert run_id == "run-1"
    sql, params = cur.executed[0]
    assert "essentials.discovery_runs" in sql
    assert "trigger_kind" in sql
    assert params == ("scheduled",)


def test_finish_run_writes_counters_and_joined_failures():
    cur = _FakeCursor()
    db.finish_run(cur, "run-1", _Stats())
    sql, params = cur.executed[0]
    assert "finished_at = now()" in sql
    assert params[0] == 40                      # items_examined
    assert params[5] == 2                       # failure_count
    assert params[6] == "outlet X: boom\nsearch 'q': bot check"
    assert params[7] == "run-1"


def test_finish_run_null_failures_when_none():
    class _Clean(_Stats):
        failures = []
    cur = _FakeCursor()
    db.finish_run(cur, "run-1", _Clean())
    _, params = cur.executed[0]
    assert params[5] == 0 and params[6] is None


def test_record_alarms_upserts_last_alarm_at_per_race():
    cur = _FakeCursor()
    db.record_alarms(cur, ["r1", "r2"])
    assert len(cur.executed) == 2
    sql, params = cur.executed[0]
    assert "last_alarm_at" in sql and "on conflict (race_id)" in sql
    assert params == ("r1",)


def test_record_alarms_empty_is_a_noop():
    cur = _FakeCursor()
    db.record_alarms(cur, [])
    assert cur.executed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py -v -k "run or alarms"`
Expected: FAIL — `AttributeError: module 'src.discovery.db' has no attribute 'insert_run'`

- [ ] **Step 3: Implement the helpers**

Append to `src/discovery/db.py`:

```python
def insert_run(cur, trigger_kind: str) -> str:
    """Open a run record; the caller commits immediately so a crashed run
    still leaves its started row (null finished_at = crashed)."""
    cur.execute(
        "insert into essentials.discovery_runs (trigger_kind) "
        "values (%s) returning id::text", (trigger_kind,))
    return cur.fetchone()[0]


def finish_run(cur, run_id: str, stats) -> None:
    failures_text = "\n".join(stats.failures)[:4000] or None
    cur.execute("""
        update essentials.discovery_runs
        set finished_at = now(), items_examined = %s, classified = %s,
            inserted_pending = %s, inserted_auto_filtered = %s,
            spend_capped = %s, failure_count = %s, failures = %s
        where id = %s::uuid
    """, (stats.examined, stats.classified, stats.inserted_pending,
          stats.inserted_auto_filtered, stats.spend_capped,
          len(stats.failures), failures_text, run_id))


def record_alarms(cur, race_ids: list) -> None:
    """Persist alarm history (last_alarm_at) for tripped races."""
    for race_id in race_ids:
        cur.execute("""
            insert into essentials.discovery_race_state (race_id, last_alarm_at)
            values (%s::uuid, now())
            on conflict (race_id) do update set last_alarm_at = now()
        """, (race_id,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py -v`
Expected: all PASS (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/discovery/db.py tests/test_discovery_db.py
git commit -m "feat: discovery run-record + alarm-persistence db helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3: `poll_discovery.py` — `--trigger` flag, run records, alarm persistence

**Files:**
- Modify: `scripts/poll_discovery.py`

The script is a thin wiring layer with no test file (matching `poll_agendas.py`); the db helpers it calls were TDD'd in Task 2. Verification here is by manual run.

- [ ] **Step 1: Add the `--trigger` argument**

In `main()`, after the `--print-alarms` argument (line 67):

```python
    ap.add_argument("--trigger", choices=("scheduled", "manual"), default="manual",
                    help="how this run started (the launchd plist passes scheduled)")
```

- [ ] **Step 2: Open a run record before the engine runs**

Replace the block from `provider = get_provider(...)` through the `return 0` at the end of the `try:` body (lines 81–107) with:

```python
        provider = get_provider(config.DISCOVERY_MODEL_ACTIVE)
        run_id = None
        if not args.dry_run:
            cur = conn.cursor()
            run_id = db.insert_run(cur, "race" if args.race else args.trigger)
            conn.commit()   # crash after this point leaves a visible started row
        stats = engine.run_discovery(
            conn,
            provider=provider,
            fetch_feed_items=feeds.fetch_outlet_items,
            ytsearch_fn=search.ytsearch,
            hydrate_fn=search.hydrate_item,
            captions_fetcher=_captions_fetcher,
            sleep_fn=time.sleep,
            meeting_keys=_meeting_source_keys(),
            today=dt.date.today(),
            dry_run=args.dry_run,
            race_filter=args.race,
            classify_cap=args.classify_cap,
            skip_watchlist=args.skip_watchlist,
            skip_sweeps=args.skip_sweeps,
        )
        print(f"DONE examined={stats.examined} queued={stats.inserted_pending} "
              f"auto_filtered={stats.inserted_auto_filtered} "
              f"prefiltered_out={stats.prefiltered_out} seen={stats.skipped_seen} "
              f"classified={stats.classified} capped={stats.spend_capped}")
        alarms = db.alarm_races(conn.cursor())
        if run_id is not None:
            cur = conn.cursor()
            db.finish_run(cur, run_id, stats)
            db.record_alarms(cur, [a[0] for a in alarms])
            conn.commit()
        for alarm in alarms:
            print(f"ALARM {alarm[2]} {alarm[1]} — no approved sources")
        if stats.failures:
            print(f"{len(stats.failures)} failure(s)", file=sys.stderr)
            return 1
        return 0
```

(This is the existing body with three changes: `run_id` open/commit before the engine, `alarms` captured once instead of queried twice, `finish_run` + `record_alarms` + commit after the engine. `captions_fetcher=` is renamed in Task 11 — leave it as-is here.)

- [ ] **Step 3: Verify manually — dry-run writes no run record, alarms path unchanged**

```bash
.venv/bin/python scripts/poll_discovery.py --print-alarms
.venv/bin/python scripts/poll_discovery.py --dry-run --skip-sweeps 2>&1 | tail -3
.venv/bin/python - <<'PY'
import os, psycopg2
from gui.env import load_env_local
load_env_local()
conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")
cur = conn.cursor()
cur.execute("select count(*) from essentials.discovery_runs")
print("runs after dry-run:", cur.fetchone()[0])
conn.close()
PY
```

Expected: alarms print as before; dry-run completes; `runs after dry-run: 0`.

- [ ] **Step 4: Verify manually — a real capped run writes one finished row and persists alarms**

```bash
.venv/bin/python scripts/poll_discovery.py --skip-sweeps --classify-cap 0
.venv/bin/python - <<'PY'
import os, psycopg2
from gui.env import load_env_local
load_env_local()
conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")
cur = conn.cursor()
cur.execute("select trigger_kind, finished_at is not null, items_examined, spend_capped "
            "from essentials.discovery_runs order by started_at desc limit 1")
print(cur.fetchone())
cur.execute("select count(*) from essentials.discovery_race_state where last_alarm_at is not null")
print("alarmed races persisted:", cur.fetchone()[0])
conn.close()
PY
```

Expected: `('manual', True, <n>, <n>)` and `alarmed races persisted:` ≥ 1 (three races are currently alarmed). `--classify-cap 0` keeps the run free (everything defers to the cap; no LLM spend).

- [ ] **Step 5: Commit**

```bash
git add scripts/poll_discovery.py
git commit -m "feat: poll_discovery writes run records + persists alarm history

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 4: Generalize the clone-wrapper; point the discovery plist at it

**Files:**
- Modify: `scripts/run_scheduled_poll.sh`
- Modify: `scripts/launchd/vote.empowered.poll-discovery.plist`

Background: PR #144 made the agenda poll run from `~/CouncilScribe/automation-checkout` (a standalone clone fast-forwarded to origin/main each run) because launchd's TCC denies git inside `~/Documents` and the primary checkout's branch is a coin flip. The discovery plist still executes the primary checkout directly. The installed agendas plist passes NO script argument (`--days 8 --reconcile-memos` only), so the wrapper must stay back-compatible.

- [ ] **Step 1: Generalize the wrapper**

In `scripts/run_scheduled_poll.sh`, replace the final `exec` line:

```bash
exec "$PYTHON" "$AUTOMATION_CHECKOUT/scripts/poll_agendas.py" "$@"
```

with:

```bash
# First argument may name the script to run (a scripts/*.py path relative to
# the checkout). Without one, default to the agenda poll so plists predating
# this generalization keep working untouched.
TARGET="scripts/poll_agendas.py"
case "${1:-}" in
    scripts/*.py) TARGET="$1"; shift ;;
esac

# launchd truncates output silently when a StandardOutPath's parent dir is
# missing; make every known job log dir exist for the NEXT run (launchd opens
# the log before exec'ing us, so this protects future runs, not this one).
mkdir -p "$HOME/CouncilScribe/agendas" "$HOME/CouncilScribe/discovery"

exec "$PYTHON" "$AUTOMATION_CHECKOUT/$TARGET" "$@"
```

Also update the header comment's first line from "Entry point for the launchd agenda/memo poll." to "Entry point for the launchd scheduled polls (agendas by default; pass a scripts/*.py path as the first argument to run another poller, e.g. scripts/poll_discovery.py)."

- [ ] **Step 2: Syntax-check and back-compat check**

```bash
bash -n scripts/run_scheduled_poll.sh && echo SYNTAX-OK
```

Expected: `SYNTAX-OK`. Then confirm argument routing without running anything, by temporarily previewing the parsed target:

```bash
bash -c 'TARGET="scripts/poll_agendas.py"; set -- --days 8 --reconcile-memos; case "${1:-}" in scripts/*.py) TARGET="$1"; shift ;; esac; echo "$TARGET | $@"'
bash -c 'TARGET="scripts/poll_agendas.py"; set -- scripts/poll_discovery.py --trigger scheduled; case "${1:-}" in scripts/*.py) TARGET="$1"; shift ;; esac; echo "$TARGET | $@"'
```

Expected: `scripts/poll_agendas.py | --days 8 --reconcile-memos` then `scripts/poll_discovery.py | --trigger scheduled`.

- [ ] **Step 3: Update the repo discovery plist**

In `scripts/launchd/vote.empowered.poll-discovery.plist`, replace the `ProgramArguments` array and drop the now-wrong `WorkingDirectory` (the wrapper sets its own context):

```xml
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/chrisandrews/CouncilScribe/automation-checkout/scripts/run_scheduled_poll.sh</string>
    <string>scripts/poll_discovery.py</string>
    <string>--trigger</string>
    <string>scheduled</string>
  </array>
```

Keep `Label`, `StartCalendarInterval` (08:00), `StandardOutPath`/`StandardErrorPath` (`~/CouncilScribe/discovery/poll.log`) unchanged. Update the comment above ProgramArguments to: `<!-- Runs from the automation-checkout clone (see run_scheduled_poll.sh header): launchd TCC cannot read ~/Documents git, and the primary checkout's branch is a coin flip. -->`

- [ ] **Step 4: Verify the wrapper drives the discovery poller end-to-end (manual, from the dev shell)**

The clone fast-forwards to origin/main, which already has `poll_discovery.py` (v1) but NOT `--trigger` (this branch). So verify with v1-compatible flags:

```bash
bash scripts/run_scheduled_poll.sh scripts/poll_discovery.py --print-alarms
```

Expected: `=== scheduled poll <timestamp> ===`, a `code: <sha> ...` line, then the ALARM lines. (Full `--trigger scheduled` verification happens post-merge via `launchctl kickstart` — see the runbook step in Task 13.)

- [ ] **Step 5: Commit**

```bash
git add scripts/run_scheduled_poll.sh scripts/launchd/vote.empowered.poll-discovery.plist
git commit -m "feat: discovery scheduler runs via the automation-checkout clone wrapper

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 5: GUI health strip — last-run line + overdue pill

**Files:**
- Modify: `gui/discovery.py` (`health()`)
- Modify: `gui/templates/discovery.html` (header)
- Test: `tests/test_gui_discovery.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_discovery.py` (reuse the file's existing helpers: `_row`, and the pattern of monkeypatching `gui.discovery` functions then using `TestClient(create_app())`; check the top of the file for the exact `create_app` import — it is already imported):

```python
def test_health_defaults_include_last_run_keys_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    h = discovery.health()
    assert h["last_run"] is None
    assert h["scheduled_run_overdue"] is False


def test_discovery_page_renders_last_run_and_overdue(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [])
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [], raising=False)
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 0,
        "last_run": {"started_at": "2026-08-03 08:00:04", "finished_at": "2026-08-03 08:11:40",
                     "trigger": "scheduled", "examined": 120, "classified": 40,
                     "queued": 9, "capped": 0, "failures": 0},
        "scheduled_run_overdue": True,
    })
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "last run 2026-08-03 08:00" in resp.text
    assert "no scheduled run in 36h" in resp.text
```

(`outlet_stats` doesn't exist until Task 9 — `raising=False` keeps this test valid both before and after.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v -k "last_run or overdue"`
Expected: FAIL — `KeyError: 'last_run'` (first test) and missing text (second).

- [ ] **Step 3: Extend `health()`**

In `gui/discovery.py`, replace the `health()` function with:

```python
def health() -> dict:
    empty = {"alarms": [], "stale_outlets": [], "pending_total": 0,
             "last_run": None, "scheduled_run_overdue": False}
    url = _db_url()
    if not url:
        return empty
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                from src.discovery.db import alarm_races
                alarms = alarm_races(cur, days=30)
                cur.execute("""
                    select name from essentials.source_outlets
                    where active and (last_polled_at is null
                                      or last_polled_at < now() - interval '48 hours')
                    order by name
                """)
                stale = [r[0] for r in cur.fetchall()]
                cur.execute("select count(*) from essentials.discovered_sources "
                            "where status = 'pending'")
                total = cur.fetchone()[0]
                cur.execute("""
                    select to_char(started_at, 'YYYY-MM-DD HH24:MI:SS'),
                           to_char(finished_at, 'YYYY-MM-DD HH24:MI:SS'),
                           trigger_kind, items_examined, classified,
                           inserted_pending, spend_capped, failure_count
                    from essentials.discovery_runs
                    order by started_at desc limit 1
                """)
                r = cur.fetchone()
                last_run = None
                if r:
                    last_run = {"started_at": r[0], "finished_at": r[1],
                                "trigger": r[2], "examined": r[3], "classified": r[4],
                                "queued": r[5], "capped": r[6], "failures": r[7]}
                cur.execute("""
                    select not exists (
                        select 1 from essentials.discovery_runs
                        where trigger_kind = 'scheduled'
                          and finished_at > now() - interval '36 hours')
                """)
                overdue = bool(cur.fetchone()[0])
            return {"alarms": alarms, "stale_outlets": stale, "pending_total": total,
                    "last_run": last_run, "scheduled_run_overdue": overdue}
        finally:
            conn.close()
    except Exception:
        return empty
```

- [ ] **Step 4: Render in the template header**

In `gui/templates/discovery.html`, inside `<header class="batch-header">` after the stale-feeds pill (`{% endif %}` at line 17), add:

```html
  {% if health.last_run %}
  <span class="pill" title="examined {{ health.last_run.examined }} · classified {{ health.last_run.classified }} · queued {{ health.last_run.queued }} · capped {{ health.last_run.capped }} · failures {{ health.last_run.failures }}">
    last run {{ (health.last_run.started_at or '')[:16] }} · {{ health.last_run.trigger }} ·
    {% if not health.last_run.finished_at %}CRASHED{% elif health.last_run.failures %}{{ health.last_run.failures }} failure(s){% else %}ok{% endif %}
  </span>
  {% endif %}
  {% if health.scheduled_run_overdue %}
  <span class="pill" style="background:#c0392b;color:#fff">no scheduled run in 36h</span>
  {% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: all PASS (new tests plus the pre-existing suite — existing tests monkeypatch `health` with their own dicts; Jinja treats the missing new keys as falsy, so they stay green).

- [ ] **Step 6: Commit**

```bash
git add gui/discovery.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat: GUI health strip shows last discovery run + 36h overdue pill

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

# Ride-alongs

### Task 6: Recency filter (stage-1 stale drop)

**Files:**
- Modify: `src/config.py` (after line 91, `DISCOVERY_FULL_EVENT_MIN_SECONDS`)
- Modify: `src/discovery/prefilter.py`
- Modify: `src/discovery/engine.py`
- Test: `tests/test_discovery_prefilter.py`, `tests/test_discovery_engine.py` (append)

- [ ] **Step 1: Write the failing prefilter tests**

Append to `tests/test_discovery_prefilter.py`:

```python
import datetime as dt

from src.discovery.prefilter import is_stale

TODAY = dt.date(2026, 8, 3)


def test_is_stale_old_item_dropped():
    assert is_stale("2024-05-01", TODAY) is True          # prior cycle


def test_is_stale_recent_and_boundary_pass():
    assert is_stale("2026-08-01", TODAY) is False
    assert is_stale((TODAY - dt.timedelta(days=420)).isoformat(), TODAY) is False


def test_is_stale_undated_and_junk_pass():
    assert is_stale(None, TODAY) is False                 # stage 2 owns undated
    assert is_stale("", TODAY) is False
    assert is_stale("not-a-date", TODAY) is False


def test_is_stale_datetime_prefix_ok():
    assert is_stale("2024-05-01T09:30:00+00:00", TODAY) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_prefilter.py -v -k stale`
Expected: FAIL — `ImportError: cannot import name 'is_stale'`

- [ ] **Step 3: Implement config + `is_stale`**

In `src/config.py`, after `DISCOVERY_FULL_EVENT_MIN_SECONDS` (line 91), add:

```python
DISCOVERY_MAX_ITEM_AGE_DAYS = 420               # recency filter: > this = stale/old-cycle
```

In `src/discovery/prefilter.py`, add `import datetime as dt` to the imports, and append:

```python
def is_stale(published_at: "str | None", today: dt.date) -> bool:
    """True when the item predates the recency window (old-cycle noise —
    the biggest observed reject class). Undated or unparseable dates pass:
    stage 2 owns them."""
    if not published_at:
        return False
    try:
        published = dt.date.fromisoformat(str(published_at)[:10])
    except ValueError:
        return False
    return (today - published).days > config.DISCOVERY_MAX_ITEM_AGE_DAYS
```

- [ ] **Step 4: Run prefilter tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_prefilter.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing engine test**

Append to `tests/test_discovery_engine.py` (uses that file's existing `_patch_db`, `_run`, `GOOD_ITEM`, `OUTLET` helpers — `_run` passes `today=dt.date(2026, 8, 3)`-style args through to `run_discovery`; read `_run`'s signature at the top of the file and match it):

```python
def test_stale_watchlist_item_is_recency_filtered(monkeypatch):
    import dataclasses
    inserted = []
    _patch_db(monkeypatch, inserted)
    old = dataclasses.replace(GOOD_ITEM, published_at="2024-01-15")
    stats = _run(monkeypatch, inserted,
                 fetch_feed_items=lambda outlet: [old], skip_sweeps=True)
    assert stats.recency_filtered == 1
    assert inserted == []


def test_hydrated_publish_date_also_recency_filtered(monkeypatch):
    import dataclasses
    inserted = []
    _patch_db(monkeypatch, inserted)
    undated = dataclasses.replace(GOOD_ITEM, published_at=None, description=None)

    def hydrate(item):
        item.description = "d"
        item.published_at = "2024-01-15"
        return item

    stats = _run(monkeypatch, inserted,
                 fetch_feed_items=lambda outlet: [undated],
                 hydrate_fn=hydrate, skip_sweeps=True)
    assert stats.recency_filtered == 1
    assert inserted == []
```

(If `_run` does not accept `fetch_feed_items`/`hydrate_fn`/`skip_sweeps` overrides via `**kwargs`, follow whatever override mechanism the existing tests at lines ~140–180 use — several already pass `hydrate_fn=lambda item: item`.)

- [ ] **Step 6: Run engine tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_engine.py -v -k recency`
Expected: FAIL — `AttributeError: 'RunStats' object has no attribute 'recency_filtered'`

- [ ] **Step 7: Implement in the engine**

In `src/discovery/engine.py`:

1. Import: change the prefilter import (line 20) to
   `from src.discovery.prefilter import is_stale, normalize, prefilter_item`
2. Add to `RunStats` (after `prefiltered_out`): `recency_filtered: int = 0`
3. In `process()`, immediately after `stats.examined += 1` (line 88):

```python
        if is_stale(item.published_at, today):
            stats.recency_filtered += 1
            return
```

4. After the hydration block re-runs the prefilter (after line 104's `return`), add the same check — hydration can reveal a publish date the flat search lacked:

```python
            if is_stale(item.published_at, today):
                stats.recency_filtered += 1
                return
```

5. In `scripts/poll_discovery.py`, extend the DONE line to include it:

```python
        print(f"DONE examined={stats.examined} queued={stats.inserted_pending} "
              f"auto_filtered={stats.inserted_auto_filtered} "
              f"prefiltered_out={stats.prefiltered_out} "
              f"recency_filtered={stats.recency_filtered} seen={stats.skipped_seen} "
              f"classified={stats.classified} capped={stats.spend_capped}")
```

- [ ] **Step 8: Run the full discovery test set**

Run: `.venv/bin/python -m pytest tests/test_discovery_engine.py tests/test_discovery_prefilter.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/config.py src/discovery/prefilter.py src/discovery/engine.py scripts/poll_discovery.py tests/test_discovery_prefilter.py tests/test_discovery_engine.py
git commit -m "feat: stage-1 recency filter kills stale/old-cycle items

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 7: yt-dlp exponential backoff on bot-check/rate errors

**Files:**
- Modify: `src/config.py`, `src/discovery/search.py`, `scripts/poll_discovery.py`
- Test: `tests/test_discovery_search.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discovery_search.py`:

```python
import pytest

from src.discovery.search import with_backoff


def _flaky(fail_times, exc):
    calls = {"n": 0}
    def fn():
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise exc
        return "ok"
    return fn, calls


def test_backoff_retries_bot_check_and_succeeds():
    fn, calls = _flaky(2, RuntimeError("Sign in to confirm you're not a bot"))
    sleeps = []
    assert with_backoff(fn, sleep_fn=sleeps.append) == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]          # exponential: later waits are longer


def test_backoff_exhaustion_reraises():
    fn, calls = _flaky(99, RuntimeError("HTTP Error 429: Too Many Requests"))
    with pytest.raises(RuntimeError):
        with_backoff(fn, retries=2, sleep_fn=lambda s: None)
    assert calls["n"] == 3                # 1 try + 2 retries


def test_backoff_ignores_non_retryable_errors():
    fn, calls = _flaky(99, ValueError("Unsupported URL: https://x"))
    with pytest.raises(ValueError):
        with_backoff(fn, sleep_fn=lambda s: None)
    assert calls["n"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_search.py -v -k backoff`
Expected: FAIL — `ImportError: cannot import name 'with_backoff'`

- [ ] **Step 3: Implement**

In `src/config.py`, after `DISCOVERY_MAX_ITEM_AGE_DAYS`:

```python
DISCOVERY_BACKOFF_RETRIES = 3                   # yt-dlp bot-check/429 retries per query
DISCOVERY_BACKOFF_BASE_SECONDS = 5.0
```

In `src/discovery/search.py`, add `import random` and `import time` to the imports, and add above `queries_for_candidate`:

```python
RETRYABLE_MARKERS = ("429", "too many requests", "sign in to confirm", "bot")


def with_backoff(fn, *, retries: "int | None" = None,
                 base_delay: "float | None" = None, sleep_fn=time.sleep):
    """Run fn(); on a retryable yt-dlp error (bot-check / rate limit) retry
    with exponential backoff + jitter. Non-retryable errors and the final
    failure propagate — the engine's per-query handler stays the decider,
    and a hard bot-check wave still exits 1 without resetting the cadence
    clock (record_sweep skips failed sweeps)."""
    tries = retries if retries is not None else config.DISCOVERY_BACKOFF_RETRIES
    base = base_delay if base_delay is not None else config.DISCOVERY_BACKOFF_BASE_SECONDS
    for attempt in range(tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — filtered by marker below
            msg = str(exc).lower()
            if attempt >= tries or not any(m in msg for m in RETRYABLE_MARKERS):
                raise
            sleep_fn(base * (3 ** attempt) * (0.5 + random.random()))
```

In `scripts/poll_discovery.py`, change the engine wiring line

```python
            ytsearch_fn=search.ytsearch,
```

to

```python
            ytsearch_fn=lambda q: search.with_backoff(lambda: search.ytsearch(q)),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_search.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/discovery/search.py scripts/poll_discovery.py tests/test_discovery_search.py
git commit -m "feat: exponential backoff on yt-dlp bot-check/rate errors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 8: Eval harvest script + calibration metric

**Files:**
- Create: `scripts/harvest_discovery_verdicts.py`
- Create (generated): `tests/fixtures/discovery_eval_real.jsonl`
- Modify: `src/discovery/eval.py`, `scripts/eval_discovery_classifier.py`
- Test: `tests/test_discovery_eval.py` (append), Create: `tests/test_harvest_discovery_verdicts.py`

- [ ] **Step 1: Write the failing calibration tests**

Append to `tests/test_discovery_eval.py`:

```python
from src.discovery.eval import calibration
from src.discovery.models import Verdict


def _v(relevant, conf):
    return Verdict(relevant=relevant, confidence=conf)


def test_calibration_brier_perfect_predictions():
    pairs = [(True, _v(True, 1.0)), (False, _v(False, 1.0))]
    out = calibration(pairs)
    assert out["n"] == 2
    assert out["brier"] == 0.0


def test_calibration_brier_scores_implied_relevance_probability():
    # says relevant at 0.8 but gold is False -> (0.8 - 0)^2 = 0.64
    out = calibration([(False, _v(True, 0.8))])
    assert abs(out["brier"] - 0.64) < 1e-9


def test_calibration_skips_parse_failures_and_buckets():
    bad = Verdict(False, 0.0, rejected_reason="no JSON in reply")
    pairs = [(True, _v(True, 0.95)), (False, _v(True, 0.95)), (True, bad)]
    out = calibration(pairs)
    assert out["n"] == 2
    top = [b for b in out["buckets"] if b["range"] == "0.8–1.0"]
    assert top and top[0]["n"] == 2 and abs(top[0]["actual"] - 0.5) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_eval.py -v -k calibration`
Expected: FAIL — `ImportError: cannot import name 'calibration'`

- [ ] **Step 3: Implement `calibration()`**

Append to `src/discovery/eval.py`:

```python
def calibration(pairs: list) -> dict:
    """pairs = [(gold_relevant, Verdict)]. Brier-scores the probability the
    model implicitly assigns to 'relevant' (confidence when it said relevant,
    1-confidence when it said not). Parse failures are excluded — they are
    already counted by classify_outcome."""
    scored = [(gold, (v.confidence if v.relevant else 1.0 - v.confidence))
              for gold, v in pairs if v.rejected_reason is None]
    if not scored:
        return {"n": 0, "brier": None, "buckets": []}
    brier = sum((p - (1.0 if gold else 0.0)) ** 2 for gold, p in scored) / len(scored)
    buckets = []
    for i in range(5):
        lo, hi = i * 0.2, (i + 1) * 0.2
        in_bucket = [(gold, p) for gold, p in scored
                     if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if in_bucket:
            buckets.append({
                "range": f"{lo:.1f}–{hi:.1f}", "n": len(in_bucket),
                "predicted": sum(p for _, p in in_bucket) / len(in_bucket),
                "actual": sum(1 for gold, _ in in_bucket if gold) / len(in_bucket),
            })
    return {"n": len(scored), "brier": brier, "buckets": buckets}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_eval.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing harvest tests**

Create `tests/test_harvest_discovery_verdicts.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from harvest_discovery_verdicts import merge_examples, to_example

ROW = ("youtube:abc12345678", "Full debate", "All four candidates", "KXAN",
       3480, "ingested", None, "TX · U.S. Senate · General · 2026",
       ["Ana Ruiz", "Maria Delgado"])


def test_to_example_approved_is_gold_true():
    ex = to_example(ROW)
    assert ex == {
        "title": "Full debate", "description": "All four candidates",
        "channel": "KXAN", "duration_seconds": 3480,
        "race_label": "TX · U.S. Senate · General · 2026",
        "roster": ["Ana Ruiz", "Maria Delgado"],
        "gold_relevant": True, "source_key": "youtube:abc12345678",
    }


def test_to_example_relevance_rejects_are_gold_false():
    for reason in ("clip-not-original", "wrong-person", "tier-5"):
        row = ROW[:5] + ("rejected", reason) + ROW[7:]
        assert to_example(row)["gold_relevant"] is False


def test_to_example_non_relevance_rejects_are_skipped():
    for reason in ("stale", "duplicate", "other"):
        row = ROW[:5] + ("rejected", reason) + ROW[7:]
        assert to_example(row) is None


def test_merge_examples_dedupes_on_source_key_existing_wins():
    existing = [{"source_key": "youtube:abc12345678", "gold_relevant": False,
                 "title": "hand-corrected"}]
    merged = merge_examples(existing, [to_example(ROW), to_example(ROW)])
    assert len(merged) == 1
    assert merged[0]["title"] == "hand-corrected"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_harvest_discovery_verdicts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harvest_discovery_verdicts'` (the script doesn't exist yet).

- [ ] **Step 7: Implement the harvest script**

Create `scripts/harvest_discovery_verdicts.py`:

```python
"""Export human triage verdicts into a real-labeled eval fixture.

Every approved/ingested row is a gold-relevant example; rejects are gold-
irrelevant ONLY when the reason is a relevance verdict (clip-not-original,
wrong-person, tier-5). stale/duplicate/other say nothing about relevance and
are skipped. Existing fixture lines win on source_key so hand corrections
survive re-harvests.

Usage:
  .venv/bin/python scripts/harvest_discovery_verdicts.py            # write
  .venv/bin/python scripts/harvest_discovery_verdicts.py --dry-run  # counts only
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui.env import load_env_local  # noqa: E402

load_env_local()

from src.discovery import db  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval_real.jsonl"

GOLD_FALSE_REASONS = {"clip-not-original", "wrong-person", "tier-5"}

QUERY = """
    select d.source_key, d.title, d.description_snippet, d.channel_name,
           d.duration_seconds, d.status, d.status_reason,
           coalesce(p.race_label, '(unknown race)'),
           coalesce((select array_agg(rc.full_name order by rc.full_name)
                     from essentials.race_candidates rc
                     where rc.race_id = d.race_id and rc.full_name is not null),
                    '{}')
    from essentials.discovered_sources d
    left join essentials.readrank_race_pipeline p on p.race_id = d.race_id
    where d.status in ('approved', 'ingested', 'rejected')
    order by d.created_at
"""


def to_example(row) -> "dict | None":
    (source_key, title, snippet, channel, duration, status, reason,
     race_label, roster) = row
    if status == "rejected" and reason not in GOLD_FALSE_REASONS:
        return None
    return {
        "title": title or "", "description": snippet or "",
        "channel": channel or "", "duration_seconds": duration,
        "race_label": race_label, "roster": list(roster or []),
        "gold_relevant": status in ("approved", "ingested"),
        "source_key": source_key,
    }


def merge_examples(existing: list, harvested: list) -> list:
    """source_key-keyed merge; existing fixture lines win (hand corrections)."""
    by_key = {}
    for ex in [e for e in harvested if e] + existing:
        by_key[ex["source_key"]] = ex   # later wins -> existing overrides
    return sorted(by_key.values(), key=lambda e: e["source_key"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(QUERY)
        rows = cur.fetchall()
    finally:
        conn.close()
    harvested = [to_example(r) for r in rows]
    kept = [e for e in harvested if e]
    existing = []
    if FIXTURE.exists():
        existing = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line]
    merged = merge_examples(existing, kept)
    gold_true = sum(1 for e in merged if e["gold_relevant"])
    print(f"triaged rows={len(rows)} harvestable={len(kept)} "
          f"merged={len(merged)} (gold_relevant={gold_true}, "
          f"gold_irrelevant={len(merged) - gold_true})")
    if args.dry_run:
        return 0
    FIXTURE.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in merged))
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Run harvest tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_harvest_discovery_verdicts.py -v`
Expected: all PASS.

- [ ] **Step 9: Wire both fixture files + calibration into the eval script**

In `scripts/eval_discovery_classifier.py`:

1. Replace the single `FIXTURES` constant with:

```python
FIXTURES = [
    Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval.jsonl",
    Path(__file__).resolve().parent.parent / "tests/fixtures/discovery_eval_real.jsonl",
]
```

2. Replace the `examples = ...` line with:

```python
    examples = []
    for path in FIXTURES:
        if path.exists():
            examples.extend(json.loads(line)
                            for line in path.read_text().splitlines() if line)
```

3. Import `calibration` alongside the other eval imports:

```python
from src.discovery.eval import calibration, classify_outcome, summarize  # noqa: E402
```

4. Collect pairs and print calibration — inside the per-model loop, add `pairs = []` beside `outcomes = []`, append `pairs.append((ex["gold_relevant"], verdict))` right after `outcomes.append(outcome)`, and after the summary table print:

```python
        cal = calibration(pairs)
        if cal["brier"] is None:
            print(f"\ncalibration ({model}): n=0")
        else:
            print(f"\ncalibration ({model}): n={cal['n']} brier={cal['brier']:.3f}")
        for b in cal["buckets"]:
            print(f"  {b['range']}: n={b['n']} predicted={b['predicted']:.2f} "
                  f"actual={b['actual']:.2f}")
```

(Note the `rows.append(summarize(...))` structure keeps per-model scope; put `cal` inside the model loop.)

- [ ] **Step 10: Harvest for real and eval**

```bash
.venv/bin/python scripts/harvest_discovery_verdicts.py
.venv/bin/python scripts/eval_discovery_classifier.py --models haiku
```

Expected: harvest reports ~34 triaged rows, ~23 harvestable (19 rejects minus 11 stale/duplicate/other, plus 15 approved/ingested — exact counts printed); eval runs on 8 synthetic + harvested examples and prints the recall/precision table plus a calibration block. Spend note: ~30–45 Haiku calls, well under the run cap.

- [ ] **Step 11: Commit**

```bash
git add scripts/harvest_discovery_verdicts.py scripts/eval_discovery_classifier.py src/discovery/eval.py tests/test_discovery_eval.py tests/test_harvest_discovery_verdicts.py tests/fixtures/discovery_eval_real.jsonl
git commit -m "feat: harvest real triage verdicts into eval fixtures + calibration metric

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 9: Mode-C evidence surface — per-outlet stats + group pending counts

**Files:**
- Modify: `gui/discovery.py`, `gui/app.py` (discovery_page), `gui/templates/discovery.html`
- Test: `tests/test_gui_discovery.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_discovery.py`:

```python
def test_outlet_stats_empty_without_db(monkeypatch):
    monkeypatch.setattr(discovery, "_db_url", lambda: None)
    assert discovery.outlet_stats() == []


def test_discovery_page_renders_outlet_evidence_and_group_counts(monkeypatch):
    monkeypatch.setattr(discovery, "pending_rows", lambda: [_row(), _row(id="d2")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "alarms": [], "stale_outlets": [], "pending_total": 2,
        "last_run": None, "scheduled_run_overdue": False})
    monkeypatch.setattr(discovery, "outlet_stats", lambda: [
        {"name": "Fountainhead Forum", "reviewed": 2, "approved": 2, "identity_rejects": 0},
        {"name": "Milwaukee Journal Sentinel", "reviewed": 6, "approved": 0, "identity_rejects": 1},
    ])
    import gui.races as races
    monkeypatch.setattr(races, "race_labels", lambda ids: {"r1": "TX · U.S. Senate"})
    client = TestClient(create_app())
    resp = client.get("/discovery")
    assert resp.status_code == 200
    assert "Fountainhead Forum" in resp.text
    assert "100%" in resp.text                       # 2/2 approved
    assert "Outlet evidence" in resp.text
    assert "2 pending</span></h2>" in resp.text.replace("\n", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v -k outlet`
Expected: FAIL — `AttributeError: ... has no attribute 'outlet_stats'`

- [ ] **Step 3: Implement `outlet_stats()`**

Append to `gui/discovery.py`:

```python
def outlet_stats() -> list:
    """Per-outlet triage evidence toward the future mode-C flag-flip.
    Qualification bar (spec, Q4): >=10 reviewed, >=90% approved, zero
    identity-class rejects. Display-only — no auto-ingest path exists."""
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select o.name,
                           count(*) filter (where d.status in
                               ('approved','ingested','rejected')) as reviewed,
                           count(*) filter (where d.status in
                               ('approved','ingested')) as approved,
                           count(*) filter (where d.status = 'rejected'
                               and d.status_reason in
                                   ('wrong-person','clip-not-original')) as identity_rejects
                    from essentials.source_outlets o
                    join essentials.discovered_sources d on d.outlet_id = o.id
                    group by o.name
                    having count(*) filter (where d.status in
                        ('approved','ingested','rejected')) > 0
                    order by 2 desc, o.name
                """)
                return [{"name": r[0], "reviewed": r[1], "approved": r[2],
                         "identity_rejects": r[3]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
```

- [ ] **Step 4: Pass stats into the template**

In `gui/app.py` `discovery_page` (line 88–91), add `outlet_stats` to the context:

```python
        return _templates.TemplateResponse(
            request, "discovery.html",
            {"groups": list(groups.items()), "health": discovery.health(),
             "outlet_stats": discovery.outlet_stats(), "flash": flash})
```

- [ ] **Step 5: Render group counts + evidence table**

In `gui/templates/discovery.html`:

1. Change the group heading (line 37) to:

```html
<h2>{{ label }} <span class="pill">{{ rows|length }} pending</span></h2>
```

2. Before `</main>` (line 90), add:

```html
{% if outlet_stats %}
<section>
  <h2>Outlet evidence</h2>
  <p><small>Auto-ingest (mode C) qualification bar: ≥10 reviewed · ≥90% approved ·
  0 identity rejects. Display only — every ingest still goes through this queue.</small></p>
  <table class="library"><tbody>
  {% for s in outlet_stats %}
  <tr>
    <td>{{ s.name }}</td>
    <td>{{ s.reviewed }} reviewed</td>
    <td>{{ s.approved }} approved ({{ (100 * s.approved / s.reviewed) | round | int }}%)</td>
    <td>{{ s.identity_rejects }} identity reject(s)</td>
  </tr>
  {% endfor %}
  </tbody></table>
</section>
{% endif %}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: all PASS. If pre-existing page tests fail on the new `discovery.outlet_stats` call, they don't monkeypatch it — the real function returns `[]` without `DATABASE_URL`; if the test env sets `DATABASE_URL`, add `monkeypatch.setattr(discovery, "outlet_stats", lambda: [])` to those tests.

- [ ] **Step 7: Commit**

```bash
git add gui/discovery.py gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat: per-outlet triage evidence readout + group pending counts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

# Slice 1 — TV-station / news-RSS watchlist layer

### Task 10: `feeds.py` — generic news feed parser, robots gate, page text

**Files:**
- Modify: `src/discovery/feeds.py`, `src/config.py`
- Test: `tests/test_discovery_feeds.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_discovery_feeds.py`:

```python
from src.discovery import feeds
from src.discovery.feeds import (_robots_allowed, fetch_page_text,
                                 parse_news_feed)
from src.discovery.models import Outlet

NEWS_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>KCTV5 Politics</title>
  <item>
    <title>Kansas governor candidates meet in first debate</title>
    <link>https://www.kctv5.com/2026/08/01/governor-debate/</link>
    <description>The full debate aired Thursday.</description>
    <pubDate>Sat, 01 Aug 2026 21:00:00 GMT</pubDate>
  </item>
  <item><title>No link, skipped</title></item>
</channel></rss>"""

NEWS_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Statehouse Bureau</title>
  <entry>
    <title>Candidate forum recap</title>
    <link rel="alternate" href="https://news.example/forum-recap"/>
    <summary>Watch the full forum.</summary>
    <published>2026-08-02T09:00:00Z</published>
  </entry>
</feed>"""


def test_parse_news_feed_rss():
    items = parse_news_feed(NEWS_RSS, outlet_id="o9")
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://www.kctv5.com/2026/08/01/governor-debate/"
    assert it.channel_name == "KCTV5 Politics"
    assert it.duration_seconds is None
    assert it.published_at.startswith("2026-08-01")
    assert it.outlet_id == "o9" and it.via == "watchlist"


def test_parse_news_feed_atom():
    items = parse_news_feed(NEWS_ATOM, outlet_id="o9")
    assert len(items) == 1
    assert items[0].url == "https://news.example/forum-recap"
    assert items[0].channel_name == "Statehouse Bureau"
    assert items[0].published_at == "2026-08-02T09:00:00Z"


def test_robots_disallow_blocks_and_missing_allows():
    feeds._robots_cache.clear()
    blocked = lambda url: "User-agent: *\nDisallow: /"
    assert _robots_allowed("https://x.example/feed.rss", fetch_text_fn=blocked) is False

    feeds._robots_cache.clear()
    def missing(url):
        raise RuntimeError("404")
    assert _robots_allowed("https://x.example/feed.rss", fetch_text_fn=missing) is True


def test_robots_cache_is_per_origin(monkeypatch):
    feeds._robots_cache.clear()
    calls = []
    def fetch(url):
        calls.append(url)
        return "User-agent: *\nAllow: /"
    assert _robots_allowed("https://x.example/a", fetch_text_fn=fetch)
    assert _robots_allowed("https://x.example/b", fetch_text_fn=fetch)
    assert calls == ["https://x.example/robots.txt"]


def test_fetch_outlet_items_web_rss_respects_robots(monkeypatch):
    feeds._robots_cache.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: False)
    outlet = Outlet(id="o9", name="KCTV5", kind="web_rss",
                    feed_url="https://www.kctv5.com/rss/politics/")
    try:
        feeds.fetch_outlet_items(outlet)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "robots.txt" in str(exc)


def test_fetch_page_text_strips_markup(monkeypatch):
    feeds._robots_cache.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_text", lambda url:
        "<html><script>var x=1;</script><body><h1>Debate</h1>"
        "<p>Watch the full governor debate &amp; forum.</p></body></html>")
    text = fetch_page_text("https://x.example/article")
    assert "Debate Watch the full governor debate & forum." in text
    assert "var x" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_feeds.py -v -k "news or robots or page_text or web_rss"`
Expected: FAIL — `ImportError: cannot import name 'parse_news_feed'`

- [ ] **Step 3: Implement**

In `src/config.py`, after `DISCOVERY_BACKOFF_BASE_SECONDS`:

```python
DISCOVERY_WEB_FETCH_SLEEP_SECONDS = 2.0         # per-domain politeness for web_rss
```

In `src/discovery/feeds.py`:

1. Extend the module docstring's first line to: `"""Watchlist feed fetching/parsing: YouTube channel Atom + podcast RSS + generic news RSS/Atom (web_rss)."""` and add to the imports:

```python
import html as html_
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from src import config
```

2. Append after `parse_podcast_feed`:

```python
_robots_cache: dict = {}


def _robots_allowed(url: str, fetch_text_fn=None) -> bool:
    """Mechanical robots.txt respect for every web_rss fetch (spec Q8).
    Missing/unreachable robots.txt (the common case) means allowed."""
    fetch = fetch_text_fn or _fetch_text
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _robots_cache:
        rp = RobotFileParser()
        try:
            rp.parse(fetch(f"{origin}/robots.txt").splitlines())
        except Exception:  # noqa: BLE001 — no robots.txt -> allowed
            rp = None
        _robots_cache[origin] = rp
    rp = _robots_cache[origin]
    return True if rp is None else rp.can_fetch("*", url)


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def parse_news_feed(xml_text: str, *, outlet_id: "str | None" = None,
                    outlet_name: "str | None" = None) -> list:
    """Generic news feed: RSS 2.0 or Atom. Items carry no duration — the
    stage-1 duration heuristics skip them and stage 2 owns depth."""
    root = ET.fromstring(xml_text)
    items = []
    if root.tag == f"{_ATOM_NS}feed":
        channel = root.findtext(f"{_ATOM_NS}title") or outlet_name
        for entry in root.findall(f"{_ATOM_NS}entry"):
            link = entry.find(f"{_ATOM_NS}link[@rel='alternate']")
            if link is None:
                link = entry.find(f"{_ATOM_NS}link")
            url = link.get("href") if link is not None else None
            if not url:
                continue
            items.append(RawItem(
                url=url,
                title=entry.findtext(f"{_ATOM_NS}title"),
                description=(entry.findtext(f"{_ATOM_NS}summary")
                             or entry.findtext(f"{_ATOM_NS}content")),
                channel_name=channel,
                published_at=(entry.findtext(f"{_ATOM_NS}published")
                              or entry.findtext(f"{_ATOM_NS}updated")),
                outlet_id=outlet_id, via="watchlist"))
        return items
    channel = root.findtext("./channel/title") or outlet_name
    for item in root.findall("./channel/item"):
        url = (item.findtext("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError):
                published = None
        items.append(RawItem(
            url=url, title=item.findtext("title"),
            description=item.findtext("description"),
            channel_name=channel, published_at=published,
            outlet_id=outlet_id, via="watchlist"))
    return items


_SCRIPT_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def fetch_page_text(url: str, max_chars: int = 6000) -> str:
    """Article-page text for the stage-2 page peek (web analog of the
    captions peek). Robots-gated like every web fetch; returns '' when
    disallowed."""
    if not _robots_allowed(url):
        return ""
    raw = _fetch_text(url)
    text = _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", raw))
    text = html_.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:max_chars]
```

3. In `fetch_outlet_items`, before the final `return []` line, add:

```python
    if outlet.kind == "web_rss":
        if not _robots_allowed(outlet.feed_url):
            raise RuntimeError(f"robots.txt disallows {outlet.feed_url}")
        text = _fetch_text(outlet.feed_url)
        time.sleep(config.DISCOVERY_WEB_FETCH_SLEEP_SECONDS)  # per-domain politeness
        return parse_news_feed(text, outlet_id=outlet.id, outlet_name=outlet.name)
```

4. Update the `Outlet.kind` comment in `src/discovery/models.py` (line 30) to:

```python
    kind: str          # 'youtube_channel' | 'podcast_rss' | 'web_rss' | 'web_page'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_feeds.py -v`
Expected: all PASS (the `web_rss` test monkeypatches `_robots_allowed`, so no sleep/network happens in tests).

- [ ] **Step 5: Commit**

```bash
git add src/discovery/feeds.py src/discovery/models.py src/config.py tests/test_discovery_feeds.py
git commit -m "feat: web_rss outlet kind — generic news RSS/Atom parser + robots gate + page text

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 11: Stage-2 page peek — `captions_fetcher` → `peek_fetcher`, YouTube-gated hydration

**Files:**
- Modify: `src/discovery/classify.py`, `src/discovery/engine.py`, `scripts/poll_discovery.py`, `scripts/eval_discovery_classifier.py`
- Test: `tests/test_discovery_classify.py`, `tests/test_discovery_engine.py` (modify call sites + add)

The fetcher contract changes: it now returns a **plain-text excerpt** (not raw VTT), so one injection point serves both YouTube captions and article pages. `vtt_to_text` moves to the caller side.

- [ ] **Step 1: Update the classify tests (contract change, test-first)**

In `tests/test_discovery_classify.py`:
- Rename every `captions_fetcher=` keyword to `peek_fetcher=` (3 sites: lines ~88, 106, 117).
- The mid-band test (line ~106) currently has its fake fetcher return raw VTT and asserts the prompt got the vtt_to_text-converted form. Change the fake to return plain text directly, e.g. `fake_captions = lambda url: "you have sixty seconds Senator my question is"` (keep the variable name if renaming is noisy), and assert the second prompt contains that exact string.
- Add a new test:

```python
def test_hydration_style_second_pass_uses_plain_excerpt_verbatim():
    prompts = []
    class _P:
        def complete(self, prompt, *, max_tokens, temperature, system=None):
            prompts.append(prompt)
            reply_conf = 0.5 if len(prompts) == 1 else 0.9
            return ('{"relevant": true, "confidence": %s,'
                    ' "candidates_present": [], "event_kind": "debate",'
                    ' "source_tier": 1, "original_vs_clip": "original",'
                    ' "route": "ingest", "why": "w"}' % reply_conf)
    item = RawItem(url="https://www.kctv5.com/2026/08/01/governor-debate/",
                   title="t", description="d", channel_name="KCTV5")
    verdict = classify_item(_P(), item, race_label="KS Governor",
                            roster_names=["Alice Example"],
                            peek_fetcher=lambda url: "full debate transcript text")
    assert verdict.confidence == 0.9
    assert "full debate transcript text" in prompts[1]
```

(Match the reply-JSON shape used by the file's existing fakes — read them first and reuse their helper if one exists.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -v`
Expected: FAIL — `TypeError: classify_item() got an unexpected keyword argument 'peek_fetcher'`

- [ ] **Step 3: Implement the rename in `classify.py`**

In `src/discovery/classify.py`:

1. `classify_item` signature and docstring:

```python
def classify_item(provider, item: RawItem, *, race_label: str, roster_names: list,
                  peek_fetcher=None) -> Verdict:
    """One LLM pass; a second pass with a peek excerpt when confidence lands
    in the mid band and a peek_fetcher is supplied. peek_fetcher(url) returns
    a PLAIN-TEXT excerpt (captions already VTT-stripped, or article-page
    text) or None."""
```

2. Body of the mid-band block — the fetched value is used directly:

```python
    low, high = config.DISCOVERY_CAPTIONS_BAND
    if (peek_fetcher is not None and verdict.rejected_reason is None
            and low <= verdict.confidence < high):
        excerpt = peek_fetcher(item.url)
        if excerpt:
            text2 = provider.complete(
                build_prompt(item, race_label=race_label, roster_names=roster_names,
                             captions_excerpt=excerpt),
                max_tokens=config.DISCOVERY_CLASSIFY_MAX_TOKENS, temperature=0.0,
                system=_SYSTEM)
            second = parse_verdict(text2)
            if second.rejected_reason is None:
                return _filter_candidates(second, roster_names)
    return _filter_candidates(verdict, roster_names)
```

3. In `build_prompt`, change the captions block label:

```python
    if captions_excerpt:
        captions_block = ("\nUnlabeled captions / article-page text excerpt:\n"
                          f"\"\"\"\n{captions_excerpt}\n\"\"\"\n")
```

4. In `_PROMPT_TEMPLATE`, change the sentence `If captions are provided, judge DISCOURSE SHAPE:` to `If a captions or article-page excerpt is provided, judge DISCOURSE SHAPE:` (rest of the sentence unchanged).

5. Also in `_PROMPT_TEMPLATE`, add the web-item routing default (spec: web items default to `quote_source`). Insert this line directly before the `Respond with JSON only:` line:

```
For web/article items (duration unknown, non-video URL): route "quote_source"
unless the page clearly hosts the full event video — then route "ingest".
```

- [ ] **Step 4: Rename through the engine and gate hydration to YouTube**

In `src/discovery/engine.py`:

1. `run_discovery` signature: `captions_fetcher,` → `peek_fetcher,` (line 66).
2. The classify call (line 128–129): `captions_fetcher=captions_fetcher` → `peek_fetcher=peek_fetcher`.
3. Gate hydration (line 94) — yt-dlp hydration is wasted (and failing) work on article URLs; web items get their depth from the page peek instead:

```python
        if ((item.duration_seconds is None or item.description is None)
                and key.startswith("youtube:")):
```

- [ ] **Step 5: Update engine tests + add the hydration-gate test**

In `tests/test_discovery_engine.py`: rename all 10 `captions_fetcher=` keywords to `peek_fetcher=`. Then append:

```python
def test_web_items_are_not_hydrated(monkeypatch):
    inserted = []
    _patch_db(monkeypatch, inserted)
    web_item = RawItem(url="https://www.kctv5.com/2026/08/01/governor-debate/",
                       title="Maria Delgado and Ana Ruiz: full debate",
                       description=None, channel_name="KCTV5",
                       published_at="2026-08-01", outlet_id="o1", via="watchlist")
    hydrate_calls = []
    def hydrate(item):
        hydrate_calls.append(item.url)
        return item
    stats = _run(monkeypatch, inserted,
                 fetch_feed_items=lambda outlet: [web_item],
                 hydrate_fn=hydrate, skip_sweeps=True)
    assert hydrate_calls == []                    # no yt-dlp on article pages
    assert stats.classified == 1                  # still reached stage 2
    assert inserted and inserted[0]["duration_seconds"] is None
```

(Description is None so the old code would have hydrated; the gate must skip it while still classifying. If the item dies at the prefilter because `description=None` breaks name matching, note the title alone carries both names — `match_names` searches title+description and handles None.)

- [ ] **Step 6: Update the two scripts**

In `scripts/poll_discovery.py`, replace `_captions_fetcher` (lines 46–57) with:

```python
def _peek_fetcher(url: str):
    """Stage-2 peek: auto-caption text for YouTube items, article-page text
    for web items. Returns plain text or None; never raises."""
    from src.discovery.classify import vtt_to_text
    if source_key(url).startswith("youtube:"):
        from src.download import download_captions_via_ytdlp
        cache = config.DISCOVERY_DIR / "captions"
        cache.mkdir(parents=True, exist_ok=True)
        safe = hashlib.sha256(source_key(url).encode("utf-8")).hexdigest()[:24]
        dest = cache / f"{safe}.vtt"
        if dest.exists():
            vtt = dest.read_text(encoding="utf-8", errors="replace")
        else:
            path = download_captions_via_ytdlp(url, dest)
            vtt = (Path(path).read_text(encoding="utf-8", errors="replace")
                   if path else None)
        return vtt_to_text(vtt) if vtt else None
    from src.discovery.feeds import fetch_page_text
    try:
        return fetch_page_text(url) or None
    except Exception:  # noqa: BLE001 — the peek is optional; stage 2 proceeds without
        return None
```

and change the engine wiring `captions_fetcher=_captions_fetcher,` → `peek_fetcher=_peek_fetcher,`.

In `scripts/eval_discovery_classifier.py`, change `captions_fetcher=None` → `peek_fetcher=None` (line 40).

- [ ] **Step 7: Run the whole discovery test set**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py tests/test_discovery_engine.py tests/test_discovery_eval.py -v`
Expected: all PASS. Then `grep -rn "captions_fetcher" src/ scripts/ tests/ --include='*.py' | grep -v worktrees` — expected: no hits.

- [ ] **Step 8: Commit**

```bash
git add src/discovery/classify.py src/discovery/engine.py scripts/poll_discovery.py scripts/eval_discovery_classifier.py tests/test_discovery_classify.py tests/test_discovery_engine.py
git commit -m "feat: page peek for web items — peek_fetcher contract + YouTube-gated hydration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 12: Extractability probe on approve→ingest for non-YouTube items

**Files:**
- Modify: `gui/discovery.py`, `gui/app.py`
- Test: `tests/test_gui_discovery.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_discovery.py` (mirror the file's existing approve-ingest tests — they monkeypatch `discovery.get_row`, `discovery.set_status`, `gui.runner.find_meeting_by_source`, and `gui.batch.launch_or_enqueue`; copy that arrangement exactly and adjust):

```python
def test_approve_ingest_probes_non_youtube_and_bounces_on_failure(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    row = _row(url="https://www.kctv5.com/2026/08/01/governor-debate/")
    monkeypatch.setattr(discovery, "get_row", lambda rid: row)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    monkeypatch.setattr(discovery, "probe_extractable",
                        lambda url: (False, "Unsupported URL"))
    launched = []
    monkeypatch.setattr(batch, "launch_or_enqueue",
                        lambda params: launched.append(params) or ("queued", "m1"))
    statuses = []
    monkeypatch.setattr(discovery, "set_status",
                        lambda rid, status, reason=None: statuses.append(status) or True)
    client = TestClient(create_app(), follow_redirects=False)
    resp = client.post("/discovery/d1/approve-ingest")
    assert resp.status_code == 303
    assert "use Edit first" in _flash(resp)
    assert launched == [] and statuses == []      # nothing enqueued, still pending


def test_approve_ingest_skips_probe_for_youtube(monkeypatch):
    import gui.batch as batch
    import gui.runner as runner
    row = _row()                                   # default _row url is YouTube
    monkeypatch.setattr(discovery, "get_row", lambda rid: row)
    monkeypatch.setattr(runner, "find_meeting_by_source", lambda url: None)
    probed = []
    monkeypatch.setattr(discovery, "probe_extractable",
                        lambda url: probed.append(url) or (True, ""))
    monkeypatch.setattr(batch, "launch_or_enqueue", lambda params: ("queued", "m1"))
    monkeypatch.setattr(discovery, "set_status", lambda rid, s, reason=None: True)
    client = TestClient(create_app(), follow_redirects=False)
    resp = client.post("/discovery/d1/approve-ingest")
    assert resp.status_code == 303
    assert probed == []                            # YouTube: no probe spent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v -k probe`
Expected: FAIL — `AttributeError: ... has no attribute 'probe_extractable'`

- [ ] **Step 3: Implement the probe**

Append to `gui/discovery.py`:

```python
def probe_extractable(url: str) -> "tuple[bool, str]":
    """Can yt-dlp actually get a video out of this page? Metadata-only, no
    download. Gate for approve->ingest on non-YouTube items so unextractable
    embeds bounce to Edit-first instead of poisoning the batch pool."""
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                "js_runtimes": {"node": {}}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 — any extractor error = not extractable
        return False, str(exc)[:200]
    if not info:
        return False, "no media found"
    if info.get("entries") is not None and not [e for e in info["entries"] if e]:
        return False, "page has no extractable video"
    return True, ""
```

- [ ] **Step 4: Wire into the approve-ingest route**

In `gui/app.py` `discovery_approve_ingest`, right after the superseded/duplicate check block (after line 118's `return _discovery_redirect(flash)`), add:

```python
        from src.source_key import source_key as _source_key
        if not _source_key(row.url).startswith("youtube:"):
            ok_probe, err = discovery.probe_extractable(row.url)
            if not ok_probe:
                return _discovery_redirect(
                    f"no extractable video ({err or 'nothing found'}) — use Edit first")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/discovery.py gui/app.py tests/test_gui_discovery.py
git commit -m "feat: yt-dlp extractability probe gates approve→ingest for web items

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 13: Runbook + docs

**Files:**
- Modify: `docs/runbooks/source-discovery.md`

- [ ] **Step 1: Rewrite the runbook sections**

Apply these changes to `docs/runbooks/source-discovery.md`:

1. Header block: add spec v2 line under the existing spec line:

```markdown
Spec v2: `docs/superpowers/specs/2026-08-03-source-discovery-v2-design.md`
```

2. **Daily workflow**, insert as new step 2 (renumber the rest): 

```markdown
2. Check the **last run** pill in the header: it must show a `scheduled` run
   within 36 h, status ok. A red "no scheduled run in 36h" pill or a CRASHED
   run means the scheduler is broken — check
   `~/CouncilScribe/discovery/poll.log` and `launchctl list | grep poll-discovery`.
```

3. **Agent gap-filler** prompt: add one sentence before "Never C-SPAN.":

```markdown
> Also check the race's Ballotpedia page and Vote411 for scheduled or recent
> debates/forums; hunt for recordings of past ones, and record upcoming
> events as a note in `why` (advance notice is evidence for the calendar layer).
```

4. **Outlet packs** section: replace the paragraph with:

```markdown
When a state comes inside ~90 days of an election, run an agent to research and
insert 8–15 outlets (`added_via='seed'`): local TV news channels, PBS + NPR
affiliates, LWV state chapter, Clean-Elections/civic-debate orgs, top newspaper
channels. For each outlet register BOTH surfaces where they exist:
- YouTube channel RSS (`kind='youtube_channel'`,
  `feed_url='https://www.youtube.com/feeds/videos.xml?channel_id=UC…'`)
- The site's politics-section news feed (`kind='web_rss'`, the RSS/Atom URL —
  station sites are templated per chain: Gray/Nexstar/Scripps/Sinclair all
  expose section feeds; note the chain in `notes`).

ToS check at registration (spec Q8): skim the site's ToS for an explicit
AI/ML-processing bar (C-SPAN-style). If present, do NOT register the outlet and
record the finding in the pack notes. Generic no-scraping boilerplate does not
disqualify: we poll only public syndication endpoints, respect robots.txt
mechanically, and never touch auth/paywalls. Set `state`, and record the ToS
verdict in `notes`. Outlets are active on insert — the per-item triage gate is
the guard.
```

5. **First-time setup** section: replace entirely with:

```markdown
## Scheduler setup / upgrade

The discovery job runs via `scripts/run_scheduled_poll.sh` from the
automation-checkout clone (`~/CouncilScribe/automation-checkout`) — launchd's
git cannot read `~/Documents`, and the primary checkout's branch is a coin
flip (see the wrapper's header). The wrapper fast-forwards the clone to
origin/main before every run, so **plist changes take effect only after the
branch merges to main.**

1. One-time (already done on this Mac): create the clone per the wrapper's
   FATAL message instructions, and `mkdir -p ~/CouncilScribe/discovery`.
2. Install/refresh the plist (AFTER MERGE TO MAIN):

   ```bash
   cp scripts/launchd/vote.empowered.poll-discovery.plist ~/Library/LaunchAgents/
   launchctl unload ~/Library/LaunchAgents/vote.empowered.poll-discovery.plist 2>/dev/null
   launchctl load ~/Library/LaunchAgents/vote.empowered.poll-discovery.plist
   launchctl kickstart gui/$(id -u)/vote.empowered.poll-discovery
   sleep 30 && tail -20 ~/CouncilScribe/discovery/poll.log
   ```

   Expected: `=== scheduled poll … ===`, a `code: <sha>` line, engine output
   ending in `DONE examined=…`, and a new row in `essentials.discovery_runs`
   (`trigger_kind='scheduled'`) visible in the GUI header.
3. The agenda poll's installed plist still passes no script argument — the
   wrapper defaults to `scripts/poll_agendas.py`, so it keeps working
   untouched. To make it explicit, prepend `scripts/poll_agendas.py` to its
   ProgramArguments after the wrapper path.
```

6. Add a new final section:

```markdown
## Eval upkeep

After each triage session:

    .venv/bin/python scripts/harvest_discovery_verdicts.py
    .venv/bin/python scripts/eval_discovery_classifier.py --models haiku

Approved/ingested rows become gold-relevant examples; rejects count as
gold-irrelevant only for relevance reasons (clip-not-original / wrong-person /
tier-5). Re-run the eval and watch recall, precision, and the calibration
block (Brier + buckets). Commit the updated
`tests/fixtures/discovery_eval_real.jsonl`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/source-discovery.md
git commit -m "docs: source-discovery runbook — scheduler wrapper, web_rss packs, ToS check, eval upkeep

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 14: Full suite + E2E verification + wrap-up

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: everything passes (v1 baseline was 1681 tests; expect ~30+ more). Fix any stragglers before proceeding.

- [ ] **Step 2: Register one real station feed and prove the web_rss lane (manual, cheap)**

Pick a station from the evidence list with a politics RSS feed (e.g. Gray's KCTV5: check `https://www.kctv5.com/arc/outboundfeeds/rss/category/news/politics/?outputType=xml` — if that 404s, find the feed link in the page source; any real station politics feed works). Then:

```bash
.venv/bin/python - <<'PY'
import os, psycopg2
from gui.env import load_env_local
load_env_local()
conn = psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")
cur = conn.cursor()
cur.execute("""
    insert into essentials.source_outlets (name, kind, feed_url, state, added_via, notes)
    values (%s, 'web_rss', %s, %s, 'manual',
            'v2 E2E verification outlet; ToS checked: no AI bar')
    on conflict (feed_url) do nothing returning id
""", ("KCTV5 politics (web)", "<FEED_URL_FOUND_ABOVE>", "KS"))
print(cur.fetchone())
conn.commit(); conn.close()
PY
.venv/bin/python scripts/poll_discovery.py --skip-sweeps 2>&1 | tail -6
```

Expected: the run completes, `DONE` line prints, and — if the feed currently carries an item naming a tracked candidate — a `QUEUED [watchlist] …` line with a station URL. (A quiet feed producing zero queued items is still a pass for plumbing: the poll must not error and the run record must show the outlet was polled without a failure.) Deactivate the outlet afterward if it's noise: `update essentials.source_outlets set active=false where name='KCTV5 politics (web)'`.

- [ ] **Step 3: Check the spec's success criteria that are checkable now**

- Criterion 3 (stale rejects ≈ 0): confirmed by Task 6's tests; real-world confirmation accrues over the next triage sessions.
- Criterion 4 (eval ≥30 real-labeled with calibration): confirmed in Task 8 Step 10 output.
- Criteria 1–2 (7 unattended runs; ≥1 station source end-to-end) start counting after merge + plist refresh — they are operating milestones, not code steps. Note them in the PR body.

- [ ] **Step 4: Finish the branch**

Use the **superpowers:finishing-a-development-branch** skill: push the branch, open a PR against main titled "Source discovery v2: operational trust + web_rss watchlist layer", PR body summarizing the three slices + the two post-merge operating steps (plist refresh via runbook §Scheduler setup; watch for 7 recorded runs). Remember the ev-accounts migration commit (Task 1) ships separately in that repo.

---

## Post-merge operating checklist (ops, not code — copy into the PR body)

1. Refresh the discovery plist per runbook §"Scheduler setup / upgrade" and kickstart once; confirm a `scheduled` row in `discovery_runs` and a fresh `poll.log`.
2. Run the WI Governor / FL Senate / WY Senate gap-filler agents (runbook §Agent gap-filler) — do not wait for any of this code.
3. Run outlet packs for remaining Aug/Sep primary states, now registering `web_rss` feeds too.
4. After each triage session: harvest verdicts + re-run the eval (runbook §Eval upkeep).
5. Watch for 7 consecutive unattended runs (success criterion 1) and the first station-site source reaching a meeting end-to-end (criterion 2).

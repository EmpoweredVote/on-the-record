"""Tests for src.discovery.autoapprove — the auto-approve sweep.

Safety invariant under test: auto_approve_pending may ONLY ever set
status='approved', route='quote_source'. It must never set status='ingested'
and must never gate on outlet_ingest_barred (the bar is on ingest, not on
quoting — barred outlets are still eligible here; a separate gate elsewhere
blocks approve-ingest for them).

No local/seeded discovery DB exists in this project (see tests/conftest.py) —
DB-touching behavior is verified via SQL construction on a fake cursor, the
project's established pattern for cursor-taking functions (mirrors the
_capture_conn helper in tests/test_gui_discovery.py and the _FakeCursor in
tests/test_gui_coverage.py).

A live_db-gated integration test is deliberately NOT included: the SQL here
reads essentials.source_outlets.trusted, which only exists once the Task 1
migration (ev-accounts commit 37313d43, migration 1863) is applied — and
that's explicitly gated on Chris's go-ahead, not yet applied to any database.
A live test today would either no-op against the wrong column set or error
outright; it couldn't confirm anything. Worth adding once the migration is
live (see .superpowers/sdd/2026-09-16-discovery-review-reorg-slice1/).
"""
from __future__ import annotations

from src.discovery.autoapprove import ELIGIBLE_LANE_SQL, auto_approve_pending
from src.discovery.lanes import FORMAL_EVENT_KINDS, content_lane


# --- 1. Pure: the eligibility WHERE fragment's shape (plan's Step 1 test) ---

def test_eligible_where_shape():
    # The fragment must require: trusted outlet, pending, race_id present,
    # original_vs_clip = 'clip', and event_kind NOT in the formal set.
    frag = ELIGIBLE_LANE_SQL
    for needle in ("o.trusted", "d.status = 'pending'", "d.race_id is not null",
                   "d.original_vs_clip = 'clip'", "not in %s"):
        assert needle in frag


def test_questionnaire_never_matches_eligible_lane_sql():
    """A questionnaire (lane='questionnaire' per content_lane — a high-value
    written quote source) must never be auto-approved by the sweep: it's a web
    page, not a video clip, so it needs a human's Approve -> quote source
    click, never a robo-approval. There is no local discovery DB (see module
    docstring), so — mirroring test_eligible_where_shape's structural style —
    this reproduces ELIGIBLE_LANE_SQL's AND-ed conditions as plain Python
    predicates and evaluates them against a fully trusted, pending, raced
    questionnaire row: even with every other gate open, it fails solely on
    'd.original_vs_clip = 'clip'' — the same field content_lane ignores when
    tagging a questionnaire row for display."""
    row = dict(trusted=True, status="pending", race_id="r1",
               original_vs_clip="original", event_kind_guess="questionnaire")
    assert content_lane(row["original_vs_clip"], row["event_kind_guess"]) == "questionnaire"

    assert "d.original_vs_clip = 'clip'" in ELIGIBLE_LANE_SQL
    eligible = (
        row["trusted"]
        and row["status"] == "pending"
        and row["race_id"] is not None
        and row["original_vs_clip"] == "clip"
        and row["event_kind_guess"] not in FORMAL_EVENT_KINDS
    )
    assert eligible is False
    # Not smuggled in via the formal-kinds set either — a questionnaire isn't
    # a FORMAL_EVENT_KIND, so the exclusion rests entirely on the clip gate.
    assert row["event_kind_guess"] not in FORMAL_EVENT_KINDS
    assert row["original_vs_clip"] != "clip"


def test_eligible_lane_sql_never_references_ingest_barred_or_ingested():
    # Barred outlets are still eligible for auto-quote-source — the bar is on
    # ingest only, applied elsewhere. And this lane never ingests, full stop.
    assert "ingest_barred" not in ELIGIBLE_LANE_SQL
    assert "ingested" not in ELIGIBLE_LANE_SQL


# --- 2. Fake-cursor: SQL construction for auto_approve_pending ---

class _FakeCursor:
    """Records the one execute() call; replays a canned rowcount."""

    def __init__(self, rowcount=0):
        self.rowcount = rowcount
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params


def test_auto_approve_pending_sql_shape_no_outlet():
    cur = _FakeCursor(rowcount=3)
    n = auto_approve_pending(cur)
    assert n == 3
    sql = cur.sql.lower()
    # The safety invariant, spelled out at the SQL level:
    assert "status = 'approved'" in sql
    assert "route = 'quote_source'" in sql
    assert "ingested" not in sql
    assert "status_reason = 'auto: trusted outlet'" in sql
    # Eligibility conditions present:
    assert "o.trusted" in sql
    assert "d.status = 'pending'" in sql
    assert "d.race_id is not null" in sql
    assert "d.original_vs_clip = 'clip'" in sql
    assert "not in %s" in sql
    # No outlet filter when outlet_id is not passed:
    assert "o.id = %s::uuid" not in sql
    assert cur.params == (tuple(FORMAL_EVENT_KINDS),)


def test_auto_approve_pending_sql_shape_with_outlet_id():
    cur = _FakeCursor(rowcount=1)
    outlet_id = "00000000-0000-0000-0000-000000000001"
    n = auto_approve_pending(cur, outlet_id=outlet_id)
    assert n == 1
    sql = cur.sql.lower()
    assert "o.id = %s::uuid" in sql
    assert cur.params == (tuple(FORMAL_EVENT_KINDS), outlet_id)

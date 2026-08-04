"""Wiring tests for scripts/poll_discovery.py: pins the transaction-ordering
invariants, each backed by its own assertion below —
  - run record opens (and commits) before the engine runs
  - alarms print before persistence, even when persistence then fails
  - finish_run commits before record_alarms
  - dry-run writes no run record
  - a mid-run engine crash leaves the started row unfinished
using fakes. No DB, no network.

scripts/ has no package __init__, so it's added to sys.path directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import poll_discovery  # noqa: E402


class _FakeConn:
    """Records every commit(); cursor()/close() are no-ops the fakes ignore."""

    def __init__(self, log):
        self._log = log

    def cursor(self):
        return object()

    def commit(self):
        self._log.append("commit")

    def close(self):
        pass


def _patch_common(monkeypatch, log):
    """Patch every poll_discovery collaborator except engine.run_discovery
    (each test sets that one up differently) with fakes that log into the
    shared call log."""

    def _fake_insert_run(cur, trigger_kind):
        log.append("insert_run")
        return "fake-run-id"

    def _fake_finish_run(cur, run_id, stats):
        log.append("finish_run")

    def _fake_record_alarms(cur, race_ids):
        log.append("record_alarms")

    def _fake_alarm_races(cur, days=30):
        return [("race-1", "WI Gov", "2026-08-11")]

    monkeypatch.setattr(poll_discovery.db, "connect", lambda: _FakeConn(log))
    monkeypatch.setattr(poll_discovery, "get_provider", lambda *a, **kw: object())
    monkeypatch.setattr(poll_discovery, "_meeting_source_keys", lambda: set())
    monkeypatch.setattr(poll_discovery.db, "insert_run", _fake_insert_run)
    monkeypatch.setattr(poll_discovery.db, "finish_run", _fake_finish_run)
    monkeypatch.setattr(poll_discovery.db, "record_alarms", _fake_record_alarms)
    monkeypatch.setattr(poll_discovery.db, "alarm_races", _fake_alarm_races)


def _make_engine_stub(log, *, raise_error=False, stats=None):
    def _stub(conn, **kwargs):
        log.append("engine")
        if raise_error:
            raise RuntimeError("boom")
        return stats
    return _stub


def _subsequence(needle: list, haystack: list) -> bool:
    """True when every item of needle appears in haystack, in order (not
    necessarily contiguous)."""
    it = iter(haystack)
    return all(item in it for item in needle)


def test_run_record_opens_and_commits_before_engine(monkeypatch):
    log = []
    _patch_common(monkeypatch, log)
    stats = poll_discovery.engine.RunStats()  # zero counts, empty failures
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                         _make_engine_stub(log, stats=stats))
    monkeypatch.setattr(sys, "argv", ["poll_discovery.py"])

    rc = poll_discovery.main()

    assert rc == 0
    assert log[:3] == ["insert_run", "commit", "engine"]


def test_finish_commit_precedes_record_alarms(monkeypatch):
    log = []
    _patch_common(monkeypatch, log)
    stats = poll_discovery.engine.RunStats()
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                         _make_engine_stub(log, stats=stats))
    monkeypatch.setattr(sys, "argv", ["poll_discovery.py"])

    rc = poll_discovery.main()

    assert rc == 0
    assert _subsequence(["finish_run", "commit", "record_alarms", "commit"], log), log


def test_dry_run_writes_no_run_record(monkeypatch):
    log = []
    _patch_common(monkeypatch, log)
    stats = poll_discovery.engine.RunStats()
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                         _make_engine_stub(log, stats=stats))
    monkeypatch.setattr(sys, "argv", ["poll_discovery.py", "--dry-run"])

    rc = poll_discovery.main()

    assert rc == 0
    assert "insert_run" not in log
    assert "finish_run" not in log
    assert "record_alarms" not in log


def test_engine_crash_leaves_started_row_unfinished(monkeypatch):
    log = []
    _patch_common(monkeypatch, log)
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                         _make_engine_stub(log, raise_error=True))
    monkeypatch.setattr(sys, "argv", ["poll_discovery.py"])

    with pytest.raises(RuntimeError, match="boom"):
        poll_discovery.main()

    assert "insert_run" in log
    assert "commit" in log
    assert "finish_run" not in log


def test_alarms_print_before_persistence_even_when_it_fails(monkeypatch, capsys):
    log = []
    _patch_common(monkeypatch, log)

    def _raise_record_alarms(cur, race_ids):
        log.append("record_alarms")
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(poll_discovery.db, "record_alarms", _raise_record_alarms)
    stats = poll_discovery.engine.RunStats()
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                         _make_engine_stub(log, stats=stats))
    monkeypatch.setattr(sys, "argv", ["poll_discovery.py"])

    with pytest.raises(RuntimeError, match="db hiccup"):
        poll_discovery.main()

    assert "ALARM" in capsys.readouterr().out

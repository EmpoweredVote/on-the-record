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

    def _fake_apply_tier3_defer(cur):
        log.append("defer")
        return 0

    monkeypatch.setattr(poll_discovery.db, "connect", lambda: _FakeConn(log))
    monkeypatch.setattr(poll_discovery, "get_provider", lambda *a, **kw: object())
    monkeypatch.setattr(poll_discovery, "_meeting_source_keys", lambda: set())
    monkeypatch.setattr(poll_discovery.db, "insert_run", _fake_insert_run)
    monkeypatch.setattr(poll_discovery.db, "finish_run", _fake_finish_run)
    monkeypatch.setattr(poll_discovery.db, "record_alarms", _fake_record_alarms)
    monkeypatch.setattr(poll_discovery.db, "alarm_races", _fake_alarm_races)
    monkeypatch.setattr(poll_discovery.db, "apply_tier3_defer", _fake_apply_tier3_defer)


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
    assert "defer" not in log   # dry-run must not invoke the tier-3 defer sweep


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


# --- _peek_fetcher ----------------------------------------------------------

def test_peek_fetcher_youtube_routes_to_captions(monkeypatch, tmp_path):
    monkeypatch.setattr(poll_discovery.config, "DISCOVERY_DIR", tmp_path)
    calls = []

    def fake_download(url, dest):
        calls.append((url, dest))
        dest.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nI will cut taxes",
                        encoding="utf-8")
        return dest

    monkeypatch.setattr("src.download.download_captions_via_ytdlp", fake_download)
    text = poll_discovery._peek_fetcher("https://www.youtube.com/watch?v=abc12345678")
    assert len(calls) == 1
    assert text == "I will cut taxes"          # vtt_to_text applied on the caller side


def test_peek_fetcher_web_routes_to_page_text(monkeypatch, tmp_path):
    monkeypatch.setattr(poll_discovery.config, "DISCOVERY_DIR", tmp_path)
    monkeypatch.setattr("src.discovery.feeds.fetch_page_text",
                        lambda url: "article body text")
    text = poll_discovery._peek_fetcher("https://www.kctv5.com/2026/08/01/governor-debate/")
    assert text == "article body text"


def test_peek_fetcher_youtube_branch_error_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(poll_discovery.config, "DISCOVERY_DIR", tmp_path)

    def boom(url, dest):
        raise OSError("disk full")

    monkeypatch.setattr("src.download.download_captions_via_ytdlp", boom)
    assert poll_discovery._peek_fetcher(
        "https://www.youtube.com/watch?v=abc12345678") is None


def test_peek_fetcher_web_branch_error_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(poll_discovery.config, "DISCOVERY_DIR", tmp_path)

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr("src.discovery.feeds.fetch_page_text", boom)
    assert poll_discovery._peek_fetcher(
        "https://www.kctv5.com/2026/08/01/governor-debate/") is None


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


def test_defer_sweep_runs_after_finish_run(monkeypatch):
    log = []
    _patch_common(monkeypatch, log)
    stats = poll_discovery.engine.RunStats()
    monkeypatch.setattr(poll_discovery.engine, "run_discovery",
                         _make_engine_stub(log, stats=stats))
    monkeypatch.setattr(sys, "argv", ["poll_discovery.py"])

    rc = poll_discovery.main()

    assert rc == 0
    assert "finish_run" in log and "record_alarms" in log and "defer" in log
    assert log.index("defer") > log.index("finish_run")   # defer runs in finalize, after the record
    assert log.index("defer") > log.index("record_alarms")   # ...and after alarms are committed

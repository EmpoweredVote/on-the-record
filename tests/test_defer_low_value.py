import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import defer_low_value  # noqa: E402


class _FakeConn:
    def __init__(self, log):
        self._log = log
    def cursor(self):
        return object()
    def commit(self):
        self._log.append("commit")
    def close(self):
        self._log.append("close")


def test_defer_low_value_runs_sweep_commits_and_reports(monkeypatch, capsys):
    log = []
    monkeypatch.setattr(defer_low_value.db, "connect", lambda: _FakeConn(log))
    monkeypatch.setattr(defer_low_value.db, "apply_tier3_defer", lambda cur: 970)
    rc = defer_low_value.main()
    assert rc == 0
    assert "DEFERRED 970" in capsys.readouterr().out
    assert log == ["commit", "close"]   # commit before close

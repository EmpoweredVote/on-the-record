from __future__ import annotations

import json
import os
import time

from src.name_suggest import Deps
from src.name_suggest_step import run_name_suggestions, skip_reason

SEGS = [{"segment_id": 0, "start_time": 0, "end_time": 30, "speaker_label": "W",
         "text": "My name is Rachel Sample, I'm with Hoosier Families and thank the committee for its time today."}]


def _write(tmp_path, **extra):
    m = {"meeting_id": "m1", "city": "Indianapolis", "event_kind": "council", "segments": SEGS,
         "speakers": {}, **extra}
    (tmp_path / "transcript_named.json").write_text(json.dumps(m))
    return m


def _deps_factory(found=None):
    def make(meeting_dir):
        return Deps(db=None, researcher=lambda *a, **k: found, fetch=lambda u: ""), [], []
    return make


def test_skip_rules(tmp_path):
    m = _write(tmp_path)
    assert skip_reason(tmp_path, m, disabled=True) == "disabled (--no-suggest-names)"
    assert skip_reason(tmp_path, {**m, "event_kind": "floor"}) == "floor session"
    assert skip_reason(tmp_path, {**m, "speakers": {"W": {"speaker_name": "Rachel Sample"}}}) == "no unnamed speakers"
    assert skip_reason(tmp_path, m) is None
    (tmp_path / "name_suggestions.json").write_text("{}")
    future = time.time() + 5
    os.utime(tmp_path / "name_suggestions.json", (future, future))
    assert skip_reason(tmp_path, m) == "up to date"
    assert skip_reason(tmp_path, m, force=True) is None


def test_run_writes_file(tmp_path):
    _write(tmp_path)
    out = run_name_suggestions(tmp_path, make_deps=_deps_factory())
    assert out is not None and [s["label"] for s in out["suggestions"]] == ["W"]
    assert json.loads((tmp_path / "name_suggestions.json").read_text())["suggestions"][0]["label"] == "W"


def test_run_never_raises(tmp_path, capsys):
    _write(tmp_path)

    def boom(meeting_dir):
        raise RuntimeError("db exploded")

    assert run_name_suggestions(tmp_path, make_deps=boom) is None
    assert "Name suggestions failed" in capsys.readouterr().out
    written = json.loads((tmp_path / "name_suggestions.json").read_text())
    assert written["suggestions"] == [] and "db exploded" in written["warnings"][0]
    assert run_name_suggestions(tmp_path / "missing", make_deps=_deps_factory()) is None


def test_run_skipped_prints_reason(tmp_path, capsys):
    _write(tmp_path, event_kind="floor")
    assert run_name_suggestions(tmp_path, make_deps=_deps_factory()) is None
    assert "skipped: floor session" in capsys.readouterr().out

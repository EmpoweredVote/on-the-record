"""The shared crash-safe writer for on-disk artifacts.

A killed process must never leave a half-written transcript_named.json behind:
that truncation is invisible until some later reader (GUI workspace, review,
publish, backfills) fails to parse it. Every write goes to a sibling temp file
first and lands via os.replace, which is atomic on macOS/Linux.
"""
from __future__ import annotations

import json
import os
import pathlib
from pathlib import Path

import pytest

from src.atomic_io import atomic_write_json, atomic_write_text


def _existing(tmp_path: Path) -> Path:
    """A meeting artifact already on disk, with parseable content."""
    path = tmp_path / "transcript_named.json"
    path.write_text(json.dumps({"meeting_id": "old", "segments": [1, 2, 3]}), encoding="utf-8")
    return path


def test_write_text_replaces_contents_and_leaves_no_temp_file(tmp_path):
    path = _existing(tmp_path)
    atomic_write_text(path, "new contents")
    assert path.read_text(encoding="utf-8") == "new contents"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["transcript_named.json"]


def test_write_json_round_trips_and_indents(tmp_path):
    path = tmp_path / "transcript_named.json"
    atomic_write_json(path, {"meeting_id": "m1", "segments": []})
    assert json.loads(path.read_text(encoding="utf-8")) == {"meeting_id": "m1", "segments": []}
    assert '\n  "meeting_id"' in path.read_text(encoding="utf-8")  # indent=2, like json.dump


def test_temp_file_is_a_sibling_named_dot_tmp(tmp_path, monkeypatch):
    # The temp name matters: it lives beside the target (same filesystem, so the
    # rename is atomic) and must not be mistaken for an artifact by any scanner.
    path = _existing(tmp_path)
    seen = {}

    def spy(src, dst):
        seen["src"], seen["dst"] = Path(src), Path(dst)
        os.unlink(src)

    monkeypatch.setattr("src.atomic_io.os.replace", spy)
    atomic_write_text(path, "x")
    assert seen["src"] == tmp_path / "transcript_named.json.tmp"
    assert seen["dst"] == path


def test_crash_before_rename_leaves_previous_file_intact(tmp_path, monkeypatch):
    path = _existing(tmp_path)
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated kill between write and rename")

    monkeypatch.setattr("src.atomic_io.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(path, "replacement that never lands")

    assert path.read_text(encoding="utf-8") == before
    assert json.loads(path.read_text(encoding="utf-8"))["meeting_id"] == "old"
    assert not (tmp_path / "transcript_named.json.tmp").exists()  # no turd left behind


def test_partial_write_leaves_previous_file_intact_and_parseable(tmp_path, monkeypatch):
    # The real failure: the process dies partway through dumping the JSON. With a
    # plain open(path, "w") that truncates the meeting; here only the temp file is
    # half-written, so the target keeps its previous, parseable content.
    path = _existing(tmp_path)
    before = json.loads(path.read_text(encoding="utf-8"))
    real_write_text = pathlib.Path.write_text

    def die_halfway(self, data, *args, **kwargs):
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("simulated kill mid-dump")

    monkeypatch.setattr(pathlib.Path, "write_text", die_halfway)
    with pytest.raises(OSError):
        atomic_write_json(path, {"meeting_id": "new", "segments": [4, 5, 6]})
    monkeypatch.undo()

    assert json.loads(path.read_text(encoding="utf-8")) == before
    assert not (tmp_path / "transcript_named.json.tmp").exists()


def test_unserializable_payload_never_touches_the_target(tmp_path):
    path = _existing(tmp_path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(TypeError):
        atomic_write_json(path, {"segments": object()})
    assert path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "transcript_named.json.tmp").exists()


def test_writes_a_brand_new_file(tmp_path):
    path = tmp_path / "quality.json"
    atomic_write_json(path, {"verdict": "pass"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"verdict": "pass"}


def test_gui_review_api_re_exports_the_shared_writer():
    # gui/review_api.py was the original home; run_local/src can't import from gui,
    # so the function moved to src and is re-exported for existing callers.
    import src.atomic_io
    from gui.review_api import _atomic_write_text
    assert _atomic_write_text is src.atomic_io.atomic_write_text

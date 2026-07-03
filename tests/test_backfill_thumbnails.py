from __future__ import annotations

from pathlib import Path

from backfill_thumbnails import meetings_needing_thumbnail


def _mk(mdir: Path, *, source=True, thumb=False):
    mdir.mkdir(parents=True)
    if source:
        (mdir / "source.webm").write_bytes(b"x")
    if thumb:
        (mdir / "thumbnail.jpg").write_bytes(b"x")


def test_lists_only_dirs_with_source_and_no_thumbnail(tmp_path: Path):
    _mk(tmp_path / "needs-it")                 # source, no thumb -> included
    _mk(tmp_path / "has-thumb", thumb=True)    # already has thumb -> skipped
    _mk(tmp_path / "no-source", source=False)  # no video -> skipped
    (tmp_path / "loose.txt").write_text("x")   # not a dir -> ignored

    out = meetings_needing_thumbnail(tmp_path)
    assert out == [tmp_path / "needs-it"]


def test_empty_when_dir_missing(tmp_path: Path):
    assert meetings_needing_thumbnail(tmp_path / "nope") == []

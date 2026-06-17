"""Tests for the --review-queue lister."""
from __future__ import annotations

import json

import run_local
from src import config
from src.checkpoint import PipelineState


def _make_meeting(root, mid, verdict, coverage):
    mdir = root / mid
    mdir.mkdir(parents=True)
    state = PipelineState(mdir)
    state.review_status = verdict
    state.trusted_coverage = coverage
    state.save()


def test_review_queue_groups_and_ranks(tmp_path, monkeypatch, capsys):
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    monkeypatch.setattr(config, "MEETINGS_DIR", meetings_dir)
    _make_meeting(meetings_dir, "2026-01-01-a", "review", 0.62)
    _make_meeting(meetings_dir, "2026-01-02-b", "review", 0.81)
    _make_meeting(meetings_dir, "2026-01-03-c", "failed", 0.30)
    _make_meeting(meetings_dir, "2026-01-04-d", "pass", 0.97)

    run_local._review_queue()
    out = capsys.readouterr().out

    # review section present, ranked desc (b before a)
    assert out.index("2026-01-02-b") < out.index("2026-01-01-a")
    # failed/low-yield section present
    assert "2026-01-03-c" in out
    # passing meeting is summarized, not listed in a review section
    assert "1 passing" in out or "pass: 1" in out.lower()


def test_review_queue_empty(tmp_path, monkeypatch, capsys):
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    monkeypatch.setattr(config, "MEETINGS_DIR", meetings_dir)
    run_local._review_queue()
    out = capsys.readouterr().out
    assert "No meetings" in out or "0" in out

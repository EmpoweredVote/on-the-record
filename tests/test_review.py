"""Tests for the pure speaker-review core (spec 2026-06-08-unified-cli-review)."""
from __future__ import annotations

import numpy as np
import pytest

from src.models import Segment, SpeakerMapping
from src import review


def _seg(label, start, end, text=""):
    return Segment(segment_id=0, start_time=start, end_time=end, speaker_label=label, text=text)


class _FakeProfile:
    def __init__(self, display_name, centroid):
        self.display_name = display_name
        self.centroid = centroid
        self.embeddings = [centroid]


class _FakeProfileDB:
    def __init__(self, profiles):
        self.profiles = profiles  # id -> _FakeProfile


def test_build_review_state_orders_by_speech_desc():
    segments = [
        _seg("SPEAKER_00", 0, 5, "hello"),
        _seg("SPEAKER_01", 5, 35, "a much longer turn"),
    ]
    mappings = {
        "SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00"),
        "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01"),
    }
    views = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=True)
    assert [v.label for v in views] == ["SPEAKER_01", "SPEAKER_00"]
    assert views[0].total_speech_seconds == 30.0
    assert views[0].seg_count == 1


def test_build_review_state_show_text_toggle():
    segments = [_seg("SPEAKER_00", 0, 5, "hello there")]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    with_text = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=True)
    no_text = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=False)
    assert with_text[0].sample_text == "hello there"
    assert no_text[0].sample_text is None
    assert with_text[0].clip_start == 0.0
    assert no_text[0].clip_start == 0.0


def test_build_review_state_includes_soft_hints():
    vec = np.array([1.0, 0.0, 0.0])
    segments = [_seg("SPEAKER_00", 0, 10)]
    mappings = {"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00")}
    embeddings = {"SPEAKER_00": vec}
    db = _FakeProfileDB({"mayor-jones": _FakeProfile("Mayor Jones", vec)})
    views = review.build_review_state(segments, mappings, embeddings, db, show_text=False)
    assert views[0].soft_hints, "expected a voice hint for an identical embedding"
    assert views[0].soft_hints[0][0] == "Mayor Jones"
    assert views[0].soft_hints[0][1] == pytest.approx(1.0, abs=1e-6)


def test_build_review_state_needs_review_flag():
    segments = [_seg("SPEAKER_00", 0, 10), _seg("SPEAKER_01", 10, 20)]
    m = SpeakerMapping(speaker_label="SPEAKER_00")
    m.needs_review = True
    mappings = {"SPEAKER_00": m, "SPEAKER_01": SpeakerMapping(speaker_label="SPEAKER_01")}
    views = review.build_review_state(segments, mappings, {}, _FakeProfileDB({}), show_text=False)
    by_label = {v.label: v for v in views}
    assert by_label["SPEAKER_00"].needs_review is True
    assert by_label["SPEAKER_01"].needs_review is False

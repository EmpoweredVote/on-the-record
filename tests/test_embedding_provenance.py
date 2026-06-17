"""Tests for per-embedding provenance (EmbeddingRecord) — schema v4."""
from __future__ import annotations

import numpy as np

from src import config
from src.enroll import (
    EmbeddingRecord,
    ProfileDB,
    StoredProfile,
    enroll_speakers,
    merge_profiles,
    load_profiles,
    save_profiles,
    _db_path,
)
from src.models import Segment, SpeakerMapping


def _emb():
    return np.random.randn(8).astype(np.float32)


def _mapping(name, label="SPEAKER_01"):
    return {label: SpeakerMapping(
        speaker_label=label, speaker_name=name, confidence=0.95,
        id_method="human_review")}


def _segments(label="SPEAKER_01", n=3):
    return [Segment(segment_id=i, start_time=float(i), end_time=float(i) + 1.0,
                    speaker_label=label, text="hi") for i in range(n)]


def test_schema_version_is_4():
    assert config.PROFILE_SCHEMA_VERSION == 4
    assert ProfileDB().schema_version == 4


def test_enroll_stamps_meeting_id_and_seg_count():
    db = enroll_speakers(ProfileDB(), {"SPEAKER_01": _emb()},
                         _mapping("John Public"), "m1", _segments(n=3))
    profile = db.profiles["public_john"]
    assert len(profile.embeddings) == 1
    rec = profile.embeddings[0]
    assert isinstance(rec, EmbeddingRecord)
    assert rec.meeting_id == "m1"
    assert rec.seg_count == 3


def test_centroid_excluding_drops_only_matching_meeting():
    profile = StoredProfile(
        speaker_id="x", display_name="X",
        embeddings=[
            EmbeddingRecord(np.array([1.0, 0.0]), "m1"),
            EmbeddingRecord(np.array([0.0, 1.0]), "m2"),
        ],
    )
    held = profile.centroid_excluding("m1")
    np.testing.assert_allclose(held, np.array([0.0, 1.0]))


def test_centroid_excluding_singleton_returns_none():
    profile = StoredProfile(
        speaker_id="x", display_name="X",
        embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "m1")],
    )
    assert profile.centroid_excluding("m1") is None


def test_centroid_excluding_unrelated_meeting_returns_full_centroid():
    profile = StoredProfile(
        speaker_id="x", display_name="X",
        embeddings=[
            EmbeddingRecord(np.array([1.0, 0.0]), "m1"),
            EmbeddingRecord(np.array([3.0, 0.0]), "m2"),
        ],
    )
    held = profile.centroid_excluding("m99")
    np.testing.assert_allclose(held, np.array([2.0, 0.0]))


def test_merge_preserves_both_sources_provenance():
    db = ProfileDB(profiles={
        "src": StoredProfile(speaker_id="src", display_name="S",
                             embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "m1")],
                             meetings_seen=["m1"]),
        "dst": StoredProfile(speaker_id="dst", display_name="D",
                             embeddings=[EmbeddingRecord(np.array([0.0, 1.0]), "m2")],
                             meetings_seen=["m2"]),
    })
    assert merge_profiles(db, "src", "dst") is True
    dst = db.profiles["dst"]
    seen = sorted(r.meeting_id for r in dst.embeddings)
    assert seen == ["m1", "m2"]


def test_v4_db_round_trips_through_pickle(monkeypatch, tmp_path):
    path = tmp_path / "speaker_profiles.pkl"
    monkeypatch.setattr("src.enroll._db_path", lambda: path)
    db = ProfileDB(profiles={
        "x": StoredProfile(speaker_id="x", display_name="X",
                           embeddings=[EmbeddingRecord(np.array([1.0, 2.0]), "m1", 5)],
                           meetings_seen=["m1"]),
    })
    save_profiles(db)
    loaded = load_profiles()
    assert loaded.schema_version == 4
    rec = loaded.profiles["x"].embeddings[0]
    assert isinstance(rec, EmbeddingRecord)
    assert rec.meeting_id == "m1"
    assert rec.seg_count == 5
    np.testing.assert_allclose(rec.vector, np.array([1.0, 2.0]))

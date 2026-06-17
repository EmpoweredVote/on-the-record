"""The meeting id stamped into voice profiles must be the meeting DIRECTORY
basename — the same key calibrate_gate's leave-one-out uses
(``_decontaminated_centroids(profile_db, meeting_dir.name)``).

If enrollment ever stamps a different identifier (a caller-supplied string or a
persisted ``meeting.meeting_id`` that has drifted from the directory name),
``centroid_excluding`` fails to drop the scored meeting's own embeddings and the
meeting silently grades itself. These tests pin enrollment to ``meeting_dir.name``.
"""
from __future__ import annotations

import json

import numpy as np

import run_local
from src.enroll import load_profiles
from src.models import SpeakerMapping


def test_enroll_after_review_stamps_directory_basename(tmp_path, monkeypatch):
    """_enroll_after_review derives the provenance key from meeting_dir.name,
    not from any caller-supplied or persisted meeting id."""
    meeting_dir = tmp_path / "2026-02-10-regular-session"
    meeting_dir.mkdir()

    # A decoy transcript with a DIVERGENT meeting_id. The old code path
    # (_review_meeting) passed meeting.meeting_id straight through; enrollment
    # must ignore it entirely and key on the directory name.
    (meeting_dir / "transcript_named.json").write_text(
        json.dumps({"meeting_id": "some-other-id-that-must-not-be-used"})
    )

    label = "SPEAKER_01"
    (meeting_dir / "embeddings.json").write_text(
        json.dumps({label: [0.1, 0.2, 0.3, 0.4]})
    )

    # Drive the interactive enrollment to completion.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    changes = [{"label": label, "new_name": "Jane Q. Public", "old_name": None}]
    mappings = {label: SpeakerMapping(
        speaker_label=label, speaker_name="Jane Q. Public",
        confidence=0.95, id_method="human_review")}

    run_local._enroll_after_review(changes, mappings, meeting_dir, segments=[])

    db = load_profiles()
    assert "public_jane" in db.profiles, "speaker should have been enrolled"
    records = db.profiles["public_jane"].embeddings
    assert records, "an EmbeddingRecord should have been stamped"
    assert {r.meeting_id for r in records} == {"2026-02-10-regular-session"}, (
        "enrollment must stamp the meeting directory basename, never a divergent "
        "persisted/caller-supplied meeting id"
    )


def test_simple_meeting_id_accepts_plain_basenames():
    assert run_local._is_simple_meeting_id("2026-02-10-regular-session")
    assert run_local._is_simple_meeting_id("foo")


def test_simple_meeting_id_rejects_divergent_ids():
    # Anything whose basename != itself would nest the dir and drift the
    # calibration key away from the stamped provenance.
    for bad in ("", ".", "..", "work/study", "a/b", "/abs/path", "trailing/"):
        assert not run_local._is_simple_meeting_id(bad), bad


def test_enroll_after_review_takes_no_meeting_id_argument():
    """Structural guard: the only meeting id _enroll_after_review can see is the
    directory it is handed. Re-introducing a meeting_id parameter would let a
    caller pass a value that diverges from meeting_dir.name (the regression this
    fix removed)."""
    import inspect

    params = list(inspect.signature(run_local._enroll_after_review).parameters)
    assert "meeting_id" not in params, (
        "_enroll_after_review must derive the id from meeting_dir.name, not accept "
        "it as a parameter"
    )

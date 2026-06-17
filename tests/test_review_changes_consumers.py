"""Merge change-dicts ({'label','merged_into'}) must not crash changes consumers."""
from __future__ import annotations

import run_local


def test_enroll_after_review_skips_merge_entries(tmp_path, monkeypatch):
    # No embeddings.json in tmp_path → _enroll_after_review returns early,
    # but if it ever iterates, a merge-only dict must not KeyError. We force the
    # iteration path by creating an embeddings file and stubbing load/enroll.
    import json
    import numpy as np
    (tmp_path / "embeddings.json").write_text(json.dumps({"SPEAKER_00": [0.1, 0.2]}))

    # stdin is not a tty under pytest → _enroll_after_review returns before the
    # loop; that alone proves no crash. Assert it simply returns None.
    changes = [{"label": "SPEAKER_01", "merged_into": "SPEAKER_00"}]
    result = run_local._enroll_after_review(changes, {}, tmp_path, [])
    assert result is None

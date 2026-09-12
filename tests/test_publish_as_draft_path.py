"""The --publish-as-draft terminal path in run_pipeline.

Regression for the bug the live de-risk run exposed: a non-'pass' gate used to
early-return before the draft publish ran, so low-coverage floor sessions were
never queued as drafts. The draft path now runs right after the gate and
publishes regardless of verdict, storing the verdict for review, and re-raises
on publish failure (so the unattended subprocess exits non-zero).
"""
import pytest

import run_local
from src.models import Meeting, ProcessingMetadata


class _Result:
    segments = 12
    speakers = 3


class _State:
    body_slug = None


def _meeting():
    m = Meeting(
        meeting_id="2026-09-10-house-floor",
        city=None,
        date="2026-09-10",
        meeting_type="House Floor",
        title="House Floor",
        event_kind="floor",
    )
    m.processing_metadata = ProcessingMetadata()
    return m


def test_draft_path_publishes_and_stores_verdict_on_failing_gate(monkeypatch, tmp_path):
    captured = {}

    def fake_publish(meeting, body_slug=None, trigger_deploy=True, status="published"):
        captured["status"] = status
        return _Result()

    monkeypatch.setattr("src.publish.publish_meeting", fake_publish)
    monkeypatch.setattr(run_local, "_attach_thumbnail", lambda *a, **k: None)

    m = _meeting()
    # A FAILING gate — the exact case that used to skip publish.
    run_local._publish_meeting_as_draft(
        m, tmp_path, _State(), {"verdict": "fail", "effective_coverage": 0.0}
    )

    assert captured["status"] == "draft"
    assert m.processing_metadata.gate_verdict == "fail"
    assert m.processing_metadata.gate_coverage == 0.0


def test_draft_path_reraises_on_publish_failure(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("src.publish.publish_meeting", boom)
    monkeypatch.setattr(run_local, "_attach_thumbnail", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="db down"):
        run_local._publish_meeting_as_draft(
            _meeting(), tmp_path, _State(),
            {"verdict": "review", "effective_coverage": 0.3},
        )

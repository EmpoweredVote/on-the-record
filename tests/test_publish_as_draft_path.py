"""The --publish-as-draft terminal path in run_pipeline.

Regression for the bug the live de-risk run exposed: a non-'pass' gate used to
early-return before the draft publish ran, so low-coverage floor sessions were
never queued as drafts. The draft path now runs right after the gate and
publishes regardless of verdict, storing the verdict for review, and re-raises
on publish failure (so the unattended subprocess exits non-zero).

The routing decision (draft vs review-queue vs continue) lives in
`_route_after_gate`; the ordering invariant — 'draft' outranks 'review_queue' —
is pinned here so a future edit cannot silently reintroduce the bug.
"""
import argparse

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


def _args(**kw):
    return argparse.Namespace(**kw)


# --- _route_after_gate: the ordering invariant (the actual root cause) ---

def test_route_draft_outranks_review_queue_on_failing_gate():
    # THE invariant: --publish-as-draft on a FAILING gate routes to 'draft',
    # NOT 'review_queue'. If the two checks were reordered this would return
    # 'review_queue' and the session would be silently dropped — the exact bug
    # the live de-risk run caught.
    r = run_local._route_after_gate(
        _args(publish_as_draft=True, no_publish=False),
        {"verdict": "fail"}, interactive=False, publish_anyway=False,
    )
    assert r == "draft"


def test_route_draft_on_passing_gate_too():
    r = run_local._route_after_gate(
        _args(publish_as_draft=True, no_publish=False),
        {"verdict": "pass"}, interactive=False, publish_anyway=False,
    )
    assert r == "draft"


def test_route_no_publish_overrides_draft():
    r = run_local._route_after_gate(
        _args(publish_as_draft=True, no_publish=True),
        {"verdict": "fail"}, interactive=False, publish_anyway=False,
    )
    assert r != "draft"


def test_route_review_queue_for_nondraft_failing_gate():
    r = run_local._route_after_gate(
        _args(publish_as_draft=False, no_publish=False),
        {"verdict": "fail"}, interactive=False, publish_anyway=False,
    )
    assert r == "review_queue"


def test_route_continue_on_passing_gate_nondraft():
    r = run_local._route_after_gate(
        _args(publish_as_draft=False, no_publish=False),
        {"verdict": "pass"}, interactive=False, publish_anyway=False,
    )
    assert r == "continue"


def test_route_continue_when_interactive_or_publish_anyway():
    for interactive, anyway in [(True, False), (False, True)]:
        r = run_local._route_after_gate(
            _args(publish_as_draft=False, no_publish=False),
            {"verdict": "fail"}, interactive=interactive, publish_anyway=anyway,
        )
        assert r == "continue"


# --- _publish_meeting_as_draft: publishes + stores verdict, re-raises on failure ---

def test_draft_path_publishes_and_stores_verdict_on_failing_gate(monkeypatch, tmp_path):
    captured = {}

    def fake_publish(meeting, body_slug=None, trigger_deploy=True, status="published"):
        captured["status"] = status
        return _Result()

    monkeypatch.setattr("src.publish.publish_meeting", fake_publish)
    monkeypatch.setattr(run_local, "_attach_thumbnail", lambda *a, **k: None)

    m = _meeting()
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

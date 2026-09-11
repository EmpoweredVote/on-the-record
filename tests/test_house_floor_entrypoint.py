import argparse
import json
import sys

import pytest
import run_local
from src.house_cdn import HouseFloorSource
from src.crec_identify import parse_crec_arg

SRC = HouseFloorSource(
    date="2026-07-16",
    manifest_url="https://cdn/east/T/manifest.m3u8",
    title="LEGISLATIVE DAY OF JULY 16, 2026",
    congress="119", session="2",
    start="2026-07-16T09:00:00", end="2026-07-16T12:15:32",
    citation_url="https://live.house.gov/?date=2026-07-16",
    rights="… public domain …",
)


def _args(**kw):
    base = dict(house_floor="2026-07-16", input=None, event_kind=None,
                meeting_type=None, date="", title=None, congressional_record=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_expand_house_floor_populates_args(monkeypatch):
    monkeypatch.setattr(run_local, "resolve_session", lambda d: SRC)
    args = _args()
    run_local._expand_house_floor(args)
    assert args.input == SRC.manifest_url
    assert args.event_kind == "floor"
    assert args.meeting_type == "House Floor"
    assert args.date == "2026-07-16"
    assert args.title == SRC.title
    # nargs=2 [DATE, CHAMBER] so parse_crec_arg (which does `date, chamber = value`) accepts it
    assert args.congressional_record == ["2026-07-16", "house"]
    assert parse_crec_arg(args.congressional_record) == ("2026-07-16", "house")
    assert args._house_source is SRC


def test_expand_house_floor_noop_when_flag_absent(monkeypatch):
    args = _args(house_floor=None)
    run_local._expand_house_floor(args)  # must not call resolve_session or raise
    assert args.input is None and args.event_kind is None


def test_expand_house_floor_aborts_when_unresolved(monkeypatch):
    monkeypatch.setattr(run_local, "resolve_session", lambda d: None)
    with pytest.raises(SystemExit):
        run_local._expand_house_floor(_args())


def test_main_house_floor_passes_source_required_validation(monkeypatch):
    """--house-floor must satisfy the 'a source is required' gate in main() and reach
    run_pipeline — the expansion runs BEFORE that validation, not only inside
    run_pipeline. Regression for the source-required abort."""
    monkeypatch.setattr(run_local, "resolve_session", lambda d: SRC)
    monkeypatch.setattr(run_local, "_resolve_metadata", lambda *a, **k: None)
    reached = {}
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: reached.update(input=args.input, kind=args.event_kind))
    monkeypatch.setattr(run_local.sys, "argv",
                        ["run_local.py", "--house-floor", "2026-07-16",
                         "--diarizer", "oss", "--compute", "modal", "--no-publish"])
    run_local.main()  # must NOT sys.exit on the source-required check
    assert reached["input"] == SRC.manifest_url
    assert reached["kind"] == "floor"


def test_publish_as_draft_with_no_publish_does_not_enable_publish(monkeypatch):
    """--no-publish must always win, even alongside --publish-as-draft. Regression
    for the auto-enable in main() (`if publish_as_draft: args.publish = True`)
    firing unconditionally and defeating --no-publish's documented
    'skip publishing even when resuming' purpose."""
    monkeypatch.setattr(run_local, "resolve_session", lambda d: SRC)
    monkeypatch.setattr(run_local, "_resolve_metadata", lambda *a, **k: None)
    reached = {}
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: reached.update(publish=args.publish))
    monkeypatch.setattr(run_local.sys, "argv",
                        ["run_local.py", "--house-floor", "2026-07-16",
                         "--diarizer", "oss", "--compute", "modal",
                         "--publish-as-draft", "--no-publish"])
    run_local.main()
    assert reached["publish"] is False


def test_publish_as_draft_alone_enables_publish(monkeypatch):
    """Positive case: --publish-as-draft with no --no-publish still auto-enables
    args.publish, so a bare --publish-as-draft run doesn't silently skip
    publishing."""
    monkeypatch.setattr(run_local, "resolve_session", lambda d: SRC)
    monkeypatch.setattr(run_local, "_resolve_metadata", lambda *a, **k: None)
    reached = {}
    monkeypatch.setattr(run_local, "run_pipeline",
                        lambda args: reached.update(publish=args.publish))
    monkeypatch.setattr(run_local.sys, "argv",
                        ["run_local.py", "--house-floor", "2026-07-16",
                         "--diarizer", "oss", "--compute", "modal",
                         "--publish-as-draft"])
    run_local.main()
    assert reached["publish"] is True


def _make_resumable_meeting(meeting_dir, mid):
    """Fixture a fully-checkpointed meeting (all 7 stages complete) so
    --resume drives run_pipeline straight to the publish block without
    doing any real ingestion/diarization/transcription/identification work.
    """
    meeting_dir.mkdir(parents=True)
    (meeting_dir / "pipeline_state.json").write_text(
        json.dumps({"completed_stage": 7}), encoding="utf-8")
    (meeting_dir / "diarization.json").write_text("[]", encoding="utf-8")
    (meeting_dir / "transcript_raw.json").write_text("[]", encoding="utf-8")
    (meeting_dir / "transcript_named.json").write_text(
        json.dumps({
            "meeting_id": mid,
            "audio_source": "https://example.invalid/floor.mp4",
            "city": None,
            "date": "2026-07-16",
            "meeting_type": "House Floor",
            "event_kind": "other",
            "segments": [],
            "speakers": {},
        }),
        encoding="utf-8",
    )


def _stub_pipeline_internals(monkeypatch, tmp_path):
    """Common stubs so a --resume run reaches the publish block cheaply:
    no real HF token lookup, no real audio-duration probe on a nonexistent
    audio.wav, and a forced-pass confidence gate (both call sites — Stage 4's
    gate and the draft-publish block's own re-evaluation — read the same
    monkeypatched src.quality.evaluate_meeting).
    """
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr(run_local, "get_hf_token", lambda: "hf_dummy_token")
    monkeypatch.setattr("src.audio_utils.get_audio_duration", lambda p: 0.0)
    monkeypatch.setattr(
        "src.quality.evaluate_meeting",
        lambda meeting, **kw: {
            "verdict": "pass",
            "reason": "stub",
            "trusted_coverage": 1.0,
            "effective_coverage": 1.0,
        },
    )
    monkeypatch.setattr(run_local, "_attach_thumbnail", lambda meeting, meeting_dir: None)


def test_publish_as_draft_failure_propagates(monkeypatch, tmp_path):
    """Regression for the draft-publish swallow bug: a --publish-as-draft run
    whose publish_meeting call raises must propagate the failure (so an
    unattended cron subprocess exits non-zero and floor_dispatch counts the
    session as failed), not just print a warning and return normally.
    """
    mid = "2026-07-16-house-floor"
    meeting_dir = tmp_path / mid
    _make_resumable_meeting(meeting_dir, mid)
    _stub_pipeline_internals(monkeypatch, tmp_path)

    def _raise_publish(meeting, body_slug=None, trigger_deploy=True, status="published"):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("src.publish.publish_meeting", _raise_publish)

    monkeypatch.setattr(
        sys, "argv",
        ["run_local.py", "--resume", mid, "--publish-as-draft", "--no-review",
         "--compute", "modal", "--diarizer", "oss"],
    )

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        run_local.main()


def test_publish_as_draft_success_still_prints_and_completes(monkeypatch, tmp_path):
    """Sanity check alongside the failure regression: a successful draft
    publish must NOT raise and must still reach pipeline completion."""
    from src.publish import PublishResult

    mid = "2026-07-16-house-floor-ok"
    meeting_dir = tmp_path / mid
    _make_resumable_meeting(meeting_dir, mid)
    _stub_pipeline_internals(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "src.publish.publish_meeting",
        lambda meeting, body_slug=None, trigger_deploy=True, status="published":
            PublishResult(meeting_id=meeting.meeting_id, segments=0, speakers=0),
    )

    monkeypatch.setattr(
        sys, "argv",
        ["run_local.py", "--resume", mid, "--publish-as-draft", "--no-review",
         "--compute", "modal", "--diarizer", "oss"],
    )

    run_local.main()  # must not raise

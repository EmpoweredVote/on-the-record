import argparse
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

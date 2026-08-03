"""run_local must never write transcript_named.json in place.

A process killed mid-dump used to leave the file truncated mid-JSON (it happened:
a 2026-04-20 meeting sat unparseable from Jun 30 until it was rebuilt by hand),
and every downstream reader — GUI workspace/review, duplicate-speaker scans,
publish, the backfills — fails on it. All writes go through src.atomic_io.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pytest
import yaml

import run_local
from src.enroll import ProfileDB
from src.models import Meeting, SpeakerMapping

_UUID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def _write_meeting(meeting_dir: Path, name: str = "Steve Hilton") -> Path:
    meeting_dir.mkdir(parents=True, exist_ok=True)
    meeting = Meeting(meeting_id=meeting_dir.name, city="X", date="2026-04-01",
                      speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name=name)})
    path = meeting_dir / "transcript_named.json"
    path.write_text(json.dumps(meeting.to_dict(), indent=2), encoding="utf-8")
    return path


def _crash_before_rename(monkeypatch):
    """Simulate the process dying after the temp file is written but before the
    rename — the window that used to truncate the real file."""
    def boom(src, dst):
        raise OSError("simulated kill between write and rename")
    monkeypatch.setattr("src.atomic_io.os.replace", boom)


def _stub_link_target(monkeypatch):
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [{"politician_id": _UUID, "politician_slug": None,
                                          "full_name": "Steve Hilton"}])
    monkeypatch.setattr("src.enroll.load_profiles", lambda: ProfileDB(profiles={}))
    monkeypatch.setattr("src.enroll.save_profiles", lambda db: None)
    monkeypatch.setattr(run_local, "_publish_meeting_standalone",
                        lambda mid, anyway=False: None)
    monkeypatch.setattr(run_local, "_trigger_render_deploy", lambda: None)


def test_bulk_relink_apply_crash_mid_write_leaves_transcript_intact(tmp_path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    named = _write_meeting(meetings_root / "m1")
    before = named.read_text(encoding="utf-8")
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    _stub_link_target(monkeypatch)

    review_file = tmp_path / "review.yaml"
    review_file.write_text(yaml.safe_dump(
        {"speakers": [{"name": "Steve Hilton", "decision": "link", "politician_id": _UUID}]}))
    args = argparse.Namespace(bulk_relink_apply=str(review_file), dry_run=False,
                              publish_anyway=True, deploy=False)

    _crash_before_rename(monkeypatch)
    with pytest.raises(OSError):
        run_local._bulk_relink_apply(args)

    assert named.read_text(encoding="utf-8") == before
    assert Meeting.from_dict(json.loads(named.read_text(encoding="utf-8"))).meeting_id == "m1"
    assert not (named.parent / "transcript_named.json.tmp").exists()


def test_relink_person_crash_mid_write_leaves_transcript_intact(tmp_path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    named = _write_meeting(meetings_root / "m1")
    before = named.read_text(encoding="utf-8")
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    _stub_link_target(monkeypatch)

    args = argparse.Namespace(relink_person="Steve Hilton", to_name=None, to_id=None,
                              meeting=None, dry_run=False, publish_anyway=True, deploy=False)

    _crash_before_rename(monkeypatch)
    with pytest.raises(OSError):
        run_local._relink_person(args)

    assert named.read_text(encoding="utf-8") == before
    assert Meeting.from_dict(json.loads(named.read_text(encoding="utf-8"))).meeting_id == "m1"
    assert not (named.parent / "transcript_named.json.tmp").exists()


def test_no_plain_open_writes_of_the_named_transcript_remain():
    """Guard for the write sites a unit test can't easily drive (the identify and
    summary stages inside process_meeting, the terminal review save): no
    `open(<...named transcript...>, "w")` may survive anywhere in run_local."""
    tree = ast.parse(Path(run_local.__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open"):
            continue
        mode = node.args[1].value if (len(node.args) > 1
                                      and isinstance(node.args[1], ast.Constant)) else ""
        target = ast.unparse(node.args[0]) if node.args else ""
        if "w" in str(mode) and ("transcript_named" in target or "named_path" in target
                                 or "named_transcript" in target):
            offenders.append(f"run_local.py:{node.lineno}: open({target}, {mode!r})")
    assert offenders == [], "non-atomic transcript writes:\n" + "\n".join(offenders)

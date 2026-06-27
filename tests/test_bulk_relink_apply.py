from __future__ import annotations

import json

import yaml

import run_local
from src.enroll import ProfileDB
from src.models import Meeting, SpeakerMapping

_UUID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def _write_meeting(meeting_dir, meeting):
    meeting_dir.mkdir(parents=True, exist_ok=True)
    with open(meeting_dir / "transcript_named.json", "w", encoding="utf-8") as f:
        json.dump(meeting.to_dict(), f, indent=2)


def _meeting(mid, name):
    return Meeting(meeting_id=mid, city="X", date="2026-04-01",
                   speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name=name)})


def _args(review_file, **over):
    import argparse
    ns = argparse.Namespace(
        bulk_relink_apply=str(review_file), dry_run=False,
        publish_anyway=False, deploy=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_apply_links_approved_and_skips_review(tmp_path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    _write_meeting(meetings_root / "m1", _meeting("m1", "Steve Hilton"))
    _write_meeting(meetings_root / "m2", _meeting("m2", "Katie Porter"))

    # Point the pipeline at the temp meetings dir (same module object as src.config).
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)

    # Stub the essentials display lookup used by resolve_link_target.
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [{"politician_id": _UUID, "politician_slug": None,
                                          "full_name": "Steve Hilton"}])

    # No-op the profile DB boundaries. The handler imports these locally from
    # src.enroll, so patch them THERE (not on run_local) or save_profiles would
    # write the real production profile DB.
    monkeypatch.setattr("src.enroll.load_profiles", lambda: ProfileDB(profiles={}))
    monkeypatch.setattr("src.enroll.save_profiles", lambda db: None)

    # Publish + deploy are module-level names in run_local, called bare.
    published = []
    monkeypatch.setattr(run_local, "_publish_meeting_standalone",
                        lambda mid, anyway=False: published.append(mid))
    monkeypatch.setattr(run_local, "_trigger_render_deploy", lambda: None)

    review = {"speakers": [
        {"name": "Steve Hilton", "decision": "link", "politician_id": _UUID},
        {"name": "Katie Porter", "decision": "review", "politician_id": None},
    ]}
    review_file = tmp_path / "review.yaml"
    review_file.write_text(yaml.safe_dump(review))

    run_local._bulk_relink_apply(_args(review_file, publish_anyway=True))

    # m1 transcript now linked, m2 untouched, only m1 published.
    m1 = json.loads((meetings_root / "m1" / "transcript_named.json").read_text())
    assert m1["speakers"]["S0"]["politician_id"] == _UUID
    m2 = json.loads((meetings_root / "m2" / "transcript_named.json").read_text())
    assert m2["speakers"]["S0"].get("politician_id") is None
    assert published == ["m1"]


def test_apply_dry_run_writes_nothing(tmp_path, monkeypatch):
    meetings_root = tmp_path / "meetings"
    _write_meeting(meetings_root / "m1", _meeting("m1", "Steve Hilton"))
    monkeypatch.setattr(run_local.config, "MEETINGS_DIR", meetings_root)
    monkeypatch.setattr("src.relink.search_politicians",
                        lambda q, **kw: [{"politician_id": _UUID, "politician_slug": None,
                                          "full_name": "Steve Hilton"}])
    published = []
    monkeypatch.setattr(run_local, "_publish_meeting_standalone",
                        lambda mid, anyway=False: published.append(mid))

    review_file = tmp_path / "review.yaml"
    review_file.write_text(yaml.safe_dump(
        {"speakers": [{"name": "Steve Hilton", "decision": "link", "politician_id": _UUID}]}))

    run_local._bulk_relink_apply(_args(review_file, dry_run=True))

    m1 = json.loads((meetings_root / "m1" / "transcript_named.json").read_text())
    assert m1["speakers"]["S0"].get("politician_id") is None  # unchanged
    assert published == []  # nothing published

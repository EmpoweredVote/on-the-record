from __future__ import annotations

import json


def test_list_cached_rosters_reads_dir(tmp_config_dir):
    from gui.rosters import list_cached_rosters
    rosters = tmp_config_dir / "rosters"
    rosters.mkdir(exist_ok=True)
    (rosters / "bloomington-common-council.json").write_text(json.dumps(
        {"body_key": "Bloomington Common Council", "politicians": [{}, {}, {}]}))
    out = list_cached_rosters()
    assert ("bloomington-common-council", "Bloomington Common Council (3 members)") in out


def test_list_cached_rosters_empty_when_no_dir(tmp_config_dir):
    from gui.rosters import list_cached_rosters
    assert list_cached_rosters() == []


def test_list_cached_rosters_tolerates_bad_json(tmp_config_dir):
    from gui.rosters import list_cached_rosters
    rosters = tmp_config_dir / "rosters"; rosters.mkdir(exist_ok=True)
    (rosters / "broken.json").write_text("{ not json")
    # falls back to the slug as the label; doesn't raise
    assert ("broken", "broken") in list_cached_rosters()

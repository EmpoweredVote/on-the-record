"""Offline schema checks for the hub-recall ground-truth fixture."""
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures/hub_recall_ground_truth.json"


def test_ground_truth_fixture_shape():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    races = data["races"]
    assert len(races) == 8
    seen_ids = set()
    for race in races:
        for key in ("race", "state", "race_label", "year", "candidates", "sources"):
            assert race.get(key), f"{race.get('race')} missing {key}"
        assert race["year"] in race["race_label"], f"{race['race']} label lacks year"
        assert len(race["state"]) == 2
        assert race["sources"], f"{race['race']} has no sources"
        for s in race["sources"]:
            assert s["id"] and s["url"], f"{race['race']} source missing id/url"
            assert s["id"] not in seen_ids, f"duplicate id {s['id']}"
            seen_ids.add(s["id"])
            assert isinstance(s.get("accept_urls", []), list)
            assert s["url"].startswith("http")

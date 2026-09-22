"""Offline validation of the committed hub-registry snapshot."""
import json
from pathlib import Path

from src.discovery.hubs import Hub, hubs_for_race

SNAP = Path(__file__).resolve().parent / "fixtures/hub_recall_hubs_snapshot.json"
_FIELDS = {"id", "name", "scope", "state", "kind", "poll_method", "domain",
           "query_template", "tos_bucket", "active", "added_via", "notes"}


def _load():
    rows = json.loads(SNAP.read_text(encoding="utf-8"))["hubs"]
    return [Hub(**{k: v for k, v in r.items() if k in _FIELDS}) for r in rows]


def test_snapshot_loads_and_has_ballotpedia_scoped_hub():
    hubs = _load()
    assert len(hubs) >= 10
    bp = [h for h in hubs if (h.domain or "").endswith("ballotpedia.org")]
    assert bp, "no ballotpedia hub in snapshot"
    assert any(h.poll_method == "scoped_search" for h in bp)


def test_snapshot_resolves_for_a_state_race():
    hubs = _load()
    applicable = hubs_for_race(hubs, state="CA")
    assert applicable, "no applicable hubs for CA"
    assert all(h.poll_method == "scoped_search" for h in applicable)

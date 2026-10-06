from __future__ import annotations

import json

from src.name_suggestion_log import LOG_NAME, log_event, log_overrides
from src.name_suggestion_view import to_view


def _rec(label, prefill):
    return {"label": label, "tier": "strong", "role": "presenter", "partial": False, "conflict": None,
            "spoken_name": prefill, "prefill_name": prefill, "evidence": [],
            "lookup": {"name": prefill, "source": "web", "verified": True, "url": "https://x.org/a"}}


def _lines(tmp_path):
    p = tmp_path / LOG_NAME
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_log_event_fields(tmp_path):
    v = to_view(_rec("W", "Rachael Sample"))
    log_event(tmp_path, meeting_id="m1", view=v, action="accepted", final_name="Rachael Sample")
    [l] = _lines(tmp_path)
    assert {k: l[k] for k in ("meeting_id", "label", "suggested_name", "tier", "source", "verified", "action",
                              "final_name")} == {"meeting_id": "m1", "label": "W", "suggested_name": "Rachael Sample",
                                                  "tier": "strong", "source": "web", "verified": True,
                                                  "action": "accepted", "final_name": "Rachael Sample"}
    assert "ts" in l


def test_log_event_never_raises(tmp_path):
    v = to_view(_rec("W", "A B"))
    log_event(tmp_path / "missing" / "deeper", meeting_id="m1", view=v, action="accepted", final_name="A B")


def test_log_overrides_dedup_and_ignores_unnamed(tmp_path):
    (tmp_path / "name_suggestions.json").write_text(json.dumps({"warnings": [], "suggestions": [
        _rec("W", "Rachael Sample"), _rec("V", "Paul Webster"), _rec("U", "Ann Lee")]}))
    finals = {"W": "Rachel Sample", "V": "paul  webster", "U": None}
    assert log_overrides(tmp_path, "m1", finals) == 1
    assert log_overrides(tmp_path, "m1", finals) == 0          # de-duplicated
    [l] = _lines(tmp_path)
    assert (l["label"], l["action"], l["final_name"]) == ("W", "overridden", "Rachel Sample")

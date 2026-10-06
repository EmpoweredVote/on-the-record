"""Terminal review: a verified name suggestion is shown and [Y] accepts it first."""
from __future__ import annotations

import json

import run_local
from src import review as review_mod
from src.models import Segment, SpeakerMapping


def _seg(label, start, end):
    return Segment(segment_id=0, start_time=start, end_time=end, speaker_label=label, text="hi")


class _DB:
    profiles: dict = {}


def _write(tmp_path, label="S0", name="Rachael Sample"):
    d = tmp_path / "m1"
    d.mkdir()
    rec = {"label": label, "tier": "strong", "role": "presenter", "partial": False, "conflict": None,
           "spoken_name": name, "prefill_name": name, "evidence": [],
           "lookup": {"name": name, "source": "web", "verified": True, "url": "https://www.example.org/x"}}
    (d / "name_suggestions.json").write_text(json.dumps({"suggestions": [rec]}), encoding="utf-8")
    return d


def _drive(monkeypatch, tmp_path, inputs, segments, mappings):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("src.config.MEETINGS_DIR", tmp_path)
    monkeypatch.setattr("src.essentials_client.search_politicians", lambda *a, **k: [])
    script = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(script))
    return run_local._interactive_speaker_review(
        segments, mappings, {}, _DB(), None, None, show_text=False, meeting_id="m1",
    )


def test_y_accepts_suggestion_and_logs(monkeypatch, tmp_path, capsys):
    d = _write(tmp_path)
    segs = [_seg("S0", 0, 50)]
    maps = {"S0": SpeakerMapping(speaker_label="S0")}
    changes = _drive(monkeypatch, tmp_path, ["y"], segs, maps)
    assert maps["S0"].speaker_name == "Rachael Sample"
    assert maps["S0"].local_slug == "rachael-sample"
    assert changes and changes[0]["new_name"] == "Rachael Sample"
    assert "Suggested: Rachael Sample (verified, web" in capsys.readouterr().out
    assert '"accepted"' in (d / "name_suggestion_log.jsonl").read_text(encoding="utf-8")


def test_y_falls_back_to_voice_match_when_suggestion_not_acceptable(monkeypatch, tmp_path):
    d = tmp_path / "m1"
    d.mkdir()
    rec = {"label": "S0", "prefill_name": None, "spoken_name": "Zed Zed", "evidence": [],
           "lookup": {"name": "Zed Zed", "source": "web", "reason": "different name returned"}}
    (d / "name_suggestions.json").write_text(json.dumps({"suggestions": [rec]}), encoding="utf-8")
    segs = [_seg("S0", 0, 50)]
    maps = {"S0": SpeakerMapping(speaker_label="S0")}

    real = review_mod.build_review_state

    def fake(*a, **k):
        vs = real(*a, **k)
        for v in vs:
            v.soft_hints = [("Voice Person", 0.9, "")]
        return vs

    monkeypatch.setattr(review_mod, "build_review_state", fake)
    monkeypatch.setattr(run_local, "_prompt_link_politician", lambda *a, **k: None)
    monkeypatch.setattr(run_local, "_prompt_create_local_person", lambda *a, **k: None)
    changes = _drive(monkeypatch, tmp_path, ["y"], segs, maps)
    assert changes and changes[0]["new_name"] == "Voice Person"
    assert not (d / "name_suggestion_log.jsonl").exists()


def test_y_rolls_back_when_slug_is_taken(monkeypatch, tmp_path, capsys):
    _write(tmp_path)
    segs = [_seg("S0", 0, 50), _seg("S1", 60, 90)]
    maps = {"S0": SpeakerMapping(speaker_label="S0"),
            "S1": SpeakerMapping(speaker_label="S1", speaker_name="Other", local_slug="rachael-sample",
                                 local_role="presenter")}
    changes = _drive(monkeypatch, tmp_path, ["y", "", ""], segs, maps)
    assert "nothing changed" in capsys.readouterr().out
    assert maps["S0"].speaker_name in (None, "")
    assert maps["S0"].local_slug is None
    assert all(s.speaker_name != "Rachael Sample" for s in segs)
    assert changes == []

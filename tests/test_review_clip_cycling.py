"""The interactive review loop lets the operator view as many clips as they want.

[V] cycles through clip candidates, [V<n>] jumps to a specific clip, [R] replays
the current one — none of these are ever treated as a rename.
"""
from __future__ import annotations

import run_local
from src.models import Segment, SpeakerMapping


def _seg(label, start, end, text=""):
    return Segment(segment_id=0, start_time=start, end_time=end, speaker_label=label, text=text)


class _FakeProfileDB:
    profiles: dict = {}


def _drive(monkeypatch, inputs, segments, mappings):
    """Run _interactive_speaker_review with scripted input; return (changes, played starts)."""
    played = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        run_local, "play_speaker_clip",
        lambda video, audio, start, duration=40.0, title="": played.append(start),
    )
    script = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(script))
    changes = run_local._interactive_speaker_review(
        segments, mappings, {}, _FakeProfileDB(),
        "/m/source.mp4", "/m/audio.wav", show_text=False,
    )
    return changes, played


def test_view_cycles_jumps_and_replays_multiple_clips(monkeypatch):
    # Three turns (50s, 20s, 10s) → candidates [0.0, 100.0, 200.0]
    segments = [_seg("S0", 0, 50), _seg("S0", 100, 120), _seg("S0", 200, 210)]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    changes, played = _drive(
        monkeypatch,
        ["v", "v", "v3", "r", ""],  # next, next, jump to 3, replay 3, skip
        segments, mappings,
    )
    assert played == [0.0, 100.0, 200.0, 200.0]
    assert changes == []  # clip controls never rename


def test_view_wraps_around_past_last_clip(monkeypatch):
    segments = [_seg("S0", 0, 50), _seg("S0", 100, 120)]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    changes, played = _drive(monkeypatch, ["v", "v", "v", ""], segments, mappings)
    assert played == [0.0, 100.0, 0.0]
    assert changes == []


def test_replay_before_any_view_plays_first_clip(monkeypatch):
    segments = [_seg("S0", 0, 50), _seg("S0", 100, 120)]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    changes, played = _drive(monkeypatch, ["r", ""], segments, mappings)
    assert played == [0.0]
    assert changes == []


def test_out_of_range_jump_reprompts(monkeypatch, capsys):
    segments = [_seg("S0", 0, 50)]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    changes, played = _drive(monkeypatch, ["v9", ""], segments, mappings)
    assert played == []
    assert changes == []
    assert "has 1 clip" in capsys.readouterr().out


def test_typing_a_name_still_renames_after_viewing(monkeypatch):
    segments = [_seg("S0", 0, 50), _seg("S0", 100, 120)]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    changes, played = _drive(monkeypatch, ["v", "Jane Doe"], segments, mappings)
    assert played == [0.0]
    assert changes == [{"label": "S0", "old_name": None, "new_name": "Jane Doe"}]
    assert mappings["S0"].speaker_name == "Jane Doe"

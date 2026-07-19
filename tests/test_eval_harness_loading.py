"""Tests for the eval CLI's pure-ish loading helpers (no API calls).

scripts/ has no package __init__, so load the module by file path.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

_spec = importlib.util.spec_from_file_location(
    "eval_speaker_id",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "eval_speaker_id.py")
eval_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_mod)


def _write_meeting(tmp_path, name, data):
    d = tmp_path / name
    d.mkdir()
    (d / "transcript_named.json").write_text(json.dumps(data))
    return d


def _write_raw(tmp_path, name, raw):
    d = tmp_path / name
    d.mkdir()
    (d / "transcript_named.json").write_text(raw)
    return d


def test_gold_labels_only_human_review_deduped():
    meeting = {
        "segments": [
            {"speaker_label": "SPEAKER_00", "speaker_name": "Jane Smith",
             "id_method": "human_review"},
            {"speaker_label": "SPEAKER_00", "speaker_name": "Jane WRONG",
             "id_method": "human_review"},  # dup label: first wins
            {"speaker_label": "SPEAKER_01", "speaker_name": "Auto Guess",
             "id_method": "llm"},            # not human_review: excluded
            {"speaker_label": "SPEAKER_02", "speaker_name": "Bob Jones",
             "id_method": "human_review"},
        ]
    }
    gold = eval_mod._gold_labels(meeting)
    assert gold == {"SPEAKER_00": "Jane Smith", "SPEAKER_02": "Bob Jones"}


def test_load_meetings_filters_and_survives_malformed(tmp_path):
    # interview kind + gold label -> kept
    _write_meeting(tmp_path, "keep-podcast", {
        "event_kind": "podcast",
        "segments": [{"speaker_label": "SPEAKER_00", "speaker_name": "Guest",
                      "id_method": "human_review"}],
    })
    # non-interview kind -> dropped
    _write_meeting(tmp_path, "drop-council", {
        "event_kind": "council",
        "segments": [{"speaker_label": "SPEAKER_00", "speaker_name": "Mayor",
                      "id_method": "human_review"}],
    })
    # interview kind but no gold labels -> dropped
    _write_meeting(tmp_path, "drop-nolabels", {
        "event_kind": "news_clip",
        "segments": [{"speaker_label": "SPEAKER_00", "speaker_name": "X",
                      "id_method": "llm"}],
    })
    # malformed JSON -> skipped without crashing
    _write_raw(tmp_path, "drop-broken", "{not valid json")

    loaded = eval_mod._load_meetings(tmp_path)
    names = [name for name, _ in loaded]
    assert names == ["keep-podcast"]

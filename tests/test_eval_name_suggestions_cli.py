from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "name_suggestions"
SCRIPT = Path(__file__).parent.parent / "scripts" / "eval_name_suggestions.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_name_suggestions", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_scores_meetings_in_a_directory(tmp_path):
    for f in FIX.glob("*.json"):
        d = tmp_path / f.stem
        d.mkdir()
        shutil.copy(f, d / "transcript_named.json")
    rows = _load().run(tmp_path, kinds=None)
    assert rows and {"label", "gold", "outcome", "tier", "event_kind"} <= set(rows[0])
    assert _load().run(tmp_path, kinds=["podcast"]) == []


_VTT = """WEBVTT
Kind: captions
Language: en

NOTE made by youtube

00:00:01.000 --> 00:00:03.000 align:start position:0%
Thanks<00:00:01.240><c> so</c><00:00:01.600><c> much,</c><00:00:02.000><c> Angelica</c><00:00:02.480><c> Salas</c>
"""


def test_captions_strips_cue_tags_and_headers(tmp_path):
    (tmp_path / "captions.en-US.vtt").write_text(_VTT, encoding="utf-8")
    text = _load()._captions(tmp_path)
    assert "Angelica Salas" in " ".join(text.split())
    for junk in ("<", "WEBVTT", "Kind:", "Language:", "NOTE", "-->"):
        assert junk not in text


def test_captions_prefers_source_then_none(tmp_path):
    assert _load()._captions(tmp_path) is None
    (tmp_path / "captions.vtt").write_text(_VTT, encoding="utf-8")
    (tmp_path / "source_captions.vtt").write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nsource one\n")
    assert "source one" in _load()._captions(tmp_path)


def test_run_skips_bad_files(tmp_path):
    a = tmp_path / "a"; a.mkdir()
    (a / "transcript_named.json").write_text("[1, 2]")
    b = tmp_path / "b"; b.mkdir()
    (b / "transcript_named.json").write_bytes(b"\xff\xfe\x00bad")
    assert _load().run(tmp_path, kinds=None) == []

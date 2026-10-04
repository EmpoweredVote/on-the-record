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

import importlib.util
from pathlib import Path

import pytest

# Load the script module by path (it lives under .claude/skills, not on sys.path).
_SPEC_PATH = Path(__file__).resolve().parents[1] / ".claude/skills/publish-quotes/scripts/insert_quotes.py"
_spec = importlib.util.spec_from_file_location("insert_quotes", _SPEC_PATH)
insert_quotes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(insert_quotes)
build_insert_rows = insert_quotes.build_insert_rows

PID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def _batch(**over):
    b = {
        "topic_key": "abortion",
        "source_url": "https://www.youtube.com/watch?v=VIZ1h4OaImU",
        "quotes": [{"text": "A quote.", "editor_note": "Clearest statement of the stance."}],
    }
    b.update(over)
    return b


def test_row_shape_and_verbatim_deidentified_default():
    rows = build_insert_rows(_batch(), PID)
    assert len(rows) == 1
    pid, topic_key, quote_text, deid, source_name, source_url, editor_note = rows[0]
    assert pid == PID
    assert topic_key == "abortion"
    assert quote_text == "A quote."
    assert deid == "A quote."  # verbatim default
    assert source_name == "www.youtube.com"
    assert source_url == "https://www.youtube.com/watch?v=VIZ1h4OaImU"
    assert editor_note == "Clearest statement of the stance."


def test_empty_editor_note_is_rejected():
    with pytest.raises(ValueError, match="editor_note"):
        build_insert_rows(_batch(quotes=[{"text": "x", "editor_note": "   "}]), PID)


def test_missing_editor_note_is_rejected():
    with pytest.raises(ValueError, match="editor_note"):
        build_insert_rows(_batch(quotes=[{"text": "x"}]), PID)


def test_per_quote_topic_and_source_override_batch_defaults():
    rows = build_insert_rows(
        _batch(quotes=[{
            "text": "y",
            "editor_note": "note",
            "topic_key": "housing",
            "source_url": "https://www.youtube.com/watch?v=OTHER",
            "timestamp_seconds": 90,
        }]),
        PID,
    )
    _, topic_key, _, _, source_name, source_url, _ = rows[0]
    assert topic_key == "housing"
    assert source_url == "https://www.youtube.com/watch?v=OTHER&t=90s"
    assert source_name == "www.youtube.com"


def test_missing_topic_key_anywhere_is_rejected():
    b = {"source_url": "https://x.test", "quotes": [{"text": "z", "editor_note": "n"}]}
    with pytest.raises(ValueError, match="topic_key"):
        build_insert_rows(b, PID)

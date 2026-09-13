from pathlib import Path

import gui.floor_import as fi


def test_list_floor_sessions_marks_local(monkeypatch, tmp_path):
    rows = [
        {"slug": "2026-09-03-house-floor", "date": "2026-09-03",
         "gate_verdict": "review", "gate_coverage": 0.63},
        {"slug": "2026-09-04-house-floor", "date": "2026-09-04",
         "gate_verdict": "review", "gate_coverage": 0.0},
    ]
    monkeypatch.setattr(fi, "_query_draft_floor_rows", lambda: rows)
    (tmp_path / "2026-09-03-house-floor").mkdir()  # already pulled

    out = fi.list_floor_sessions(tmp_path)

    assert [s.slug for s in out] == ["2026-09-03-house-floor", "2026-09-04-house-floor"]
    assert out[0].is_local is True and out[1].is_local is False
    assert out[0].gate_coverage == 0.63


def test_list_floor_sessions_empty_without_db(monkeypatch, tmp_path):
    monkeypatch.setattr(fi, "_query_draft_floor_rows", lambda: [])
    assert fi.list_floor_sessions(tmp_path) == []

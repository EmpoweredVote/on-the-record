import json
from pathlib import Path

import gui.floor_import as fi


def _fake_gh(tmp_download_root):
    """A subprocess.run stand-in for the gh calls import_session makes."""
    def run(cmd, *a, **k):
        import types
        if cmd[:2] == ["gh", "run"] and "list" in cmd:
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"databaseId": 111}, {"databaseId": 222}]))
        if cmd[:2] == ["gh", "run"] and "download" in cmd:
            # -D <dir> is where gh would place the artifact contents
            out = tmp_download_root / cmd[cmd.index("-D") + 1]
            slug = "2026-09-03-house-floor"
            # Only run 222's artifact contains the slug.
            if "222" in cmd:
                d = out / slug
                d.mkdir(parents=True, exist_ok=True)
                (d / "pipeline_state.json").write_text(json.dumps(
                    {"completed_stage": 4, "event_kind": "floor"}))
                (d / "transcript_named.json").write_text(json.dumps({"title": "Floor"}))
                (d / "embeddings.json").write_text("{}")
            else:
                out.mkdir(parents=True, exist_ok=True)
            return types.SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected gh call: {cmd}")
    return run


def test_import_session_extracts_slug_folder(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    monkeypatch.setattr(fi, "_download_root", lambda: tmp_path / "dl")
    dest = fi.import_session("2026-09-03-house-floor", meetings,
                             runner=_fake_gh(tmp_path / "dl"))
    assert dest == meetings / "2026-09-03-house-floor"
    assert (dest / "pipeline_state.json").exists()
    assert (dest / "embeddings.json").exists()


def test_import_session_refuses_to_clobber_local(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"; (meetings / "2026-09-03-house-floor").mkdir(parents=True)
    monkeypatch.setattr(fi, "_download_root", lambda: tmp_path / "dl")
    import pytest
    with pytest.raises(fi.FloorAlreadyLocalError):
        fi.import_session("2026-09-03-house-floor", meetings, runner=_fake_gh(tmp_path / "dl"))


def test_import_session_not_found_raises(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    monkeypatch.setattr(fi, "_download_root", lambda: tmp_path / "dl")
    import pytest
    with pytest.raises(fi.FloorImportError):
        fi.import_session("2099-01-01-house-floor", meetings, runner=_fake_gh(tmp_path / "dl"))


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

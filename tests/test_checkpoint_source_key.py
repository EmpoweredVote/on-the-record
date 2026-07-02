from __future__ import annotations

from src.checkpoint import PipelineState


def test_source_key_persists_and_reloads(tmp_path):
    st = PipelineState(tmp_path)
    assert st.source_key is None            # default
    st.source_key = "youtube:abc123"
    st.save()

    st2 = PipelineState(tmp_path)            # fresh load from disk
    assert st2.source_key == "youtube:abc123"


def test_source_key_absent_in_old_state_defaults_none(tmp_path):
    import json
    (tmp_path / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    assert PipelineState(tmp_path).source_key is None

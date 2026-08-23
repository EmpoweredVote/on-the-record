"""Tests for scripts/generate_summary_ab.py's script-level (non-network)
logic: markdown assembly blindness, pair-title derivation, and the
highlights discard.

scripts/ has no package __init__, so load the module by file path (matches
tests/test_eval_harness_loading.py's pattern for scripts/eval_speaker_id.py).
"""
from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import MagicMock

_spec = importlib.util.spec_from_file_location(
    "generate_summary_ab",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "generate_summary_ab.py")
ab_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab_mod)

from src.models import Meeting, Segment  # noqa: E402
from src.summary_ab_pairing import assign_visible_ids, build_pair, pair_rng  # noqa: E402


SECRET_MODEL_ID = "totally-fake-secret-model-xyz-123"


def _fake_comparisons(model_id: str = SECRET_MODEL_ID) -> list:
    """Build comparison records the same shape main() assembles, using the
    real pairing functions but with a made-up, unmistakably-identifiable
    model id — no client/network involved."""
    meeting_id = "m1"
    rng = pair_rng(meeting_id, model_id)
    pairs = [
        build_pair(meeting_id, f"{model_id}::executive_summary", model_id,
                   "ALPHA EXEC TEXT", "BETA EXEC TEXT", rng),
        build_pair(meeting_id, f"{model_id}::section_0:Intro", model_id,
                   "ALPHA SECTION TEXT", "BETA SECTION TEXT", rng),
    ]
    pairs = assign_visible_ids(pairs, start=1)
    return [{"meeting_id": meeting_id, "comparison_index": 1, "pairs": pairs}]


# --- item 1: blindness of the fully assembled ab_pairs.md ------------------

def test_assembled_markdown_never_contains_model_id():
    md = ab_mod.assemble_markdown(_fake_comparisons())
    assert SECRET_MODEL_ID not in md


def test_assembled_markdown_never_contains_candidate_or_reference_role_words():
    md = ab_mod.assemble_markdown(_fake_comparisons())
    assert "candidate" not in md.lower()
    assert "reference" not in md.lower()


def test_assembled_markdown_uses_anonymous_comparison_header():
    md = ab_mod.assemble_markdown(_fake_comparisons())
    assert "## m1 — Comparison 1" in md


def test_assembled_markdown_includes_visible_pair_ids():
    md = ab_mod.assemble_markdown(_fake_comparisons())
    assert "pair-1" in md
    assert "pair-2" in md


def test_assembled_markdown_includes_both_option_texts():
    md = ab_mod.assemble_markdown(_fake_comparisons())
    assert "ALPHA EXEC TEXT" in md
    assert "BETA EXEC TEXT" in md
    assert "ALPHA SECTION TEXT" in md
    assert "BETA SECTION TEXT" in md


def test_judging_instructions_reference_pair_id_not_model():
    assert "pair-7" in ab_mod.JUDGING_INSTRUCTIONS
    assert SECRET_MODEL_ID not in ab_mod.JUDGING_INSTRUCTIONS


def test_multi_meeting_multi_comparison_markdown_stays_blind():
    model_a, model_b = "fake-model-alpha", "fake-model-beta"
    comparisons = []
    idx = 1
    for meeting_id in ("m1", "m2"):
        for n, model_id in enumerate((model_a, model_b), start=1):
            rng = pair_rng(meeting_id, model_id)
            pairs = [build_pair(meeting_id, f"{model_id}::executive_summary", model_id,
                                 "CAND", "REF", rng)]
            pairs = assign_visible_ids(pairs, start=idx)
            idx += len(pairs)
            comparisons.append({"meeting_id": meeting_id, "comparison_index": n, "pairs": pairs})
    md = ab_mod.assemble_markdown(comparisons)
    assert model_a not in md and model_b not in md
    assert "## m1 — Comparison 1" in md and "## m1 — Comparison 2" in md
    assert "## m2 — Comparison 1" in md and "## m2 — Comparison 2" in md


# --- _pair_title ------------------------------------------------------

def test_pair_title_strips_model_prefix_for_executive_summary():
    assert ab_mod._pair_title(f"{SECRET_MODEL_ID}::executive_summary") == "Executive Summary"


def test_pair_title_strips_model_prefix_for_section():
    title = ab_mod._pair_title(f"{SECRET_MODEL_ID}::section_0:Gubernatorial Campaign Announcement")
    assert title == "Gubernatorial Campaign Announcement"
    assert SECRET_MODEL_ID not in title


# --- item 5: highlights are computed (bundled in the same call) but never
# threaded through to candidate_view, since nothing pairs them -------------

def _fake_client(section_json_texts, exec_json_text):
    """MagicMock client whose messages.create returns section summaries in
    order, then the executive-summary JSON last."""
    client = MagicMock()
    responses = list(section_json_texts) + [exec_json_text]
    call_count = [0]

    def create(**kwargs):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        msg = MagicMock()
        msg.content = [MagicMock(text=responses[idx])]
        return msg

    client.messages.create.side_effect = create
    return client


def _council_meeting():
    seg = Segment(segment_id=0, start_time=0.0, end_time=10.0,
                  speaker_label="SPEAKER_00", text="We call this meeting to order.")
    return Meeting(meeting_id="m1", city="Testville", date="2026-01-01",
                   event_kind="council", segments=[seg])


def test_synthesize_candidate_drops_unused_highlights():
    meeting = _council_meeting()
    gold_sections = [{"section_type": "discussion", "title": "Item 1",
                       "start_segment": 0, "end_segment": 0}]
    exec_json = '{"executive_summary": "Summary text.", "key_decisions": ["Decision A", "Decision B"]}'
    client = _fake_client(["Discussion content."], exec_json)

    result = ab_mod.synthesize_candidate(client, None, meeting, gold_sections)

    assert "highlights" not in result
    assert result["executive_summary"] == "Summary text."
    assert result["sections"] == [
        {"title": "Item 1", "section_type": "discussion", "content": "Discussion content."}
    ]


def test_reference_view_has_no_highlights_key():
    gold_summary = {
        "executive_summary": "Ref exec.",
        "key_decisions": ["Ref decision"],
        "sections": [{"section_type": "discussion", "title": "Item 1", "content": "Ref content."}],
    }
    ref = ab_mod._reference_view(gold_summary, is_interview=False)
    assert "highlights" not in ref
    assert ref["executive_summary"] == "Ref exec."

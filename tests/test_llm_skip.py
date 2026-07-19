"""Layer-3 LLM is skipped on Congressional Record runs (CREC is authoritative;
the local LLM hallucinates congressional names)."""
from __future__ import annotations

from run_local import should_run_llm


def test_llm_runs_by_default():
    assert should_run_llm(skip_llm=False, crec_request=None) is True


def test_skip_llm_flag_disables():
    assert should_run_llm(skip_llm=True, crec_request=None) is False


def test_congressional_record_run_skips_llm():
    # A --congressional-record run skips Layer 3: CREC is authoritative, and an
    # unresolved speaker should be an honest 'unidentified' -> review, not an LLM
    # hallucination.
    assert should_run_llm(skip_llm=False, crec_request=("2026-03-27", "house")) is False


def test_skip_flag_and_crec_both_skip():
    assert should_run_llm(skip_llm=True, crec_request=("2026-03-27", "house")) is False

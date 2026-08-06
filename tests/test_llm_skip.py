"""Layer-3 LLM is skipped on Congressional Record runs (CREC is authoritative;
the local LLM hallucinates congressional names) and on interview-kind meetings
(88-interview eval 2026-08-05: ~13% correct-name coverage — interview guests
are rarely full-named on air, so names come from review instead)."""
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


def test_llm_runs_by_default_with_no_event_kind():
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind=None) is True


def test_council_kind_unchanged():
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="council") is True


def test_debate_kind_unchanged():
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="debate") is True


def test_news_clip_skips_llm_even_without_skip_flag_or_crec():
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="news_clip") is False


def test_podcast_skips_llm():
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="podcast") is False


def test_press_conference_skips_llm():
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="press_conference") is False


def test_interview_kind_still_skips_when_skip_flag_and_crec_also_set():
    assert should_run_llm(skip_llm=True, crec_request=("2026-03-27", "house"), event_kind="podcast") is False

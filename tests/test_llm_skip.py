"""Layer-3 LLM speaker identification is gated by a master switch
(config.SPEAKER_ID_LLM_ENABLED, default OFF as of 2026-08-06 -- an 88-interview
eval found ~13-16% correct-name coverage across five models, and in practice
names come from human review). When the switch is off, should_run_llm returns
False for every combination of flags/event kinds -- no exceptions. When the
switch is on, the pre-existing skip rules still apply: --skip-llm, a
Congressional Record run (CREC is authoritative; the local LLM hallucinates
congressional names), and interview-kind meetings (news_clip/press_conference/
podcast -- interview guests are rarely full-named on air) all still skip
Layer 3."""
from __future__ import annotations

from run_local import should_run_llm
from src import config


# --- Master switch OFF (the shipped default): nothing runs Layer 3, period ---

def test_master_switch_off_disables_default_case(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", False)
    assert should_run_llm(skip_llm=False, crec_request=None) is False


def test_master_switch_off_disables_with_no_event_kind(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", False)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind=None) is False


def test_master_switch_off_disables_council(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", False)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="council") is False


def test_master_switch_off_disables_debate(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", False)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="debate") is False


def test_master_switch_off_disables_even_with_skip_llm_false_and_no_crec(monkeypatch):
    # Belt-and-suspenders: the knob alone is sufficient, no other flag needed.
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", False)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="podcast") is False
    assert should_run_llm(skip_llm=True, crec_request=None, event_kind="council") is False
    assert should_run_llm(skip_llm=False, crec_request=("2026-03-27", "house"), event_kind=None) is False


# --- Master switch ON: pre-existing behavior holds ---

def test_llm_runs_by_default_when_switch_on(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None) is True


def test_skip_llm_flag_disables(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=True, crec_request=None) is False


def test_congressional_record_run_skips_llm(monkeypatch):
    # A --congressional-record run skips Layer 3: CREC is authoritative, and an
    # unresolved speaker should be an honest 'unidentified' -> review, not an LLM
    # hallucination.
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=("2026-03-27", "house")) is False


def test_skip_flag_and_crec_both_skip(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=True, crec_request=("2026-03-27", "house")) is False


def test_llm_runs_by_default_with_no_event_kind_when_switch_on(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind=None) is True


def test_council_kind_unchanged_when_switch_on(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="council") is True


def test_debate_kind_unchanged_when_switch_on(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="debate") is True


def test_news_clip_skips_llm_even_without_skip_flag_or_crec(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="news_clip") is False


def test_podcast_skips_llm(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="podcast") is False


def test_press_conference_skips_llm(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=False, crec_request=None, event_kind="press_conference") is False


def test_interview_kind_still_skips_when_skip_flag_and_crec_also_set(monkeypatch):
    monkeypatch.setattr(config, "SPEAKER_ID_LLM_ENABLED", True)
    assert should_run_llm(skip_llm=True, crec_request=("2026-03-27", "house"), event_kind="podcast") is False

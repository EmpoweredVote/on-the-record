"""run_local.py prints the LLM token lines (src.llm_providers, INFO)."""
from __future__ import annotations

import logging

import run_local
from src import llm_providers


def test_enable_llm_usage_logging_prints_info_lines(capsys):
    run_local._enable_llm_usage_logging()
    with llm_providers.llm_call_site("topics"):
        llm_providers._log_usage("m", llm_providers.Usage(10, 3), False, 100)
    err = capsys.readouterr().err
    assert "[INFO] llm_usage call_site=topics model=m input_tokens=10 output_tokens=3" in err


def test_enable_llm_usage_logging_is_idempotent(capsys):
    run_local._enable_llm_usage_logging()
    run_local._enable_llm_usage_logging()
    llm_providers._log_usage("m", llm_providers.Usage(1, 1), True, 1)
    err = capsys.readouterr().err
    assert err.count("llm_usage") == 1
    assert err.count("[WARNING]") == 1


def test_enable_llm_usage_logging_leaves_other_loggers_alone():
    root_level = logging.getLogger().level
    run_local._enable_llm_usage_logging()
    assert logging.getLogger().level == root_level

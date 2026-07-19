"""Tests for Layer-3 eval scoring (src.speaker_id_eval)."""
from __future__ import annotations

from src.speaker_id_eval import classify, is_null_gold, summarize


def test_is_null_gold():
    assert is_null_gold(None) is True
    assert is_null_gold("") is True
    assert is_null_gold("Unidentified Speaker") is True
    assert is_null_gold("Jane Smith") is False


def test_classify_correct_name():
    assert classify("Jeff Merkley", "Jeff Merkley") == "correct"
    assert classify("Jeff Merkley", "Senator Merkley") == "correct"  # surname match


def test_classify_wrong_name():
    assert classify("Jeff Merkley", "Jane Smith") == "wrong"


def test_classify_safe_null():
    assert classify("Unidentified Speaker", None) == "safe_null"


def test_classify_hallucination():
    assert classify("Unidentified Speaker", "Mr. Bean") == "hallucination"


def test_classify_miss():
    assert classify("Jeff Merkley", None) == "miss"


def test_classify_fuzzy_surname_match():
    assert classify("Jeff Merkley", "Jeff Merkely") == "correct"  # transposed letters
    assert classify("Jeff Merkley", "Jeff Bilirakis") == "wrong"


def test_summarize_counts_and_rates():
    outcomes = ["correct", "correct", "miss", "safe_null", "hallucination", "wrong"]
    row = summarize("haiku", outcomes)
    assert row["model"] == "haiku"
    assert row["n"] == 6
    assert row["correct"] == 2
    assert row["hallucination"] == 1
    assert 0.0 <= row["accuracy"] <= 1.0


def test_summarize_empty_is_zero():
    row = summarize("x", [])
    assert row["n"] == 0
    assert row["accuracy"] == 0.0

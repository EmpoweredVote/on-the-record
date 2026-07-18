# tests/test_crec_align.py
from __future__ import annotations

from src.crec_align import _content_tokens, _overlap


def test_content_tokens_drops_stopwords_punct_and_short():
    toks = _content_tokens("The Senator moved to proceed, on the BILL!")
    assert toks == {"senator", "moved", "proceed", "bill"}


def test_content_tokens_empty():
    assert _content_tokens("") == set()
    assert _content_tokens(None) == set()


def test_overlap_coefficient():
    assert _overlap({"a", "b", "c"}, {"a", "b"}) == 1.0        # containment of smaller
    assert _overlap({"a", "b", "c", "d"}, {"a", "b"}) == 1.0
    assert _overlap({"a", "b"}, {"b", "x"}) == 0.5
    assert _overlap(set(), {"a"}) == 0.0
    assert _overlap({"a"}, set()) == 0.0

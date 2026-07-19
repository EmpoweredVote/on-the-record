"""Shared name normalization + tokenization for speaker-ID matching.

Single source of truth so the Layer-3 anchoring guardrail (src/llm_utils.py) and
the eval scorer (src/speaker_id_eval.py) can't drift apart.
"""
from __future__ import annotations

import re

HONORIFICS = {
    "mr", "mrs", "ms", "dr", "rep", "sen", "senator", "representative",
    "president", "chair", "chairman", "chairwoman", "chairperson",
    "councilmember", "council", "member", "mayor", "the", "hon", "honorable",
    "gov", "governor", "speaker",
}


def normalize(text: str) -> str:
    """Lowercase, non-alphanumerics -> spaces, collapse whitespace."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def significant_tokens(name: str) -> list[str]:
    """Name tokens minus honorifics and tokens shorter than 2 chars."""
    return [t for t in normalize(name).split() if len(t) >= 2 and t not in HONORIFICS]

# src/crec_align.py
"""Phase 3: align identity-annotated CREC turns onto anonymous diarized labels.

Given diarized segments (verbatim ASR/caption text + nameless speaker_labels)
and CREC turns annotated with identities (Phase 2 annotate_turns), align the two
ordered sequences by content-word overlap under a monotonic (order-preserving)
constraint, then aggregate per speaker_label to a resolved identity. Attaches
identity only — never touches timestamps/words (ADR-0001). Pure; no network.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .congress_roster import CongressMember

_STOPWORDS = frozenset("""
the a an and or but of to in on at for with as is are was were be been being
i you he she it we they that this these those my your his her its our their
will would shall should can could may might must do does did have has had not
""".split())

# A backtracked diagonal only counts as a real match above this overlap floor.
_MATCH_FLOOR = 0.1


def _content_tokens(text: str) -> set[str]:
    """Lowercased content-word set: drop punctuation, stopwords, tokens < 3 chars."""
    toks = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def _overlap(a: set, b: set) -> float:
    """Overlap coefficient: |a∩b| / min(|a|,|b|); 0.0 if either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))

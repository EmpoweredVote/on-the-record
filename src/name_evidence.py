"""Evidence that a spoken name belongs to a diarized speaker.

Pure: segments in, Evidence out. Implements rules E1-E4 and exclusions X1-X3
of docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import Segment

INTRO_WORDS = 60          # E1 window: first N words of the first substantial turn
MIN_TURN_WORDS = 10       # a turn shorter than this is not "substantial"
CALL_WINDOW_WORDS = 25    # E2 window: last N words before a turn change
THANK_WINDOW_WORDS = 10   # E3 window: first N words of the next speaker's turn

OFFICE_TITLES = (
    "Lieutenant Governor", "Attorney General", "Council Member", "Congresswoman",
    "Congressman", "Councilmember", "Councilwoman", "Councilman", "Representative",
    "Commissioner", "Prosecutor", "Treasurer", "Governor", "Senator", "Auditor",
    "Justice", "Sheriff", "Trustee", "Mayor", "Judge", "Rep.", "Sen.",
)
COURTESY_TITLES = (
    "Professor", "Reverend", "Doctor", "Pastor", "Father", "Rabbi", "Chief", "Imam",
    "Miss", "Mrs.", "Rev.", "Mr.", "Ms.", "Dr.",
)
_TITLE = "|".join(re.escape(t) for t in sorted(OFFICE_TITLES + COURTESY_TITLES, key=len, reverse=True))
_TOKEN = r"[A-Z][a-zA-Z'\u2019\-]+"
_NAME_1_3 = rf"{_TOKEN}(?:\s+{_TOKEN}){{0,2}}"
_NAME_2_3 = rf"{_TOKEN}(?:\s+{_TOKEN}){{1,2}}"

# Capitalized words that are not names (sentence starts, courtesies, fillers).
NOT_NAME = {
    "i", "sorry", "just", "not", "going", "gonna", "here", "very", "so", "really", "glad",
    "happy", "proud", "honored", "thrilled", "excited", "running", "sure", "also", "still",
    "actually", "currently", "pleased", "a", "an", "the", "madam", "chair", "chairman",
    "chairwoman", "members", "member", "committee", "everyone", "everybody", "all", "sir",
    "senators", "folks", "yes", "no", "okay", "ok", "thank", "thanks", "good", "morning",
    "afternoon", "evening", "today", "now", "well", "speaker", "president", "colleagues",
    "absolutely", "great", "right", "mud", "tonight", "again", "please", "welcome",
}
# Words just before a name that mean "this is someone else" (X1).
MENTION_CUES = {
    "colleague", "colleagues", "said", "says", "asked", "told", "by", "author", "sponsor",
    "son", "daughter", "wife", "husband", "father", "mother", "brother", "sister", "friend",
}

_R_MY_NAME = re.compile(rf"(?i:\bmy\s+name(?:'s|\s+is)\s+)(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})")
_R_IM_TITLED = re.compile(rf"(?i:\b(?:I'm|I\s+am)\s+)(?P<title>{_TITLE})\s+(?P<name>{_NAME_1_3})")
_R_IM = re.compile(
    rf"(?i:\b(?:I'm|I\s+am)\s+)(?P<name>{_NAME_2_3})"
    r"(?=\s*(?:[.,;!?]|$|(?i:and|with|from|of|on|representing|here|a|an|the)\b))"
)
_R_AFFIL = re.compile(
    r"(?i:\b(?:I'm\s+with|I\s+am\s+with|with|from|representing|on\s+behalf\s+of|here\s+for))\s+"
    r"(?P<org>(?:(?i:the)\s+)?[A-Z][^.,;!?]{1,80})"
)


@dataclass
class Turn:
    label: str
    segment_ids: list[int] = field(default_factory=list)
    words: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass(frozen=True)
class Evidence:
    kind: str                     # "E1" | "E2" | "E3" | "E4"
    label: str                    # speaker label the name is attributed to
    name: str                     # as spoken, title removed, e.g. "Aaron Spiegel"
    title: Optional[str]          # "Rabbi", "Senator", ... or None
    affiliation: Optional[str]    # stated organisation / place, E1 only
    quote: str                    # the exact words the name came from
    segment_id: int               # segment where the quote starts

    @property
    def partial(self) -> bool:
        return len(self.name.split()) < 2


def build_turns(segments: Iterable[Segment]) -> list[Turn]:
    """Consecutive same-label, non-empty segments merged into turns (time order)."""
    turns: list[Turn] = []
    for s in sorted(segments, key=lambda s: s.start_time):
        words = (s.text or "").split()
        if not words:
            continue
        if turns and turns[-1].label == s.speaker_label:
            turns[-1].segment_ids.append(s.segment_id)
            turns[-1].words.extend(words)
        else:
            turns.append(Turn(label=s.speaker_label, segment_ids=[s.segment_id], words=list(words)))
    return turns


def clean_name(raw: str) -> Optional[str]:
    """Trim non-name trailing tokens; None if nothing name-like is left."""
    toks = raw.split()
    while toks and toks[-1].lower().strip("'\u2019") in NOT_NAME:
        toks.pop()
    if not toks or len(toks) > 3 or len(" ".join(toks)) > 40:
        return None
    if any(t.lower() in NOT_NAME for t in toks):
        return None
    return " ".join(toks)


def is_mention(text: str, start: int) -> bool:
    """X1: the 4 words before position `start` mark a mention of someone else."""
    before = re.findall(r"[a-z']+", text[:start].lower())[-4:]
    return any(w in MENTION_CUES for w in before)


def _affiliation_after(text: str, end: int) -> Optional[str]:
    m = _R_AFFIL.search(" ".join(text[end:].split()[:30]))
    return m.group("org").strip() if m else None


def find_self_intros(turns: list[Turn]) -> list[Evidence]:
    """E1: the first self-introduction in each label's first substantial turn."""
    out: list[Evidence] = []
    seen: set[str] = set()
    for turn in turns:
        if turn.label in seen or len(turn.words) < MIN_TURN_WORDS:
            continue
        seen.add(turn.label)
        window = " ".join(turn.words[:INTRO_WORDS])
        matches = []
        for rx in (_R_MY_NAME, _R_IM_TITLED, _R_IM):
            for m in rx.finditer(window):
                name = clean_name(m.group("name"))
                if name and not is_mention(window, m.start()):
                    matches.append((m.start(), m, name))
        if not matches:
            continue
        start, m, name = min(matches, key=lambda x: x[0])  # X3: first intro only
        out.append(Evidence(
            kind="E1", label=turn.label, name=name, title=m.groupdict().get("title"),
            affiliation=_affiliation_after(window, m.end()),
            quote=window[start:m.end() + 60].strip(), segment_id=turn.segment_ids[0],
        ))
    return out

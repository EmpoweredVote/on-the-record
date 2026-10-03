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
    "absolutely", "great", "right", "tonight", "again", "please", "welcome",
}
# Words just before a name that mean "this is someone else" (X1).
# Note: "said", "says", "asked", "told" are handled by the (b) rule in is_mention
MENTION_CUES = {
    "colleague", "colleagues", "by", "author", "sponsor",
    "son", "daughter", "wife", "husband", "father", "mother", "brother", "sister", "friend",
}

_R_MY_NAME = re.compile(rf"(?i:\bmy\s+name(?:['′\u2019]s|\s+is)\s+)(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})")
_R_IM_TITLED = re.compile(rf"(?i:\b(?:I['′\u2019]m|I\s+am)\s+)(?P<title>{_TITLE})\s+(?P<name>{_NAME_1_3})")
_R_IM = re.compile(
    rf"(?i:\b(?:I['′\u2019]m|I\s+am)\s+)(?P<name>{_NAME_2_3})"
    r"(?=\s*(?:[.,;!?]|$|(?i:and|with|from|of|on|representing|here|a|an|the)\b))"
)
_R_AFFIL = re.compile(
    r"(?i:\b(?:I['′\u2019]m\s+with|I\s+am\s+with|with|from|representing|on\s+behalf\s+of|here\s+for))\s+"
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
    """Trim non-name trailing tokens; None if nothing name-like is left.

    Removes trailing contractions (I'm, I'll, I've, I'd, n't) and rejects
    names with possessive 's/'s endings.
    """
    toks = raw.split()

    # Drop trailing contraction tokens (I'm, I'll, I've, I'd, or n't)
    while toks:
        last_lower = toks[-1].lower()
        # Remove apostrophes for comparison
        last_stripped = last_lower.strip("'\u2019")
        # Check if it's a contraction or NOT_NAME token
        if last_stripped in ("i'm", "i'll", "i've", "i'd", "i've") or last_lower.endswith("n't") or last_stripped in NOT_NAME:
            toks.pop()
        else:
            break

    if not toks or len(toks) > 3 or len(" ".join(toks)) > 40:
        return None

    # Reject if last token is possessive ('s or 's)
    if toks[-1].endswith("'s") or toks[-1].endswith("'s"):
        return None

    # Reject if any token is in NOT_NAME
    if any(t.lower() in NOT_NAME for t in toks):
        return None

    return " ".join(toks)


def is_mention(text: str, name_start: int, name_end: int) -> bool:
    """X1: Check if a name is a mention of someone else (not the speaker).

    A name is a mention if:
    (a) The word immediately before the name (skipping optional title), is in MENTION_CUES
    (b) The word before name/title is "as" and word right after name is said/says/asked/mentioned/noted
    (c) The name is immediately followed by 's or 's (possessive)
    """
    # Extract words before and after the name match
    before_text = text[:name_start].lower()
    after_text = text[name_end:].lower()

    # (c) Check for possessive: name followed by 's or 's
    if after_text.startswith("'s") or after_text.startswith("'s"):
        return True

    # Extract word tokens before the name (up to the match start)
    before_words = re.findall(r"[a-z'′]+", before_text)

    # (a) Check if word immediately before name is a mention cue
    # Skip one optional title word first
    if before_words:
        # Check if the word immediately before is a title (in OFFICE_TITLES or COURTESY_TITLES)
        last_word = before_words[-1]
        title_set = {t.lower() for t in OFFICE_TITLES + COURTESY_TITLES}

        if last_word in title_set:
            # Skip the title and check the word before it
            if len(before_words) > 1 and before_words[-2] in MENTION_CUES:
                return True
        elif last_word in MENTION_CUES:
            # Direct mention cue before the name
            return True

    # (b) Check for "as [name] said/says/asked/mentioned/noted" pattern
    # The word before name/title is "as" and word after name is said/says/asked/mentioned/noted
    if before_words and before_words[-1] == "as":
        # Check word right after the name
        after_words = re.findall(r"[a-z'′]+", after_text)
        if after_words and after_words[0] in ("said", "says", "asked", "mentioned", "noted"):
            return True

    return False


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
                # Check if the name is a mention using the name group's span
                if name and not is_mention(window, m.start("name"), m.end("name")):
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


_CALL_PATTERNS = (
    re.compile(rf"(?i:\b(?:yes|okay|ok|all\s+right|alright|go\s+ahead|please)\b,?\s+)"
               rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})\s*[.?!,]"),
    re.compile(rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3}),\s+"
               r"(?i:would\s+you|you['′\u2019]re\s+recognized|you\s+are\s+recognized|please|go\s+ahead|"
               r"the\s+floor\s+is\s+yours|you['′\u2019]re\s+up|come\s+on\s+up|welcome)"),
    re.compile(rf"(?i:\b(?:next|now)\b[^.?!]{{0,30}}?\b(?:hear\s+from|have|call(?:\s+up)?|invite|"
               rf"recognize|welcome|is)\s+)(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})"),
    re.compile(rf"(?i:\b(?:welcome|recognize|calling|call\s+up|I\s+see)\s+)"
               rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})"),
)
_R_THANK = re.compile(rf"(?i:\bthank(?:s|\s+you)(?:\s+(?:so|very)\s+much)?,?\s+)"
                      rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})")


def _preceded_by_thanks(text: str, start: int) -> bool:
    return any(w.startswith("thank") for w in re.findall(r"[a-z]+", text[:start].lower())[-3:])


def find_chair_calls(turns: list[Turn]) -> list[Evidence]:
    """E2: the last name called in the words just before a turn change."""
    out: list[Evidence] = []
    for prev, nxt in zip(turns, turns[1:]):
        if prev.label == nxt.label:
            continue
        window = " ".join(prev.words[-CALL_WINDOW_WORDS:])
        best = None
        for rx in _CALL_PATTERNS:
            for m in rx.finditer(window):
                name = clean_name(m.group("name"))
                if not name or is_mention(window, m.start("name"), m.end("name")) or _preceded_by_thanks(window, m.start()):
                    continue
                if best is None or m.start() > best[0]:
                    best = (m.start(), m, name)
        if best:
            start, m, name = best
            out.append(Evidence(kind="E2", label=nxt.label, name=name, title=m.groupdict().get("title"),
                                affiliation=None, quote=window[start:m.end()].strip(),
                                segment_id=prev.segment_ids[-1]))
    return out


def find_thank_backs(turns: list[Turn]) -> list[Evidence]:
    """E3: "thank you, <name>" at the start of the next speaker's turn."""
    out: list[Evidence] = []
    for prev, nxt in zip(turns, turns[1:]):
        if prev.label == nxt.label:
            continue
        window = " ".join(nxt.words[:THANK_WINDOW_WORDS])
        m = _R_THANK.search(window)
        if not m:
            continue
        name = clean_name(m.group("name"))
        if name:
            out.append(Evidence(kind="E3", label=prev.label, name=name, title=m.group("title"),
                                affiliation=None, quote=window[m.start():m.end()].strip(),
                                segment_id=nxt.segment_ids[0]))
    return out

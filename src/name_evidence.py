"""Evidence that a spoken name belongs to a diarized speaker.

Pure: segments in, Evidence out. Implements rules E1-E4 and exclusions X1-X3
of docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import Segment
from .name_matching import significant_tokens

INTRO_WORDS = 60          # E1 window: first N words of the first substantial turn
MIN_TURN_WORDS = 10       # a turn shorter than this is not "substantial"
CALL_WINDOW_WORDS = 25    # E2 window: last N words before a turn change
THANK_WINDOW_WORDS = 10   # E3 window: first N words of the next speaker's turn

OFFICE_TITLES = (
    "Lieutenant Governor", "Attorney General", "Council Member", "Congresswoman",
    "Congressman", "Councilmember", "Councilwoman", "Councilman", "Representative",
    "Commissioner", "Prosecutor", "Treasurer", "Governor", "Senator", "Auditor",
    "Justice", "Sheriff", "Trustee", "Mayor", "Judge", "Rep.", "Sen.", "Gov.",
)
COURTESY_TITLES = (
    "Professor", "Reverend", "Doctor", "Pastor", "Father", "Rabbi", "Chief", "Imam",
    "Miss", "Mrs.", "Rev.", "Mr.", "Ms.", "Dr.",
)
def _title_alt(titles: tuple) -> str:
    """Alternation of titles; abbreviations accept the period optionally ("Mr" and "Mr.")."""
    abbr = {t.rstrip(".") for t in titles if t.endswith(".")}
    forms = sorted({t.rstrip(".") for t in titles}, key=len, reverse=True)
    return r"(?:" + "|".join(re.escape(t) + (r"\.?" if t in abbr else "") for t in forms) + r")"


TITLE_QUALIFIERS = ("State", "County", "City", "U.S.", "Former", "Assistant", "Deputy")
_QUAL = r"(?:(?:" + "|".join(re.escape(q) for q in TITLE_QUALIFIERS) + r")\s+)*"
_TITLE_ONLY = _title_alt(OFFICE_TITLES + COURTESY_TITLES)
_OFFICE_ONLY = _title_alt(OFFICE_TITLES)
_TITLE = _QUAL + _TITLE_ONLY
_OFFICE_TITLE = _QUAL + _OFFICE_ONLY
_L = r"[^\W\d_]"                      # any Unicode letter
_UP = r"[A-Z\u00C0-\u00D6\u00D8-\u00DE]"  # capital letter (Latin incl. accented)
_TOKEN = rf"{_UP}(?:{_L}|['\u2019\-](?={_L}))+(?!{_L})"
_NAME_1_3 = rf"{_TOKEN}(?:\s+{_TOKEN}){{0,2}}"
_NAME_2_3 = rf"{_TOKEN}(?:\s+{_TOKEN}){{1,2}}"
_TITLE_WORDS = {w.lower() for t in OFFICE_TITLES + COURTESY_TITLES for w in t.rstrip(".").split()}
_QUAL_WORDS = {q.lower().rstrip(".") for q in TITLE_QUALIFIERS}

# A capitalized word after a 3-token capture that starts a new clause or is a
# name suffix does not make the name boundary unknown.
_CLAUSE_STARTS = {"i", "i'm", "i'll", "i've", "i'd", "i’m", "i’ll", "i’ve", "i’d"}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

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
    r"(?=\s*[.,;!?]|\s*$|\s+(?i:and|with|from|of|on|representing|here|a|an|the)\b|\s+I['’]m\b)"
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
        return len(significant_tokens(self.name)) < 2


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


def _trim_greedy(raw: str, after: str) -> str:
    """A 3-token capture followed by another capitalized word has an unknown
    boundary ("Chris Swanson American Federation"): keep the first two tokens.
    Not when that word starts a new clause ("I", "I'm") or is a suffix (Jr, III)."""
    toks = raw.split()
    if len(toks) != 3:
        return raw
    m = re.match(rf"\s+({_UP}[^\s,.;:!?]*)", after)
    if not m:
        return raw
    nxt = m.group(1).lower().rstrip(".")
    if nxt in _CLAUSE_STARTS or nxt in _NAME_SUFFIXES:
        return raw
    return " ".join(toks[:2])


def split_name_title(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Trim non-name tokens; return (name, title_found_inside_the_capture).

    Removes trailing contractions and filler, drops everything up to and
    including the last title token ("State Representative Francesca" ->
    title "Representative"), and rejects possessives, ALL-CAPS runs, NOT_NAME
    words and bare titles. (None, None) if nothing name-like is left.
    """
    toks = raw.split()

    # Drop trailing contraction / filler tokens
    while toks:
        last_lower = toks[-1].lower()
        last_stripped = last_lower.strip("'\u2019")
        if last_stripped in ("i'm", "i'll", "i've", "i'd") or last_lower.endswith("n't") or last_stripped in NOT_NAME:
            toks.pop()
        else:
            break

    title = None
    title_idx = [i for i, t in enumerate(toks) if t.lower().rstrip(".") in _TITLE_WORDS]
    if title_idx:
        name_like = [t for i, t in enumerate(toks)
                     if i not in title_idx and t.lower().rstrip(".") not in _QUAL_WORDS]
        if not name_like:
            return None, None  # only titles / qualifiers ("Senator", "State Senator")
        last = title_idx[-1]
        if last < len(toks) - 1:  # a name token follows the title: drop through it
            title = toks[last]
            toks = toks[last + 1:]
        # else the title word is the surname ("Jim Justice", "Mary Pastor"): keep it

    if not toks or len(toks) > 3 or len(" ".join(toks)) > 40:
        return None, None

    # Reject possessives (straight or curly apostrophe, any case)
    if toks[-1].lower().endswith(("'s", "\u2019s")):
        return None, None

    if any(t.lower() in NOT_NAME for t in toks):
        return None, None

    # ASR shouting / sentence fragments: every token ALL CAPS
    if all(t.isupper() for t in toks):
        return None, None

    return " ".join(toks), title


def clean_name(raw: str) -> Optional[str]:
    """Name part of split_name_title (None if rejected)."""
    return split_name_title(raw)[0]


def _norm_title(raw: Optional[str]) -> Optional[str]:
    """Drop qualifiers ("State Senator" -> "Senator"); None stays None."""
    if not raw:
        return None
    words = raw.split()
    while len(words) > 1 and words[0].lower().rstrip(".") in _QUAL_WORDS:
        words.pop(0)
    return " ".join(words)


def resolve_name_title(m: "re.Match", raw_name: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """(name, title) for a pattern match: explicit title group wins, else a title found in the capture."""
    name, inner = split_name_title(raw_name if raw_name is not None else m.group("name"))
    gd = m.groupdict()
    return name, _norm_title(gd.get("title")) or inner


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
    if after_text.startswith(("'s", "\u2019s")):
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
                raw = m.group("name")
                if rx is not _R_IM:
                    raw = _trim_greedy(raw, window[m.end("name"):])
                name, title = resolve_name_title(m, raw)
                if name and not is_mention(window, m.start("name"), m.end("name")):
                    matches.append((m.start(), m, name, title))
        if not matches:
            continue
        start, m, name, title = min(matches, key=lambda x: x[0])  # X3: first intro only
        out.append(Evidence(
            kind="E1", label=turn.label, name=name, title=title,
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
    re.compile(rf"(?i:\b(?:(?:we['′\u2019]ll|we\s+will)\s+)?call\s+)"
               rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})"),
    re.compile(rf"(?:^|[.?!]\s*)(?i:(?:yes|okay|ok|all\s+right|alright),?\s+)?"
               rf"(?:(?P<title>{_OFFICE_TITLE})\s+)(?P<name>{_NAME_1_3})\s*[.?!]*$"),
)
_R_THANK = re.compile(rf"(?i:\bthank(?:s|\s+you)(?:\s+(?:so|very)\s+much)?,?\s+)"
                      rf"(?:(?P<title>{_TITLE})\s+)?(?P<name>{_NAME_1_3})")


def _preceded_by_thanks(text: str, start: int) -> bool:
    """Check if match is preceded by thank/thanks in the SAME sentence.

    Returns True only if a thank-word appears between the last sentence boundary
    (. ? ! or start of text) and the match position. If the match is at or
    immediately after a boundary, returns False (new sentence).
    """
    # Check if match is at or right after a sentence boundary
    if start == 0:
        return False

    # Check if char at start is a boundary marker
    if text[start] in ".?!":
        return False

    # Check if previous non-space char is a boundary
    i = start - 1
    while i >= 0 and text[i] == " ":
        i -= 1
    if i >= 0 and text[i] in ".?!":
        return False

    # Find the last sentence boundary (. ? ! or start of text) before the match
    before_match = text[:start]
    last_boundary = max(
        before_match.rfind("."),
        before_match.rfind("?"),
        before_match.rfind("!")
    )
    # Extract text from last boundary to match start (same sentence)
    if last_boundary == -1:
        same_sentence = before_match
    else:
        same_sentence = before_match[last_boundary + 1:]

    # Check if any thank-word appears in the same sentence
    return any(w.startswith("thank") for w in re.findall(r"[a-z]+", same_sentence.lower()))


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
                name, title = resolve_name_title(m)
                if not name or is_mention(window, m.start("name"), m.end("name")) or _preceded_by_thanks(window, m.start()):
                    continue
                if best is None or m.start() > best[0]:
                    best = (m.start(), m, name, title)
        if best:
            start, m, name, title = best
            out.append(Evidence(kind="E2", label=nxt.label, name=name, title=title,
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
        name, title = resolve_name_title(m)
        if name:
            out.append(Evidence(kind="E3", label=prev.label, name=name, title=title,
                                affiliation=None, quote=window[m.start():m.end()].strip(),
                                segment_id=nxt.segment_ids[0]))
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", s.lower()).split())


def extract_evidence(segments: list[Segment], captions_text: Optional[str] = None) -> list[Evidence]:
    """All E1-E3 evidence, plus E4 where source captions contain the full name."""
    turns = build_turns(segments)
    found = find_self_intros(turns) + find_chair_calls(turns) + find_thank_backs(turns)
    if captions_text:
        caps = f" {_norm(captions_text)} "
        seen: set[tuple[str, str]] = set()
        for e in list(found):
            key = (e.label, _norm(e.name))
            if e.partial or key in seen or f" {key[1]} " not in caps:
                continue
            seen.add(key)
            found.append(Evidence(kind="E4", label=e.label, name=e.name, title=e.title,
                                  affiliation=None, quote="(source captions)", segment_id=e.segment_id))
    return found

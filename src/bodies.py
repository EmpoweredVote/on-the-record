"""Per-body adapter config: what agenda sections MEAN for a given body.

Stage and public-comment facts come from here (encoded from official council
rules), never from an LLM. Sources for Bloomington: spike findings doc
2026-07-27 + "Rules for Making Public Comment" (adopted 2024-06-05, amended
2025-08-06). Section matching is by header keyword, not number, so agenda
renumbering doesn't break classification.

Keyword matching ignores header parentheticals: "Reports (a maximum of twenty
minutes is set aside ...)" must hit REPORTS, but "twenty minutes" contains
"MINUTES" case-insensitively, so keywords are matched against the header with
any parenthetical (and asterisks) stripped.
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SectionRule:
    header_keyword: str            # matched case-insensitively against the header
    kind: str                      # default kind for items in this section
    stage: Optional[str] = None
    public_comment: bool = False
    public_comment_note: Optional[str] = None


@dataclass(frozen=True)
class BodyConfig:
    slug: str                      # body slug, also used in meeting slugs
    city: str
    state: str
    meeting_title_prefix: str      # OnBoard title filter
    meeting_type: str
    event_kind: str
    timezone: str                  # IANA
    section_rules: tuple[SectionRule, ...]
    source_note: str


_GENERAL_COMMENT_NOTE = (
    "General public comment period: one comment of up to 3 minutes, at either "
    "this period or the other general period (not both), from the podium — no "
    "advance sign-up."
)

BLOOMINGTON_COMMON_COUNCIL = BodyConfig(
    slug="bloomington-city-council",
    city="Bloomington",
    state="IN",
    meeting_title_prefix="Common Council Regular Session",
    meeting_type="Regular Session",
    event_kind="council",
    timezone="America/Indiana/Indianapolis",
    section_rules=(
        SectionRule("ROLL CALL", kind="procedural"),
        SectionRule("AGENDA SUMMATION", kind="procedural"),
        SectionRule("MINUTES", kind="minutes"),
        SectionRule("REPORTS", kind="report"),
        SectionRule("APPOINTMENTS", kind="appointment"),
        SectionRule(
            "FIRST READING", kind="legislation", stage="First reading",
            public_comment=False,
            public_comment_note=(
                "First readings are typically read by title only; public comment "
                "on this item comes at its second reading."
            ),
        ),
        SectionRule(
            "SECOND READING", kind="legislation",
            stage="Second reading — final vote",
            public_comment=True,
            public_comment_note=(
                "Public comment is taken on this item during the meeting before "
                "the vote."
            ),
        ),
        SectionRule(
            "PUBLIC COMMENT", kind="public-comment",
            public_comment=True, public_comment_note=_GENERAL_COMMENT_NOTE,
        ),
        SectionRule("COUNCIL SCHEDULE", kind="procedural"),
        SectionRule("ADJOURNMENT", kind="procedural"),
    ),
    source_note=(
        "Section semantics: Bloomington Common Council agenda template + Rules "
        "for Making Public Comment (2024-06-05, am. 2025-08-06); see spike "
        "findings 2026-07-27."
    ),
)


@dataclass(frozen=True)
class ItemClassification:
    kind: str
    stage: Optional[str]
    public_comment: bool
    public_comment_note: Optional[str]


_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")


def _matchable_header(header: str) -> str:
    """Header text used for keyword matching: parentheticals and asterisks
    stripped, uppercased."""
    return _PARENTHETICAL_RE.sub("", header).replace("*", "").upper()


def classify_item(item, body: BodyConfig) -> ItemClassification:
    """Map a ParsedItem to kind/stage/comment via the body's section rules."""
    header = _matchable_header(item.section)
    rule = None
    for r in body.section_rules:
        if r.header_keyword in header:
            rule = r
            break
    if rule is None:
        return ItemClassification("other", None, False, None)
    kind = rule.kind
    if kind == "legislation":
        ref = (item.legislation_ref or "").lower()
        kind = "resolution" if "resolution" in ref else "ordinance"
    # The "Reports ... Public" sub-item is a general comment period. The real
    # agenda titles it "Public*" (footnote asterisk), so strip asterisks.
    if rule.kind == "report" and item.title_raw.strip().strip("*").strip().upper() in (
        "PUBLIC", "REPORTS FROM THE PUBLIC",
    ):
        return ItemClassification("public-comment", None, True, _GENERAL_COMMENT_NOTE)
    return ItemClassification(kind, rule.stage, rule.public_comment, rule.public_comment_note)

"""Parse Bloomington Common Council agenda text into structured items.

Input is the plain text extracted from an OnBoard agenda PDF (see
``src/pdf_text.py``). The format is a numbered outline:

    1. Roll Call
    ...
    6. Legislation for First Readings
    A. Ordinance 2026-16 – To Amend an Ordinance Fixing the Salaries ...
    (title may wrap across up to 3 lines with no indentation cue)
    Council Sponsor - N/A

Hazards handled here:

- Page-footer footnotes bleed into the text (e.g. a Zoom URL line right
  after a section header, CATS/YouTube URLs at the end). These are skipped
  and never joined into titles.
- Section headers may themselves wrap when they carry a long parenthetical
  ("8. Additional Public Comment* (a maximum of twenty-five minutes is set
  aside for / this section)"). Continuation is joined only while the
  header's parentheses are unbalanced, so trailer text after
  "10. Adjournment" is not absorbed.
- Legislation refs use an en-dash separator for ordinances but a plain
  hyphen for at least one resolution; the ref regex keys off the
  "Ordinance/Resolution NNNN-N" token instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_SECTION_RE = re.compile(r"^(10|[1-9])\.\s+(\S.*?)\s*$")
_ITEM_RE = re.compile(r"^([A-Z])\.\s+(\S.*?)\s*$")
_SPONSOR_RE = re.compile(r"^Council\s+Sponsors?\s*[-:–—]\s*(.+?)\s*$", re.IGNORECASE)
# Footnotes: a digit glued (or nearly glued) to a URL ("1https://...",
# "2 https://catstv.net/") or a bare URL line.
_FOOTNOTE_RE = re.compile(r"^(?:\d+\s*)?https?://\S+", re.IGNORECASE)
_LEGISLATION_RE = re.compile(r"\b((?:Appropriation\s+)?(?:Ordinance|Resolution))\s+(\d{4}-\d+)")


@dataclass
class ParsedItem:
    position: int  # 1-based across the whole agenda
    item_number: str  # "6A", or "1" for a section with no lettered items
    section: str  # verbatim section header (parenthetical kept)
    section_number: int
    title_raw: str  # verbatim item text, continuation lines joined with single spaces
    legislation_ref: Optional[str] = None
    sponsor: Optional[str] = None  # None when absent or N/A
    extra_lines: list[str] = field(default_factory=list)


def _extract_legislation_ref(title: str) -> Optional[str]:
    m = _LEGISLATION_RE.search(title)
    if not m:
        return None
    kind = re.sub(r"\s+", " ", m.group(1))
    return f"{kind} {m.group(2)}"


def _parens_unbalanced(text: str) -> bool:
    return text.count("(") > text.count(")")


def parse_agenda(text: str) -> list[ParsedItem]:
    """Parse agenda text into a flat, position-ordered list of items.

    Sections with no lettered items become one item each (item_number =
    str(section_number), title_raw = the section header) so positions cover
    the whole agenda in document order and nothing is silently dropped.
    """
    items: list[ParsedItem] = []

    section_header: Optional[str] = None
    section_number: int = 0
    section_has_items = False
    header_open = False  # section header has an unbalanced "(" -> still wrapping

    # In-progress lettered item
    item_letter: Optional[str] = None
    title_parts: list[str] = []
    sponsor: Optional[str] = None
    sponsor_seen = False
    extra_lines: list[str] = []

    in_footnote = False  # skipping a footnote and its wrapped continuation

    def flush_item() -> None:
        nonlocal item_letter, title_parts, sponsor, sponsor_seen, extra_lines
        if item_letter is None:
            return
        title = " ".join(title_parts)
        items.append(
            ParsedItem(
                position=len(items) + 1,
                item_number=f"{section_number}{item_letter}",
                section=section_header or "",
                section_number=section_number,
                title_raw=title,
                legislation_ref=_extract_legislation_ref(title),
                sponsor=sponsor,
                extra_lines=extra_lines,
            )
        )
        item_letter = None
        title_parts = []
        sponsor = None
        sponsor_seen = False
        extra_lines = []

    def flush_section() -> None:
        """Close the current section; emit a headline-only item if it had none."""
        flush_item()
        if section_header is not None and not section_has_items:
            items.append(
                ParsedItem(
                    position=len(items) + 1,
                    item_number=str(section_number),
                    section=section_header,
                    section_number=section_number,
                    title_raw=section_header,
                    legislation_ref=None,
                    sponsor=None,
                    extra_lines=[],
                )
            )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = _SECTION_RE.match(line)
        if m:
            flush_section()
            section_number = int(m.group(1))
            section_header = m.group(2)
            section_has_items = False
            header_open = _parens_unbalanced(section_header)
            in_footnote = False
            continue

        m = _ITEM_RE.match(line)
        if m and section_header is not None:
            flush_item()
            item_letter = m.group(1)
            title_parts = [m.group(2)]
            section_has_items = True
            header_open = False
            in_footnote = False
            continue

        m = _SPONSOR_RE.match(line)
        if m:
            if item_letter is not None:
                value = m.group(1)
                sponsor = None if value.strip().upper() in {"N/A", "NA", "NONE"} else value
                sponsor_seen = True
            in_footnote = False
            continue

        if _FOOTNOTE_RE.match(line):
            in_footnote = True
            continue
        if in_footnote:
            # Wrapped continuation of a footnote (e.g. "LPz.1 Meeting ID: ...").
            continue

        if header_open and section_header is not None:
            # Section header wrapped mid-parenthetical; keep joining.
            section_header = f"{section_header} {line}"
            header_open = _parens_unbalanced(section_header)
            continue

        if item_letter is not None:
            if sponsor_seen:
                extra_lines.append(line)
            else:
                title_parts.append(line)
            continue

        # Preamble before section 1, or trailer text after a headline-only
        # section (accessibility notice, "*" footnote text, etc.) -> skip.

    flush_section()
    return items

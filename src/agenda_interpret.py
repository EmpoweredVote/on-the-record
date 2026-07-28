"""LLM plain-language interpretation of agenda items, behind a groundedness gate.

Contract: the model explains WHAT an item is and WHAT IS BEING DECIDED, in
plain language, from the agenda title + attached legislation/staff text. It
never states procedure (stage/public comment — that's src/bodies.py) and its
output is rejected wholesale if any number or legislation ref it emits is not
present in the source text. Abstain-don't-guess, like llm_utils._name_is_anchored.
"""
import json
import re
from dataclasses import dataclass
from typing import Optional

from . import config
from .agenda_parse import ParsedItem

_SYSTEM = (
    "You explain city-council agenda items to ordinary residents. Plain, "
    "neutral language; no government jargon; no opinions. Use ONLY the "
    "provided source text — if it does not say something, do not say it. "
    "Reply with JSON only: {\"summary_plain\": one or two sentences on what "
    "this item is, \"decision_plain\": one sentence on what the council is "
    "actually deciding, or null if nothing is being decided}."
)

_NUM_RE = re.compile(r"\d[\d,.]*")
# IGNORECASE: a lowercase invented ref ("resolution 2026-16") must still be
# extracted from generated text so the gate can check it against the source.
_REF_RE = re.compile(
    r"\b(?:Appropriation\s+)?(?:Ordinance|Resolution)\s+\d{4}-\d+", re.IGNORECASE
)


@dataclass
class InterpretResult:
    summary_plain: Optional[str]
    decision_plain: Optional[str]
    rejected_reason: Optional[str] = None


def build_interpret_prompt(item: ParsedItem, source_text: str) -> str:
    return (
        f"Agenda item (verbatim): {item.title_raw}\n"
        f"Section: {item.section}\n\n"
        f"Source text (agenda + attached legislation/staff memo excerpts):\n"
        f"{source_text}"
    )


def ungrounded_tokens(generated: str, source: str) -> list[str]:
    """Numbers or legislation refs in `generated` that are absent from `source`.

    Number matching is deliberately permissive substring matching: it catches
    INVENTED numbers, not paraphrases ("4 percent" vs source "4%" both ground
    on "4"). A mis-summarized pending ordinance is worse than no summary.
    """
    source_norm = source.lower()
    bad: list[str] = []
    for tok in _NUM_RE.findall(generated):
        if tok.strip(",.").lower() not in source_norm:
            bad.append(tok.strip(",."))
    for m in _REF_RE.finditer(generated):
        if m.group(0).lower() not in source_norm:
            bad.append(m.group(0))
    return bad


def _string_or_none(value) -> Optional[str]:
    """Non-string or empty JSON field values are treated as absent, not errors."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def interpret_item(client, item: ParsedItem, source_text: str) -> InterpretResult:
    response = client.messages.create(
        model=config.AGENDA_INTERPRET_MODEL,
        max_tokens=config.AGENDA_INTERPRET_MAX_TOKENS,
        system=_SYSTEM,
        messages=[{"role": "user", "content": build_interpret_prompt(item, source_text)}],
    )
    text = response.content[0].text
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return InterpretResult(None, None, rejected_reason="no JSON in reply")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return InterpretResult(None, None, rejected_reason="malformed JSON")
    summary = _string_or_none(payload.get("summary_plain"))
    decision = _string_or_none(payload.get("decision_plain"))
    if summary is None and decision is None:
        return InterpretResult(None, None, rejected_reason="empty payload")
    combined = " ".join(filter(None, [summary, decision]))
    bad = ungrounded_tokens(combined, f"{item.title_raw}\n{source_text}")
    if bad:
        return InterpretResult(
            None, None,
            rejected_reason=f"ungrounded tokens: {', '.join(sorted(set(bad)))}",
        )
    return InterpretResult(summary, decision)

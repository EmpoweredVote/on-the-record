from __future__ import annotations
import json
import re
from .models import CrossCheckVerdict

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_SYSTEM = ("You are an independent fact-checker. Read the SOURCE cold and judge a "
           "proposed quote. Respond with ONLY the requested JSON.")

_INSTRUCTIONS = """A proposed evidence quote attributed to {name}:
QUOTE: {quote}
CONTEXT SHOWN: {context}

Read the SOURCE below independently and judge:
- own_words: are these {name}'s own words in the SOURCE (not the author/interviewer)?
- in_context: does the SOURCE support this meaning (not cut to distort)?
- primary: is the SOURCE {name}'s own venue or an outlet's own interview with them
  (true), versus reporting on a separate event (false)?
- issue: the single lowercase topic label YOU think this quote answers.
Return JSON: {{"own_words","in_context","primary","issue","notes"}}.

SOURCE:
{text}
"""


def build_crosscheck_prompt(cand, source_text, candidate_name) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, quote=cand.text,
                                context=cand.context, text=source_text[:60000])


def parse_crosscheck(raw: str) -> CrossCheckVerdict:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return CrossCheckVerdict(False, False, False, False, None, "unparseable")
    return CrossCheckVerdict(
        own_words=bool(d.get("own_words")), in_context=bool(d.get("in_context")),
        primary=bool(d.get("primary")), tag_agree=False,
        issue=(d.get("issue") or None), notes=(d.get("notes") or ""))


def crosscheck(cand, source_text, *, candidate_name, provider, extractor_issue,
               max_tokens=400) -> CrossCheckVerdict:
    raw = provider.complete(
        build_crosscheck_prompt(cand, source_text, candidate_name),
        max_tokens=max_tokens, temperature=0.0, system=_SYSTEM)
    v = parse_crosscheck(raw)
    v.tag_agree = bool(v.issue and extractor_issue
                       and v.issue.strip().lower() == extractor_issue.strip().lower())
    return v

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
PROPOSED ISSUE TAG: {issue}

Read the SOURCE below independently and judge:
- own_words: are these {name}'s own words in the SOURCE (not the author/interviewer)?
- in_context: does the SOURCE support this meaning (not cut to distort)?
- primary: is the SOURCE {name}'s own venue or an outlet's own interview with them
  (true), versus reporting on a separate event (false)?
- tag_ok: is "{issue}" a DEFENSIBLE issue tag for this quote? Judge the substance,
  not the exact wording — true if the quote is genuinely about that issue (a
  broader or narrower label for the same subject still counts as defensible);
  false only if the tag is off-topic or misleading.
- issue: OPTIONAL — a label you'd suggest instead, only if you think a clearly
  better one exists. This is a note, not part of the tag_ok judgment.
Return JSON: {{"own_words","in_context","primary","tag_ok","issue","notes"}}.

SOURCE:
{text}
"""


def build_crosscheck_prompt(cand, source_text, candidate_name) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, quote=cand.text,
                                context=cand.context, issue=cand.issue,
                                text=source_text[:60000])


def parse_crosscheck(raw: str) -> CrossCheckVerdict:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return CrossCheckVerdict(False, False, False, False, None, "unparseable")
    if not isinstance(d, dict):
        return CrossCheckVerdict(False, False, False, False, None, "unparseable")
    iss = d.get("issue")
    issue = iss.strip() if isinstance(iss, str) and iss.strip() else None
    return CrossCheckVerdict(
        own_words=bool(d.get("own_words")), in_context=bool(d.get("in_context")),
        primary=bool(d.get("primary")), tag_agree=bool(d.get("tag_ok")),
        issue=issue, notes=(d.get("notes") if isinstance(d.get("notes"), str) else ""))


def crosscheck(cand, source_text, *, candidate_name, provider,
               max_tokens=400) -> CrossCheckVerdict:
    raw = provider.complete(
        build_crosscheck_prompt(cand, source_text, candidate_name),
        max_tokens=max_tokens, temperature=0.0, system=_SYSTEM)
    return parse_crosscheck(raw)

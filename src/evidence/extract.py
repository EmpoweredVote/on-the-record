from __future__ import annotations
import json
import re
from .models import QuoteCandidate

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)

_SYSTEM = ("You extract a politician's own VERBATIM sentences that state a view "
           "on an issue. Never paraphrase. Respond with ONLY the requested JSON.")

_INSTRUCTIONS = """From the SOURCE below, extract sentences spoken or written by {name}
that state a forward-looking view on a policy issue. Rules:
- VERBATIM only — copy the exact words from the SOURCE; never summarize or reword.
- One claim per quote. Trim only filler; mark substantive cuts with … .
- issue = a short lowercase topic label (e.g. "housing", "homelessness", "policing").
- is_own_words: true only if these are {name}'s own words (not the author's or an
  interviewer's).
- is_primary_venue: true if the SOURCE is {name}'s own venue (their site/official
  page/op-ed) or an outlet's OWN interview/Q&A with them; false if the SOURCE is
  reporting on a separate event where {name} spoke.
- If is_primary_venue is false and the SOURCE names a spoken event ({name} said X at
  a debate/town-hall/interview/podcast), set reported_event to "<event>, <date>" and,
  if the SOURCE links the primary (e.g. a YouTube URL), set primary_handle to it.
Return JSON: {{"quotes": [{{"text","context","issue","date","setting",
"is_own_words","is_primary_venue","reported_event","primary_handle"}}]}}.
context = the surrounding passage from the SOURCE (enough to vet the quote).

SOURCE:
{text}
"""


def build_extract_prompt(text: str, candidate_name: str) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, text=text[:60000])


def parse_extract(raw: str) -> list:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out = []
    for q in data.get("quotes", []):
        if not (q.get("text") or "").strip():
            continue
        out.append(QuoteCandidate(
            text=q["text"].strip(), context=(q.get("context") or "").strip(),
            issue=(q.get("issue") or "").strip().lower(), date=q.get("date"),
            setting=q.get("setting"), is_own_words=bool(q.get("is_own_words", True)),
            is_primary_venue=bool(q.get("is_primary_venue", True)),
            reported_event=q.get("reported_event"),
            primary_handle=q.get("primary_handle")))
    return out


def extract_quotes(text, *, candidate_name, provider, max_tokens=1500) -> list:
    raw = provider.complete(build_extract_prompt(text, candidate_name),
                            max_tokens=max_tokens, temperature=0.0, system=_SYSTEM)
    return parse_extract(raw)

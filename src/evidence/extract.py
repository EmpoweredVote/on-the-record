from __future__ import annotations
import json
import re
from .models import QuoteCandidate


def _split_point(text: str, target: int, floor: int) -> int:
    """Index just after a natural boundary at or before `target` but not before
    `floor`; falls back to `target` when none is found (so a run with no
    boundary still splits)."""
    for sep in ("\n\n", "\n", ". ", " "):
        i = text.rfind(sep, floor, target)
        if i != -1:
            return i + len(sep)
    return target


def chunk_text(text: str, size: int = 12000, overlap: int = 2000) -> list:
    """Split `text` into overlapping windows of about `size` chars, preferring
    to cut on a paragraph/sentence/space boundary. Overlap keeps a quote that
    straddles a cut whole in an adjacent window. Empty text -> no windows."""
    text = text or ""
    if len(text) <= size:
        return [text] if text else []
    windows, start, n = [], 0, len(text)
    while start < n:
        target = min(start + size, n)
        end = target if target >= n else _split_point(text, target, start + size // 2)
        windows.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return windows


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)

_SYSTEM = ("You extract a politician's own VERBATIM sentences that state a view "
           "on an issue. Never paraphrase. Respond with ONLY the requested JSON.")

_INSTRUCTIONS = """From the SOURCE below, extract sentences spoken or written by {name}
that state a forward-looking view on a policy issue. Rules:
- VERBATIM only — copy the exact words from the SOURCE; never summarize or reword.
- Capture the candidate's COMPLETE stance on ONE issue as a coherent, CONTIGUOUS
  passage — usually 1 to 3 sentences. When the candidate states HOW they would act
  (a specific policy mechanism or lever: e.g. build shelters, enforce encampment
  laws, expand services, triple housing construction) in sentences ADJACENT to the
  goal, INCLUDE those sentences in `text`. Do NOT reduce the quote to the bare goal
  and leave the mechanism behind in the surrounding text.
- Keep `text` VERBATIM and CONTIGUOUS — one unbroken run of the SOURCE, or adjacent
  sentences from it; never stitch together non-adjacent passages, and keep ONE
  stance per quote (do not merge unrelated claims). Trim only filler; mark a
  substantive internal cut with … .
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


def _iter_json_objects(payload: str):
    """Yield each complete top-level object inside the `quotes` array, stopping
    at the first incomplete one. Lets a truncated reply still surrender the
    quotes it did finish."""
    key = payload.find('"quotes"')
    lb = payload.find("[", key) if key != -1 else payload.find("[")
    if lb == -1:
        return
    dec = json.JSONDecoder()
    i, n = lb + 1, len(payload)
    while i < n:
        j = payload.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(payload, j)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            yield obj
        i = end


def _to_candidate(q):
    if not isinstance(q, dict):
        return None
    text = q.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    ctx = q.get("context"); iss = q.get("issue")
    return QuoteCandidate(
        text=text.strip(),
        context=ctx.strip() if isinstance(ctx, str) else "",
        issue=iss.strip().lower() if isinstance(iss, str) else "",
        date=q.get("date"), setting=q.get("setting"),
        is_own_words=bool(q.get("is_own_words", True)),
        is_primary_venue=bool(q.get("is_primary_venue", True)),
        reported_event=q.get("reported_event"),
        primary_handle=q.get("primary_handle"))


def parse_extract(raw: str) -> list:
    m = _FENCE.search(raw or "")
    payload = m.group(1) if m else (raw or "")
    try:
        data = json.loads(payload)
        items = data.get("quotes", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        items = list(_iter_json_objects(payload))  # salvage a truncated reply
    out = []
    for q in items:
        c = _to_candidate(q)
        if c is not None:
            out.append(c)
    return out


def extract_quotes(text, *, candidate_name, provider, max_tokens=1500) -> list:
    raw = provider.complete(build_extract_prompt(text, candidate_name),
                            max_tokens=max_tokens, temperature=0.0, system=_SYSTEM)
    return parse_extract(raw)

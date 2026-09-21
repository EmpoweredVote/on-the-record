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

_SYSTEM = ("You extract a politician's OWN verbatim words — first person, or sentences "
           "directly quoted from them — that state a view on an issue. Never paraphrase, "
           "reword, or trim. Respond with ONLY the requested JSON.")

_INSTRUCTIONS = """From the SOURCE below, extract statements by {name} that express a view on a
policy issue. Rules:
- VERBATIM only — copy the exact words from the SOURCE; never summarize, reword, or paraphrase.
- OWN WORDS ONLY. Extract a statement only if it is in {name}'s own voice — FIRST PERSON
  (I, we, my, our, us) — OR a sentence directly quoted from {name} (in quotation marks, or
  attributed like "…," {name} said). A THIRD-PERSON description of {name} ("{name} will…",
  "the Mayor has…", "she believes…") is NOT {name}'s words: set is_own_words false for it.
- If the SOURCE is written about {name} in the third person but DIRECTLY QUOTES {name}, extract
  the QUOTED sentence(s) as `text` (that is own words) — not the surrounding paraphrase.
- Capture the FULL coherent CONTIGUOUS passage for ONE stance. Where {name} states HOW they would
  act (the mechanism/lever: e.g. build shelters, enforce encampment laws, expand services), include
  the adjacent sentences that carry it. Capture as many sentences as the complete stance takes.
  Do NOT trim, shorten, cut, abbreviate, or add "…" — copy the unbroken run of the SOURCE as-is.
  (Condensing to the essence is a separate later step; here, capture faithfully and in full.)
- Keep `text` CONTIGUOUS — one unbroken run of the SOURCE; never stitch together non-adjacent
  passages, and keep ONE stance per quote. A passage that covers two distinct stances becomes TWO
  separate quotes.
- issue = a short lowercase topic label (e.g. "housing", "homelessness", "policing").
- is_own_words: apply the OWN WORDS rule above.
- is_primary_venue: true if the SOURCE is {name}'s own venue (their site/official page/op-ed) or an
  outlet's OWN interview/Q&A with them; false if the SOURCE is reporting on a separate event where
  {name} spoke.
- If is_primary_venue is false and the SOURCE names a spoken event ({name} said X at a
  debate/town-hall/interview/podcast), set reported_event to "<event>, <date>" and, if the SOURCE
  links the primary (e.g. a YouTube URL), set primary_handle to it.
Return JSON: {{"quotes": [{{"text","context","issue","date","setting","is_own_words",
"is_primary_venue","reported_event","primary_handle"}}]}}.
context = the surrounding paragraph(s) from the SOURCE — enough that a reader can see the full
setting of the quote and vet it.

SOURCE:
{text}
"""


def build_extract_prompt(text: str, candidate_name: str) -> str:
    return _INSTRUCTIONS.format(name=candidate_name, text=text)


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


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _dedup(cands: list) -> list:
    seen, out = set(), []
    for c in cands:
        k = _norm(c.text)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def extract_quotes(text, *, candidate_name, provider, max_tokens=3000,
                   chunk_size=12000, overlap=2000) -> list:
    cands = []
    for window in chunk_text(text, chunk_size, overlap):
        raw = provider.complete(build_extract_prompt(window, candidate_name),
                                max_tokens=max_tokens, temperature=0.0,
                                system=_SYSTEM)
        cands.extend(parse_extract(raw))
    return _dedup(cands)

"""LLM judge: scores one engine's found-sources list against a race's ground truth.

SPIKE code -- part of a throwaway bakeoff harness. For each race the judge is
given the race's ground-truth `sources` and one engine's `found` list (same
shape, minus tier -- the engine never saw tier or ground truth).

The judge gives EXACTLY ONE verdict per FOUND item, so recall and precision can
never contradict (the earlier two-list design let a find be "matched" for recall
and "wrong" for precision at the same time):

  - "match"        -- a real, current-cycle, neutral, comparable common-question
                      source that covers one of the GROUND TRUTH sources
                      (give matched_gt_url). Counts for BOTH recall and precision.
  - "valid_extra"  -- a real, current, neutral, comparable source that does NOT
                      correspond to any ground-truth source (ground truth may be
                      incomplete). Counts for precision only.
  - "stale"        -- prior-cycle content presented as current.
  - "advocacy"     -- host is not actually neutral (partisan/single-interest).
  - "hallucinated" -- no such source appears to exist / fabricated.
  - "not_comparable" -- a real page but not a same-questions comparable source
                      (hub/pointer/directory, or background reporting, no Q&A).

judge_engine() computes recall/precision/trap-counts itself from the verdicts,
not from any LLM arithmetic.

--dry-run replaces the LLM call with a deterministic heuristic (_mock_judge)
that reads the planted "[dry-run fixture] ... <reason>" markers fixtures.py
writes into `notes`, so the scoring math runs with no network and no cost. It is
NOT a stand-in for real judgment quality.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO_ROOT = Path("/Users/chrisandrews/Documents/GitHub/on-the-record")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import INJECTION_SAFETY_RULES, round_num  # noqa: E402

MODEL_KEY = "haiku-or"

TRAP_REASONS = ("stale", "advocacy", "hallucinated")
CORRECT_VERDICTS = ("match", "valid_extra")

JUDGE_SYSTEM_PROMPT = (
    "You are an impartial judge for an election-source-discovery benchmark. You "
    "will be given (1) GROUND TRUTH: a hand-verified list of comparable "
    "common-question sources for a race (debates/forums, interviews, "
    "questionnaires/guides/voter pamphlets), and (2) FOUND: a list an automated "
    "engine reported finding for the same race, WITHOUT ever seeing the ground "
    "truth.\n\n"
    "Give EXACTLY ONE verdict for EACH item in FOUND (never list an item twice):\n"
    '  "match"        - a real, CURRENT-cycle, neutral, comparable common-question '
    "source that is the SAME SOURCE as one of the GROUND TRUTH items (an archived "
    "copy, a mirror, or an article about the exact same debate/forum/event still "
    'counts). Give its matched_gt_url (a GROUND TRUTH url).\n'
    '  "valid_extra"  - a real, current, neutral, comparable common-question '
    "source that does NOT correspond to any ground-truth item (ground truth may "
    "be incomplete). No matched_gt_url.\n"
    '  "stale"        - prior-cycle content presented as current.\n'
    '  "advocacy"     - the host is not actually neutral (partisan/single-interest).\n'
    '  "hallucinated" - no such source appears to exist / fabricated.\n'
    '  "not_comparable" - a real page but not a same-questions comparable source '
    "(a hub/pointer/directory, or background reporting with no direct candidate Q&A).\n\n"
    "A found item that genuinely covers a ground-truth source must be \"match\" "
    "(or \"stale\" only if it is genuinely the wrong-cycle version of it) -- never "
    "\"not_comparable\".\n\n"
    + INJECTION_SAFETY_RULES
    + "\n\n"
    'Respond with ONLY this JSON object: {"verdicts": [{"found_url": "...", '
    '"verdict": "match|valid_extra|stale|advocacy|hallucinated|not_comparable", '
    '"matched_gt_url": "<ground-truth url or null>"}]}'
)


class _RealJudgeLLM:
    def __init__(self, model_key: str):
        from src.llm_providers import get_provider

        self._provider = get_provider(model_key)

    def judge(self, gt_sources: list[dict], found: list) -> list[dict]:
        import json

        prompt = (
            f"GROUND TRUTH sources ({len(gt_sources)}):\n{json.dumps(gt_sources, indent=2)}\n\n"
            f"FOUND sources ({len(found)}):\n{json.dumps(found, indent=2)}\n\n"
            "Return the JSON object now."
        )
        raw = self._provider.complete(prompt, max_tokens=1500, temperature=0.0, system=JUDGE_SYSTEM_PROMPT)
        obj = _extract_json_object(raw)
        verdicts = obj.get("verdicts", []) if isinstance(obj, dict) else []
        return verdicts if isinstance(verdicts, list) else []


def _extract_json_object(text: str) -> dict:
    import json
    import re

    if not text:
        return {}
    text = text.strip()
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        try:
            val = json.loads(fence.group(1).strip())
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            val = json.loads(text[start : end + 1])
            if isinstance(val, dict):
                return val
        except json.JSONDecodeError:
            pass
    return {}


def _mock_judge(gt_sources: list[dict], found: list) -> list[dict]:
    """Deterministic stand-in for --dry-run. One verdict per found item, reading
    the URL-identity + planted "[dry-run fixture] ... <reason>" markers."""
    gt_urls = {s["url"] for s in gt_sources}
    verdicts: list[dict] = []
    for f in found:
        if not isinstance(f, dict):
            continue
        url = f.get("url", "")
        notes = (f.get("notes") or "").lower()
        probe = "[dry-run fixture]" in notes
        if url in gt_urls:
            if probe and "stale" in notes:
                verdicts.append({"found_url": url, "verdict": "stale", "matched_gt_url": None})
            elif probe and "advocacy" in notes:
                verdicts.append({"found_url": url, "verdict": "advocacy", "matched_gt_url": None})
            else:
                verdicts.append({"found_url": url, "verdict": "match", "matched_gt_url": url})
        else:
            if probe and "hallucination" in notes:
                verdicts.append({"found_url": url, "verdict": "hallucinated", "matched_gt_url": None})
            else:
                verdicts.append({"found_url": url, "verdict": "not_comparable", "matched_gt_url": None})
    return verdicts


def judge_engine(race: dict, found: list, dry_run: bool = False) -> dict:
    """Score one engine's `found` list against `race`'s ground-truth sources."""
    gt_sources = race["sources"]
    gt_urls = {s["url"] for s in gt_sources}

    if dry_run:
        verdicts = _mock_judge(gt_sources, found)
    else:
        verdicts = _RealJudgeLLM(MODEL_KEY).judge(gt_sources, found)

    found_count = len([f for f in found if isinstance(f, dict)])
    matched_gt: set[str] = set()
    correct_count = 0
    trap_counts = {r: 0 for r in TRAP_REASONS}
    not_comparable_count = 0

    for v in verdicts:
        if not isinstance(v, dict):
            continue
        verdict = v.get("verdict")
        if verdict == "match":
            correct_count += 1
            g = v.get("matched_gt_url")
            if g in gt_urls:
                matched_gt.add(g)
        elif verdict == "valid_extra":
            correct_count += 1
        elif verdict in trap_counts:
            trap_counts[verdict] += 1
        else:  # not_comparable / unknown
            not_comparable_count += 1

    known = len(gt_sources)
    recall = (len(matched_gt) / known) if known else None
    precision = (correct_count / found_count) if found_count else 0.0

    return {
        "known": known,
        "found_count": found_count,
        "matched_count": len(matched_gt),
        "correct_count": correct_count,
        "recall": round_num(recall, 3),
        "precision": round_num(precision, 3),
        "matched_gt_urls": sorted(matched_gt),
        "verdicts": verdicts,
        "trap_counts": trap_counts,
        "not_comparable_count": not_comparable_count,
    }

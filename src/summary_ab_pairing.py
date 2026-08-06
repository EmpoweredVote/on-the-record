"""Pure pairing/randomization/answer-key logic for the synthesis-stage BLIND
A/B eval (scripts/generate_summary_ab.py). No filesystem, no network, no
wall-clock — every random draw is seeded from a hash of the meeting id, so
re-running the generator (even with a different set of candidate models, on a
different day) reproduces the same Option 1 / Option 2 assignment for a given
(meeting id, model, pair key).
"""
from __future__ import annotations

import hashlib
import random


def meeting_seed(meeting_id: str) -> int:
    """A stable integer seed derived from meeting_id.

    NOT Python's built-in hash() — that's randomized per-process
    (PYTHONHASHSEED) for strings, so it would silently break reproducibility
    across runs/machines. Never time- or random-based.
    """
    digest = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def meeting_rng(meeting_id: str) -> random.Random:
    """A random.Random seeded deterministically from meeting_id.

    Callers should build one fresh instance per (meeting_id, model) — not
    share one Random across multiple candidate models for the same meeting —
    so that adding or removing a model from a run never shifts another
    model's already-assigned pair ordering. Draws must be made in a fixed,
    stable order (e.g. always executive_summary first) for reproducibility.
    """
    return random.Random(meeting_seed(meeting_id))


def build_pair(
    meeting_id: str,
    pair_key: str,
    candidate_model: str,
    candidate_text: str,
    reference_text: str,
    rng: random.Random,
) -> dict:
    """One blind A/B pair. Advances `rng` by exactly one draw.

    Returns a dict split into two parts:
      - "reviewer": option_1_text / option_2_text ONLY — no model name or
        candidate/reference label anywhere, preserving blindness.
      - "answer": which option was which, for the withheld answer key.
    """
    candidate_is_option_1 = rng.random() < 0.5
    if candidate_is_option_1:
        option_1_text, option_2_text = candidate_text, reference_text
    else:
        option_1_text, option_2_text = reference_text, candidate_text
    return {
        "meeting_id": meeting_id,
        "pair_key": pair_key,
        "reviewer": {"option_1_text": option_1_text, "option_2_text": option_2_text},
        "answer": {
            "meeting_id": meeting_id,
            "pair_key": pair_key,
            "candidate_model": candidate_model,
            "option_1_is": "candidate" if candidate_is_option_1 else "reference",
            "option_2_is": "reference" if candidate_is_option_1 else "candidate",
        },
    }


def build_answer_key(pairs: list) -> dict:
    """{meeting_id: {pair_key: answer_dict}} from a list of build_pair() outputs."""
    key: dict = {}
    for p in pairs:
        key.setdefault(p["meeting_id"], {})[p["pair_key"]] = p["answer"]
    return key


def render_pair_markdown(pair: dict, title: str) -> str:
    """Render ONE pair as a reviewer-facing markdown block: a title, then
    Option 1 / Option 2 text only. No model name or candidate/reference label
    is ever included — that's the whole point of the blind pairing.
    """
    lines = [
        f"### {title}",
        "",
        "**Option 1:**",
        "",
        pair["reviewer"]["option_1_text"],
        "",
        "**Option 2:**",
        "",
        pair["reviewer"]["option_2_text"],
        "",
    ]
    return "\n".join(lines)

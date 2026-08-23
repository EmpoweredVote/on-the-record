"""Pure pairing/randomization/answer-key logic for the synthesis-stage BLIND
A/B eval (scripts/generate_summary_ab.py). No filesystem, no network, no
wall-clock — every random draw is seeded from a hash of (meeting id, model),
so re-running the generator (even on a different day, even with a different
set of OTHER candidate models in the run) reproduces the same Option 1 /
Option 2 assignment for a given (meeting id, model, pair key).
"""
from __future__ import annotations

import hashlib
import random


def pair_seed(meeting_id: str, model: str) -> int:
    """A stable integer seed derived from (meeting_id, model).

    Seeding on meeting_id alone would put the IDENTICAL reference text in the
    same option slot for every candidate model compared against that meeting
    — a de-blinding pattern a reviewer could learn across a multi-model run.
    Including `model` in the digest gives each (meeting, model) comparison an
    independent draw sequence.

    NOT Python's built-in hash() — that's randomized per-process
    (PYTHONHASHSEED) for strings, so it would silently break reproducibility
    across runs/machines. Never time- or random-based.
    """
    digest = hashlib.sha256(f"{meeting_id}::{model}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def pair_rng(meeting_id: str, model: str) -> random.Random:
    """A random.Random seeded deterministically from (meeting_id, model).

    Callers must build one fresh instance per (meeting_id, model) — never
    share one Random across multiple candidate models for the same meeting —
    so that adding or removing a model from a run never shifts another
    model's already-assigned pair ordering. Draws must be made in a fixed,
    stable order (e.g. always executive_summary first) for reproducibility.
    """
    return random.Random(pair_seed(meeting_id, model))


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
    A "visible_id" key (a judge-facing handle like "pair-7") is added later,
    by assign_visible_ids() — build_pair() itself doesn't know about it.
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


def assign_visible_ids(pairs: list, start: int = 1, prefix: str = "pair") -> list:
    """Attach a sequential, model-blind "visible_id" (e.g. "pair-7") to each
    pair dict, in list order. This is the judge-facing handle rendered into
    ab_pairs.md and referenced in the judging instructions ("pair-7: Option
    1") — it carries no information about which meeting or model produced
    the pair.

    Returns NEW dicts (does not mutate the input). `start` lets callers keep
    numbering contiguous across multiple calls (e.g. one call per
    meeting/model comparison, continuing from where the last one left off).
    """
    out = []
    for i, pair in enumerate(pairs):
        tagged = dict(pair)
        tagged["visible_id"] = f"{prefix}-{start + i}"
        out.append(tagged)
    return out


def build_answer_key(pairs: list) -> dict:
    """{meeting_id: {pair_key: answer_dict}} from a list of build_pair() (or
    assign_visible_ids()-tagged) outputs. When a pair carries a "visible_id"
    (set by assign_visible_ids), it's copied into the answer_dict too."""
    key: dict = {}
    for p in pairs:
        answer = dict(p["answer"])
        if "visible_id" in p:
            answer["visible_id"] = p["visible_id"]
        key.setdefault(p["meeting_id"], {})[p["pair_key"]] = answer
    return key


def build_visible_id_index(pairs: list) -> dict:
    """{visible_id: answer_dict} — the flat lookup a blind reviewer's recorded
    pick (e.g. "pair-7: Option 1") resolves through, without needing to know
    the internal meeting_id/pair_key. Pairs without a "visible_id" are
    skipped (assign_visible_ids() must run first)."""
    out: dict = {}
    for p in pairs:
        vid = p.get("visible_id")
        if vid is None:
            continue
        answer = dict(p["answer"])
        answer["visible_id"] = vid
        out[vid] = answer
    return out


def render_pair_markdown(pair: dict, title: str, visible_id: "str | None" = None) -> str:
    """Render ONE pair as a reviewer-facing markdown block: a title (with the
    visible pair id, when given), then Option 1 / Option 2 text only. No
    model name or candidate/reference label is ever included — that's the
    whole point of the blind pairing.
    """
    header = f"### {title}" if not visible_id else f"### {title} — {visible_id}"
    lines = [
        header,
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

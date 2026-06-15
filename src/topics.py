"""Stage 6: Topic classification — tag substantive summary sections with
Compass issue topic_keys (AI-predicted).

Vocabulary is the live set of inform.compass_topics, fetched at publish time
so it tracks Compass (including rewrites). One Haiku call per meeting maps each
substantive section to 0..N topic_keys drawn ONLY from that vocabulary; the
model's choices are validated against the vocab before use.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from . import config
from .models import SectionTopic, SummarySection


def fetch_live_topics(conn) -> list[dict]:
    """Read live Compass topics (the classification vocabulary) via psycopg2.

    Returns dicts with topic_key, short_title, question_text.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT topic_key, short_title, question_text
            FROM inform.compass_topics
            WHERE is_live = true
            ORDER BY topic_key
            """
        )
        rows = cur.fetchall()
    return [
        {"topic_key": r[0], "short_title": r[1], "question_text": r[2]}
        for r in rows
    ]


def substantive_sections(
    sections: list[SummarySection],
) -> list[tuple[int, SummarySection]]:
    """Return (original_index, section) for substantive section types only."""
    return [
        (i, s)
        for i, s in enumerate(sections)
        if s.section_type in config.SUBSTANTIVE_SECTION_TYPES
    ]


def validate_topic_keys(keys: list[str], vocab: set[str]) -> list[str]:
    """Keep only in-vocabulary keys, deduped, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k in vocab and k not in seen:
            seen.add(k)
            out.append(k)
    return out


_CLASSIFY_SYSTEM = """You tag city council meeting discussion sections with the political issues they're about.

You are given a fixed list of allowed topics (each: a key, a short title, and the question it represents) and a set of meeting sections. For each section, choose the topic keys that the discussion is genuinely about — zero, one, or several. Only use keys from the allowed list. Many routine items (contract renewals, procedural votes) match no topic; return an empty list for those rather than forcing a match.

Respond with ONLY valid JSON:
{
  "sections": [
    {"section_index": 1, "topic_keys": ["housing"]},
    {"section_index": 3, "topic_keys": []}
  ]
}"""


def build_classification_prompt(
    sections: list[tuple[int, SummarySection]],
    vocab: list[dict],
) -> str:
    """Build the user prompt: allowed topics + the sections to classify."""
    topic_lines = [
        f"- {t['topic_key']}: {t.get('short_title') or ''} — {t.get('question_text') or ''}".rstrip(" —")
        for t in vocab
    ]
    sec_lines = []
    for idx, sec in sections:
        body = (sec.content or "")[:600]
        sec_lines.append(f"section {idx} — \"{sec.title}\":\n{body}")
    return (
        "ALLOWED TOPICS:\n"
        + "\n".join(topic_lines)
        + "\n\nSECTIONS TO TAG:\n"
        + "\n\n".join(sec_lines)
    )


def classify_sections(
    client,
    sections: list[SummarySection],
    vocab: list[dict],
) -> list[SectionTopic]:
    """Classify substantive sections into topic_keys. Returns one SectionTopic
    per substantive section (topic_keys possibly empty)."""
    subs = substantive_sections(sections)
    if not subs:
        return []

    vocab_keys = {t["topic_key"] for t in vocab}
    prompt = build_classification_prompt(subs, vocab)

    message = client.messages.create(
        model=config.TOPIC_CLASSIFY_MODEL,
        max_tokens=2048,
        system=_CLASSIFY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    match = re.search(r"\{[\s\S]*\}", text)
    parsed = {}
    if match:
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            parsed = {}

    by_index = {
        item.get("section_index"): item.get("topic_keys", [])
        for item in parsed.get("sections", [])
        if isinstance(item, dict)
    }

    result = []
    for idx, _sec in subs:
        raw = by_index.get(idx, [])
        keys = validate_topic_keys(raw if isinstance(raw, list) else [], vocab_keys)
        result.append(SectionTopic(section_index=idx, topic_keys=keys))
    return result

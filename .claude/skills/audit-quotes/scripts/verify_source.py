"""Source verification: does a quote actually appear in its cited source?

Pure, testable core (normalize / candidate_phrases / parse_youtube / phrase_found) plus a thin
DB-backed wrapper (check_source / run_source_checks) that reads meetings.meetings /
meetings.segments. Read-only.
"""
import re
from scripts.models import Finding


def normalize(text):
    """Lowercase; drop bracketed insertions and ellipses; keep only alnum + single spaces."""
    if not text:
        return ""
    text = text.replace("…", " ").replace("...", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)          # drop [bracketed] insertions
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def candidate_phrases(quote_text, n=6, k=3):
    """Up to k contiguous n-word windows from the INTERIOR of the normalized quote
    (interior avoids the most-edited first/last words). Falls back gracefully for short quotes."""
    words = normalize(quote_text).split()
    if len(words) < n:
        return [" ".join(words)] if words else []
    interior = words[2:-2] if len(words) > n + 4 else words
    if len(interior) < n:
        interior = words
    step = max(1, (len(interior) - n) // max(1, k))
    out = []
    for i in range(0, len(interior) - n + 1, step):
        out.append(" ".join(interior[i:i + n]))
        if len(out) >= k:
            break
    return out or [" ".join(words[:n])]


def parse_youtube(url):
    """(video_id, t_seconds|None) from a YouTube url, else (None, None)."""
    if not url:
        return (None, None)
    m = re.search(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", url)
    t = re.search(r"[?&]t=(\d+)", url)
    return (m.group(1) if m else None, int(t.group(1)) if t else None)


def phrase_found(phrases, segment_texts):
    """True if any candidate phrase appears in the normalized concatenation of segment_texts."""
    hay = " ".join(normalize(s) for s in segment_texts)
    return any(p and p in hay for p in phrases)


def _majority_threshold(n):
    """Quorum for 'the quote is really in this transcript': more than half the candidate
    phrases, not just one. A single 6-word window can coincide by chance (common phrasing,
    verbal tics repeated by the same speaker elsewhere) — requiring a majority of the
    independently-drawn interior windows to land is what actually distinguishes a real
    match from a coincidental one. Verified against real data: correctly-sourced quotes
    matched 2/3 or 3/3 of their phrases; a confirmed mis-sourced quote matched only 1/3."""
    return max(1, -(-n // 2))  # ceil(n/2)


def check_source(conn, row):
    """Return a Finding if the quote can't be verified against its cited source, else None.
    row: dict with id, candidate, topic_key, race_id, quote_text, source_url."""
    base = dict(level="quote", quote_id=row["id"], topic_key=row["topic_key"],
                race_id=row["race_id"], candidate=row["candidate"], fix_class="decision-required")
    vid, t = parse_youtube(row.get("source_url"))
    if not vid:
        return None  # non-video/written source — not transcript-verifiable here (source-tier check covers those)
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id FROM meetings.meetings WHERE video_url=%s LIMIT 1", (vid,))
        m = cur.fetchone()
        if not m:
            return Finding(check_id="source-not-ingested", principle="source must be verifiable",
                           severity="medium", what=f"Cited video {vid} is not ingested; quote can't be auto-verified.",
                           suggested_fix="Verify the quote against the source manually, or ingest the source.", **base)
        cur.execute("SELECT text, speaker_name, start_time FROM meetings.segments WHERE meeting_id=%s", (m["id"],))
        segs = cur.fetchall()
    phrases = candidate_phrases(row.get("quote_text"))
    hay = " ".join(normalize(s["text"]) for s in segs)
    matched = [p for p in phrases if p and p in hay]
    if len(matched) < _majority_threshold(len(phrases)):
        return Finding(check_id="source-unverified", principle="quote must appear in its cited source",
                       severity="high", what="No distinctive phrase from the quote appears in the cited video's transcript — likely mis-sourced.",
                       suggested_fix="Find the true source (search other transcripts) and correct source_url, or remove the quote.", **base)
    hits = [s for s in segs if phrase_found(matched, [s["text"]])]
    cand_last = (row.get("candidate") or "").split()[-1].lower()
    if cand_last and not any(cand_last in (h["speaker_name"] or "").lower() for h in hits):
        return Finding(check_id="source-speaker-mismatch", principle="quote must be spoken by the candidate",
                       severity="high", what=f"Quote phrase found in the cited video but not attributed to {row.get('candidate')}.",
                       suggested_fix="Confirm the speaker; the quote may belong to another person.", **base)
    if t is not None:
        nearest = min(abs(h["start_time"] - t) for h in hits)
        if nearest > 180:
            return Finding(check_id="source-timestamp-drift", principle="deep-link should point at the quote",
                           severity="low", what=f"Quote found ~{int(nearest)}s from the cited timestamp {t}s.",
                           suggested_fix="Update the &t= deep-link to the correct moment.", **base)
    return None


def run_source_checks(conn, rows):
    out = []
    for r in rows:
        f = check_source(conn, r)
        if f:
            out.append(f)
    return out

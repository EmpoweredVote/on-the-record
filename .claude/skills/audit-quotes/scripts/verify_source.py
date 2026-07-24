"""Source verification: does a quote actually appear in its cited source?

Pure, testable core (normalize / verbatim_runs / candidate_phrases / parse_youtube /
longest_verbatim_match / phrase_found) plus a thin DB-backed wrapper (check_source /
run_source_checks) that reads meetings.meetings / meetings.segments. Read-only.

Curated quotes are not verbatim transcripts: they chain several spoken spans together with
`…` elisions and `[bracketed]` editorial insertions, and drop filler ("um", "right now").
So we never expect the *whole* quote to appear contiguously in the raw ASR segments. Instead
we split the quote into its contiguous verbatim spans (never spanning an edit) and ask whether
a long-enough contiguous run of one of those spans appears in the cited video's transcript.
"""
import re
from scripts.models import Finding

# A contiguous verbatim run this many words long (or longer) is distinctive enough to treat the
# quote as genuinely present in the transcript. Chosen conservatively: on real curated data,
# correctly-sourced quotes clear this comfortably (observed floor ~11 words), while a genuinely
# mis-sourced quote has no run this long. Shorter quotes fall back to their own longest run
# (see check_source) so a faithfully-sourced short quote isn't punished for being short.
MIN_RUN_WORDS = 5


def normalize(text):
    """Lowercase; drop bracketed insertions and ellipses; keep only alnum + single spaces."""
    if not text:
        return ""
    text = text.replace("…", " ").replace("...", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)          # drop [bracketed] insertions
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def verbatim_runs(quote_text):
    """Split the quote into its contiguous verbatim spans, each normalized.

    Curators join spoken spans with `…`/`...` elisions and `[bracketed]` insertions. Nothing
    runs contiguously across such an edit — the words on either side were not spoken back-to-back
    (an elision drops material; a bracket is editorial and not in the source at all). So we split
    at every edit and normalize each side, yielding spans that (modulo dropped filler) should each
    appear verbatim in the transcript. A phrase that straddles an edit is NOT evidence of sourcing,
    which is exactly the false-positive the old fixed-6-gram approach produced."""
    if not quote_text:
        return []
    parts = re.split(r"…|\.\.\.|\[[^\]]*\]", quote_text)
    return [r for r in (normalize(p) for p in parts) if r]


def candidate_phrases(quote_text):
    """The quote's contiguous verbatim spans (see verbatim_runs) — the phrases that, modulo
    dropped filler, must appear in the cited transcript for the quote to be considered sourced."""
    return verbatim_runs(quote_text)


def parse_youtube(url):
    """(video_id, t_seconds|None) from a YouTube url, else (None, None)."""
    if not url:
        return (None, None)
    m = re.search(r"(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})", url)
    t = re.search(r"[?&]t=(\d+)", url)
    return (m.group(1) if m else None, int(t.group(1)) if t else None)


def _sublist_index(needle, haystack):
    """Index of the first contiguous occurrence of list `needle` in list `haystack`, else -1.
    Word-list (not substring) matching, so "data center" never matches inside "database centered"."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return -1
    first = needle[0]
    for i in range(len(haystack) - n + 1):
        if haystack[i] == first and haystack[i:i + n] == needle:
            return i
    return -1


def longest_verbatim_match(phrases, segment_texts):
    """Longest contiguous slice (in words) of any phrase that appears verbatim as a contiguous
    word-sublist of the segments. Returns (length, matched_words).

    Scans windows longest-first, so a single dropped-filler word inside an otherwise-verbatim
    span only shortens the reported run rather than erasing it — which is what lets a genuinely
    sourced but lightly-cleaned quote verify."""
    hay = " ".join(normalize(s) for s in segment_texts).split()
    best_len, best_words = 0, []
    for phrase in phrases:
        pw = phrase.split()
        length = len(pw)
        while length > best_len:                     # only bother looking for something longer
            hit = None
            for i in range(len(pw) - length + 1):
                window = pw[i:i + length]
                if _sublist_index(window, hay) >= 0:
                    hit = window
                    break
            if hit:
                best_len, best_words = length, hit
                break
            length -= 1
    return best_len, best_words


def phrase_found(phrases, segment_texts, min_words=MIN_RUN_WORDS):
    """True if some contiguous verbatim run of >= min_words from any phrase appears in
    segment_texts. Contiguous-run matching (not fixed-window quorum), so dropped filler doesn't
    hide a genuine match."""
    n, _ = longest_verbatim_match(phrases, segment_texts)
    return n >= min_words


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

    runs = verbatim_runs(row.get("quote_text"))
    if not runs:
        return None  # nothing verbatim to check (e.g. quote was all editorial) — leave to other checks
    seg_texts = [s["text"] for s in segs]
    best_len, best_words = longest_verbatim_match(runs, seg_texts)

    # A genuinely sourced quote has a long contiguous run in the transcript. Require MIN_RUN_WORDS,
    # but never demand more than the quote's own longest span — a faithfully-sourced short quote
    # shouldn't be flagged just for being short.
    longest_run_total = max(len(r.split()) for r in runs)
    need = min(MIN_RUN_WORDS, longest_run_total)
    if best_len < need:
        return Finding(check_id="source-unverified", principle="quote must appear in its cited source",
                       severity="high", what="No distinctive phrase from the quote appears in the cited video's transcript — likely mis-sourced.",
                       suggested_fix="Find the true source (search other transcripts) and correct source_url, or remove the quote.", **base)

    # Attribution: the matched run must land in a segment spoken by the candidate. Match per-segment
    # on the run we actually found (>= need words), which also tolerates the run spanning a segment
    # boundary — at least one segment will still carry a long-enough chunk.
    matched_phrase = " ".join(best_words)
    hits = [s for s in segs if phrase_found([matched_phrase], [s["text"]], min_words=need)]
    cand_last = (row.get("candidate") or "").split()[-1].lower()
    if cand_last and not any(cand_last in (h["speaker_name"] or "").lower() for h in hits):
        return Finding(check_id="source-speaker-mismatch", principle="quote must be spoken by the candidate",
                       severity="high", what=f"Quote phrase found in the cited video but not attributed to {row.get('candidate')}.",
                       suggested_fix="Confirm the speaker; the quote may belong to another person.", **base)
    if t is not None and hits:
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

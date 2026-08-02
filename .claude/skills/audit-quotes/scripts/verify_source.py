"""Source verification: does a quote actually appear in its cited source?

Pure, testable core (normalize / verbatim_runs / candidate_phrases / parse_youtube /
longest_verbatim_match / phrase_found / extract_page_text / starts_midsentence) plus thin
wrappers (check_source / run_source_checks) over the two kinds of cited source:

- **Video sources** (YouTube) are matched against the ingested transcript in
  meetings.meetings / meetings.segments. Read-only DB access, always on.
- **Written sources** (campaign issue pages, op-eds, news articles) are matched against the
  live page. This costs network I/O, so it is **opt-in**: `check_source` only takes this path
  when the caller passes a `fetch_page` callable (`scripts.audit --verify-written`). Without
  one the written path is a no-op and the audit stays DB-only, as it was before.

Curated quotes are not verbatim transcripts: they chain several spoken spans together with
`…` elisions and `[bracketed]` editorial insertions, and drop filler ("um", "right now").
So we never expect the *whole* quote to appear contiguously in the raw ASR segments. Instead
we split the quote into its contiguous verbatim spans (never spanning an edit) and ask whether
a long-enough contiguous run of one of those spans appears in the cited source. The same
span-matching machinery serves both source kinds.

Two further checks catch excerpts that are *perfectly verbatim* and still misrepresent the
candidate, which is why no amount of string matching against the source can see them:

- `nested_quotation` — the words are ones the candidate *relayed* rather than their own (the
  TN-Governor defect, 2026-08-02: Blackburn quoting what voters say, curated as her pledge).
  High severity; it publishes an opinion the candidate never expressed. Runs on **both** source
  kinds: against the page for written sources, against the candidate's own transcript segments
  for video. Its page-independent signal (`_self_framed`) runs for every quote regardless of
  source kind, even ones we can't fetch or haven't ingested.
- `starts_midsentence` — the excerpt starts mid-sentence and drops the operative clause (the
  WI-02 campaign-finance defect, docs/audits/2026-08-01-quote-audit-wi-house-02.md). Written
  sources only: an ASR transcript has no reliable sentence boundaries to cut against.
"""
import hashlib
import pathlib
import re
import time
from html.parser import HTMLParser

from scripts.checks import AGGREGATOR_SOURCE, QUIZ_SOURCE
from scripts.models import Finding

# A contiguous verbatim run this many words long (or longer) is distinctive enough to treat the
# quote as genuinely present in the transcript. Chosen conservatively: on real curated data,
# correctly-sourced quotes clear this comfortably (observed floor ~11 words), while a genuinely
# mis-sourced quote has no run this long. Shorter quotes fall back to their own longest run
# (see check_source) so a faithfully-sourced short quote isn't punished for being short.
# Written sources reuse the same bar rather than inventing a second, uncalibrated one.
MIN_RUN_WORDS = 5

# A fetched page with fewer words than this was not really read: a JS-rendered SPA shell, a
# consent interstitial, a paywall stub or an error body. Reporting that as `source-unverified`
# would be a false accusation, so it becomes `source-unfetchable` instead.
MIN_PAGE_WORDS = 50

# Pages are cached on disk so a full-sweep audit fetches each cited URL once, not once per
# quote. A week is long enough to cover a multi-day audit and short enough that a re-audit
# months later sees the current page.
CACHE_TTL_SECONDS = 7 * 24 * 3600

# Minimum gap between live requests to the same host, so a sweep over a race whose quotes all
# cite one campaign site doesn't hammer it. Cache hits don't count as requests.
HOST_DELAY_SECONDS = 1.0

FETCH_TIMEOUT_SECONDS = 20
USER_AGENT = ("EmpoweredVote-quote-audit/1.0 "
              "(read-only source verification; contact via empoweredvote.org)")


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


# --- written sources: HTML -> text ---

# Tags whose *content* is not prose. `head` is skipped wholesale (title/meta/style live there).
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
# Tags that end a line of prose. Without a separator here, "…one</p><p>Two…" fuses into the
# phantom phrase "oneTwo" and can manufacture a match that isn't on the page.
_BLOCK_TAGS = {
    "p", "div", "br", "hr", "li", "ul", "ol", "dl", "dt", "dd", "tr", "td", "th", "table",
    "section", "article", "header", "footer", "nav", "aside", "main", "form", "label",
    "blockquote", "pre", "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip_depth = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def _collapse_whitespace(text):
    """Collapse runs of whitespace but KEEP single newlines — a newline marks a block boundary,
    which starts_midsentence reads as a sentence boundary."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def extract_page_text(html):
    """Visible prose from an HTML document, punctuation intact.

    Stdlib-only (html.parser) — the audit venv has no bs4/lxml and this doesn't warrant a new
    dependency. Punctuation must survive because starts_midsentence keys off the character
    immediately before a match; only whitespace and non-prose elements are normalized away.
    Input that contains no markup is passed through as plain text."""
    if not html:
        return ""
    if "<" not in html:
        return _collapse_whitespace(html)
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                                # malformed markup: fall back to tag-stripping
        return _collapse_whitespace(re.sub(r"<[^>]+>", " ", html))
    return _collapse_whitespace("".join(parser.parts))


# --- written sources: clip-boundary check ---

# What can legitimately precede the start of an excerpt. A colon counts: "his position is
# simple: taxes are too high" introduces a quotable clause.
_SENTENCE_BOUNDARY_CHARS = ".!?…:"
# A quotation mark immediately before the match means the SOURCE delimited this as a quotation
# ('…adding that "a government-paying program is…"'). The curator reproduced the source's own
# cut, which is the normal journalistic form and not the defect this check is looking for.
# Double-quote family only — a bare `'` is far more often an apostrophe than a quote open.
_QUOTE_CHARS = "\"“”„«»‹›"
# Opening punctuation between the boundary and the first word ("He said. (We must…").
_OPENERS = " \t'‘’(（[{—–-"


def _tokens_with_offsets(text):
    """(normalized_word, start_char_index) for each word, so a word-list match can be traced
    back to its position in the punctuated original."""
    return [(m.group(0).lower(), m.start()) for m in re.finditer(r"[a-zA-Z0-9]+", text)]


def _locate_run(run_words, page_text):
    """(start_char, end_char) of the first contiguous occurrence of `run_words` in the punctuated
    page text, else None. Word-list matching, so the span lines up with the original characters
    and the callers below can read the punctuation on either side of it."""
    tokens = _tokens_with_offsets(page_text)
    i = _sublist_index(run_words, [w for w, _ in tokens])
    if i < 0:
        return None
    word, off = tokens[i + len(run_words) - 1]
    return (tokens[i][1], off + len(word))


def starts_midsentence(quote_text, page_text):
    """True if the quote's first verbatim run appears on the page but begins mid-sentence,
    with no ellipsis marking the cut.

    This is the WI-02 defect: an excerpt that is perfectly verbatim yet misrepresents the
    candidate because it opens on a trailing subordinate clause and drops the operative one.
    Deliberately conservative — returns False whenever we can't be sure:
      - the curator marked the cut with a leading `…` (the sanctioned form, nothing to flag)
      - the run isn't found on the page at all (that's source-unverified's job, not ours)
      - the run starts the page or a block, or follows `. ! ? … :` (a real sentence start)
      - the source itself opened a quotation right before it (the cut is the source's)"""
    if not quote_text or not page_text:
        return False
    opening = quote_text.lstrip().lstrip("\"'“‘")
    if opening.startswith("…") or opening.startswith("..."):
        return False
    runs = verbatim_runs(quote_text)
    if not runs or not runs[0]:
        return False

    span = _locate_run(runs[0].split(), page_text)
    if span is None:
        return False

    prefix = page_text[:span[0]]
    if not prefix.strip():
        return False                                 # excerpt opens the page
    # Excerpt opens a block. Testing only the trailing whitespace for a newline missed the case
    # where a bullet marker sits between the line break and the text — "…ready to:\n👉 End
    # Inflation…". 7 of the 42 clip findings in the first live sweep were exactly that (👉, 〰️),
    # so anything after the last newline that carries no word character still counts as a start.
    if "\n" in prefix and not re.search(r"\w", prefix.rsplit("\n", 1)[-1]):
        return False
    if prefix.rstrip()[-1:] in _QUOTE_CHARS:
        return False                                 # the source itself opened the quotation here
    core = prefix.rstrip(_OPENERS)
    if not core:
        return False
    return core[-1] not in _SENTENCE_BOUNDARY_CHARS


# --- written sources: nested-quotation / relayed-speech check ---

# Subjects that are plainly NOT the candidate. The candidate's own name and a bare "he/she/they"
# are deliberately absent: `Blackburn said, "I will secure the border"` is ordinary journalism
# reporting the candidate's own words, and must never flag. Only a frame whose speaker is
# demonstrably someone else counts.
_THIRD_PARTY_SUBJECT = (
    r"(?:people|folks|voters|constituents|residents|everyone|everybody|someone|somebody|"
    r"others|critics|opponents?|democrats|republicans|"
    r"the\s+(?:left|right|other\s+side|report|bill|letter|ad|law|memo|study)|"
    r"they|them)"
)
# Verbs of reported speech. "put it" covers "as they put it".
_FRAMING_VERB = (
    r"(?:say|says|said|saying|tell|tells|told|telling|ask|asks|asked|asking|"
    r"argue|argues|argued|claim|claims|claimed|insist|insists|insisted|"
    r"write|writes|wrote|complain|complains|complained|put\s+it)"
)
# Only a closed set of modals/adverbs may sit between subject and verb, so the pattern can't
# wander across an unrelated clause and manufacture a frame.
_FRAME_FILLER = r"(?:\s+(?:will|would|might|often|always|usually|sometimes|all|just|really|keep|kept|still))*"
_FRAME_RE = re.compile(rf"\b{_THIRD_PARTY_SUBJECT}{_FRAME_FILLER}\s+{_FRAMING_VERB}\b", re.I)

# How far back on the page to look for a frame governing the matched text. A frame further away
# than this is probably governing some other clause.
_FRAME_WINDOW_CHARS = 100
# How far into the stored quote a self-frame must appear to count as *the quote's own* framing
# rather than an aside the candidate makes later in their own sentence.
_SELF_FRAME_CHARS = 60

_SINGLE_MARKS = "'‘’"

# Words that are normally written with a leading apostrophe standing in for dropped letters. Each
# one looks exactly like an opening single quote — whitespace before, a letter after — and, never
# being closed, would leave every later match in the text reading as nested. This is not
# hypothetical: a sweep of all 3,272 live quotes produced exactly two nested findings and both
# were this, `'cause` in one transcript and Whisper splitting "o'clock" into "o 'clock" in another.
_ELISIONS = {"cause", "em", "til", "till", "tis", "twas", "round", "bout", "clock", "n", "nother"}


def _opens_quotation(text, i):
    """Does the mark at text[i] open a quotation (rather than close one, or be an apostrophe)?

    `'` and `’` are overwhelmingly apostrophes, so a single mark only opens when it sits after a
    space/quote/bracket AND before a word — "let's" and "workers'" both fail that test. It must
    also be a *letter* after (so "back in the '90s" is not a quotation) and not one of the
    elisions above."""
    c = text[i]
    prev = text[i - 1] if i else " "
    nxt = text[i + 1] if i + 1 < len(text) else " "
    if c in "“„«":
        return True
    if c == '"':
        return not prev.isalnum()
    if c in "'‘":
        if not (prev.isspace() or prev in "\"“„«([{—–"):
            return False
        if nxt in "\"“":
            return True                              # a double quotation opening inside a single
        word = re.match(r"[a-zA-Z]+", text[i + 1:])
        return bool(word) and word.group(0).lower() not in _ELISIONS
    return False


def _closes_quotation(text, i):
    """Does the mark at text[i] close a single quotation? Possessives ("workers' rights") read as
    closes, which can only ever cancel an open and suppress a finding — the safe direction."""
    c = text[i]
    prev = text[i - 1] if i else " "
    nxt = text[i + 1] if i + 1 < len(text) else " "
    return c in "'’" and (prev.isalnum() or prev in ".,!?…") and not nxt.isalnum()


def _quote_depth(prefix):
    """(inside_double, inside_single) for the position at the end of `prefix`.

    Straight `"` is counted by parity; directional marks by open/close. This is how the nested
    case is recognised: `Blackburn said, "People will say, 'Hey, …` leaves us inside both a
    double and a single quotation, and the singly-nested span is someone else's speech.

    Scoped to the current block — the text after the last newline, i.e. one paragraph on a page
    or one segment in a transcript. Quote marks do not reliably balance across a whole document
    (ASR omits them, articles use them decoratively), and without this bound a single misread
    mark would mark everything after it as nested. A quotation that genuinely spans blocks is
    rare, and the framing signal still covers it."""
    prefix = prefix.rsplit("\n", 1)[-1]
    depth_d = prefix.count('"') % 2
    depth_d += prefix.count("“") + prefix.count("„") - prefix.count("”")
    depth_s = 0
    for i, c in enumerate(prefix):
        if c not in _SINGLE_MARKS:
            continue
        if _opens_quotation(prefix, i):
            depth_s += 1
        elif _closes_quotation(prefix, i) and depth_s > 0:
            depth_s -= 1
    return depth_d > 0, depth_s > 0


def _third_party_frame(prefix):
    """The reported-speech frame governing whatever follows `prefix`, or None.

    Requires the frame to be close by and in the same sentence — a `.`/`!`/`?` between the frame
    and our text means that sentence closed and the frame no longer governs. A newline counts as
    a hard break too: on a page it separates blocks, and on a transcript it separates segments,
    where the frame may well belong to a different speaker (a moderator's question) entirely."""
    window = prefix[-_FRAME_WINDOW_CHARS:]
    match = None
    for m in _FRAME_RE.finditer(window):
        match = m
    if match is None:
        return None
    if any(ch in window[match.end():] for ch in ".!?\n"):
        return None
    return " ".join(match.group(0).split())


def _self_framed(quote_text):
    """A frame at the head of the stored quote itself, where the curator kept the framing and the
    relayed words together ("People will say, 'Hey, let's …'").

    An opening quotation mark must follow the frame. Without that requirement this would also
    flag a candidate's own rhetorical setup ("People say we can't fix this. They're wrong."),
    which is genuinely their words and a perfectly good quote."""
    head = (quote_text or "").lstrip().lstrip("\"'“‘")[:_SELF_FRAME_CHARS]
    m = _FRAME_RE.search(head)
    if not m:
        return None
    rest = head[m.end():]
    if not any(_opens_quotation(rest, i) for i in range(len(rest))):
        return None
    return " ".join(m.group(0).split())


def nested_quotation(quote_text, source_text):
    """Reason string if the quote's words are ones the candidate is *relaying* — a voter, an
    opponent, a document — rather than their own position. None if it looks like their words.

    This is the TN-Governor defect (2026-08-02): Blackburn saying `People will say, 'Hey, let's
    … pick up the pace deporting illegal aliens.'` was curated as her own pledge. The text is
    perfectly verbatim, so `source-unverified` cannot see it, and `starts_midsentence` at most
    catches it incidentally at the wrong severity. Three independent signals, any of which is
    enough:

      1. the stored quote carries its own frame plus an inner quotation (`_self_framed`)
      2. the matched text sits inside a quotation nested within another quotation
      3. a third-party frame ("People will say", "they tell me") immediately precedes the match

    `source_text` is a written page or an ASR transcript — the same three signals apply to both,
    but not with the same force. ASR seldom transcribes quotation marks, so on a transcript
    signal 2 rarely fires and signal 3 does the work; on a page both are live. Signal 1 needs no
    source at all, which is why `check_source` runs it up front for every source kind.

    Signals 2 and 3 stay silent when the run isn't in `source_text` — absence is
    `source-unverified`'s business, not this check's."""
    self_frame = _self_framed(quote_text)
    if self_frame:
        return f"the quote itself opens with “{self_frame}” and then quotes someone"

    if not quote_text or not source_text:
        return None
    runs = verbatim_runs(quote_text)
    if not runs or not runs[0]:
        return None
    span = _locate_run(runs[0].split(), source_text)
    if span is None:
        return None

    prefix = source_text[:span[0]]
    inside_double, inside_single = _quote_depth(prefix)
    if inside_single and inside_double:
        return "the matched text sits in a quotation nested inside the candidate's own quotation"
    if inside_single:
        return "the matched text sits inside a nested ‘…’ quotation"

    frame = _third_party_frame(prefix)
    if frame:
        return f"the source introduces the matched text with “{frame}”"
    return None


def _relayed_finding(reason, base):
    """The `source-nested-quotation` finding, shared by the video and written paths."""
    return Finding(check_id="source-nested-quotation",
                   principle="a quote must be the candidate's own words, not words they relay",
                   severity="high",
                   what=f"Quote is verbatim in the cited source but {reason} — these read as words the candidate is relaying (voters, an opponent, a document), not their own position.",
                   suggested_fix="Re-read the passage. If the candidate is quoting someone else, remove the quote; if they endorse the relayed point in their own words nearby, quote that instead.",
                   **base)


# --- written sources: fetching, with an on-disk cache ---

def fetch_url(url):
    """Fetch a URL and return its decoded body, or None on any failure. Network I/O lives here
    and nowhere else, so every other function in this module stays pure and testable."""
    import requests
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT_SECONDS,
                            headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    return resp.text


def cached_page_text(url, cache_dir, fetch):
    """Body for `url`, served from `cache_dir` when fresh. Returns None if the fetch failed.

    A failed fetch is cached as an empty file: during a sweep, dozens of quotes can cite the
    same 403ing host, and re-requesting it for each one is both slow and rude."""
    cache_dir = pathlib.Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".txt")
    if path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS:
        return path.read_text(encoding="utf-8") or None
    body = fetch(url)
    path.write_text(body or "", encoding="utf-8")
    return body or None


def make_page_fetcher(cache_dir, fetch=fetch_url):
    """A `fetch_page(url)` callable for check_source: cached, and rate-limited per host."""
    last_hit = {}

    def polite_fetch(url):
        host = re.sub(r"^\w+://([^/]+).*$", r"\1", url).lower()
        wait = HOST_DELAY_SECONDS - (time.time() - last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        last_hit[host] = time.time()
        return fetch(url)

    return lambda url: cached_page_text(url, cache_dir, polite_fetch)


def _check_written_source(row, base, fetch_page):
    """Verify a non-video source by matching the quote against the live page. No-op unless the
    caller opted into network I/O by supplying `fetch_page`."""
    if fetch_page is None:
        return None                                  # DB-only default; nothing fetched, nothing checked
    url = (row.get("source_url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    if AGGREGATOR_SOURCE.search(url) or QUIZ_SOURCE.search(url):
        return None                                  # invalid-source / unquotable-source own these
    runs = verbatim_runs(row.get("quote_text"))
    if not runs:
        return None                                  # nothing verbatim to check

    page = extract_page_text(fetch_page(url) or "")
    if len(page.split()) < MIN_PAGE_WORDS:
        return Finding(check_id="source-unfetchable", principle="source must be verifiable",
                       severity="medium",
                       what=f"Cited page returned no readable prose (JS-rendered, blocked or empty): {url}",
                       suggested_fix="Open the page and check the quote by hand; if the page is gone, re-source or deselect.",
                       **base)

    best_len, _ = longest_verbatim_match(runs, [page])
    need = min(MIN_RUN_WORDS, max(len(r.split()) for r in runs))
    if best_len < need:
        return Finding(check_id="source-unverified", principle="quote must appear in its cited source",
                       severity="high",
                       what=f"No distinctive phrase from the quote appears on the cited page {url} — the text may be paraphrased, from a different page, or from a superseded version.",
                       suggested_fix="Re-read the cited page: fix the quote to match it verbatim, correct source_url to the page that does carry it, or remove the quote.",
                       **base)

    # Before the clip check: a relayed quote is verbatim and would otherwise fall through to
    # source-midsentence-clip, which reports the wrong defect at medium instead of high.
    relayed = nested_quotation(row.get("quote_text"), page)
    if relayed:
        return _relayed_finding(relayed, base)

    if starts_midsentence(row.get("quote_text"), page):
        return Finding(check_id="source-midsentence-clip",
                       principle="an excerpt must not misrepresent by where it is cut",
                       severity="medium",
                       what="Quote is verbatim but starts mid-sentence with no ellipsis marking the cut — the clause before it may carry the candidate's actual position.",
                       suggested_fix="Re-read the full sentence: extend the quote to carry the operative clause, or mark the cut with a leading '…'.",
                       **base)
    return None


def check_source(conn, row, fetch_page=None):
    """Return a Finding if the quote can't be verified against its cited source, else None.
    row: dict with id, candidate, topic_key, race_id, quote_text, source_url.

    Video sources are matched against the ingested transcript (needs `conn`). Written sources
    are matched against the live page, but only when `fetch_page` is supplied — see the module
    docstring on why that's opt-in."""
    base = dict(level="quote", quote_id=row["id"], topic_key=row["topic_key"],
                race_id=row["race_id"], candidate=row["candidate"], fix_class="decision-required")

    # Signal 1 of the relayed-speech check needs neither a page nor a transcript, so it runs for
    # every source kind before we resolve the source at all — including the ones nothing else can
    # inspect (un-ingested video, a 403ing page, an aggregator we deliberately skip, a source the
    # caller never opted into fetching). Free, and the defect is real regardless of source kind.
    self_frame = _self_framed(row.get("quote_text"))
    if self_frame:
        return _relayed_finding(
            f"the quote itself opens with “{self_frame}” and then quotes someone", base)

    vid, t = parse_youtube(row.get("source_url"))
    if not vid:
        return _check_written_source(row, base, fetch_page)
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id FROM meetings.meetings WHERE video_url=%s LIMIT 1", (vid,))
        m = cur.fetchone()
        if not m:
            return Finding(check_id="source-not-ingested", principle="source must be verifiable",
                           severity="medium", what=f"Cited video {vid} is not ingested; quote can't be auto-verified.",
                           suggested_fix="Verify the quote against the source manually, or ingest the source.", **base)
        # Ordered: the joined transcript is read as running text below (and by
        # longest_verbatim_match), so segments arriving out of order would break runs that span a
        # segment boundary and would put the wrong words before a match.
        cur.execute("SELECT text, speaker_name, start_time FROM meetings.segments "
                    "WHERE meeting_id=%s ORDER BY start_time", (m["id"],))
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

    # Relayed speech: the candidate said these words, but as someone else's. Restricted to the
    # candidate's OWN segments — otherwise a moderator's "People will say …" earlier in the tape
    # could be read as framing the candidate's answer. Joined with newlines so a frame can never
    # reach across a segment boundary (see _third_party_frame).
    own = [s["text"] for s in segs
           if not cand_last or cand_last in (s["speaker_name"] or "").lower()]
    relayed = nested_quotation(row.get("quote_text"), "\n".join(own))
    if relayed:
        return _relayed_finding(relayed, base)

    if t is not None and hits:
        nearest = min(abs(h["start_time"] - t) for h in hits)
        if nearest > 180:
            return Finding(check_id="source-timestamp-drift", principle="deep-link should point at the quote",
                           severity="low", what=f"Quote found ~{int(nearest)}s from the cited timestamp {t}s.",
                           suggested_fix="Update the &t= deep-link to the correct moment.", **base)
    return None


def run_source_checks(conn, rows, fetch_page=None):
    out = []
    for r in rows:
        f = check_source(conn, r, fetch_page=fetch_page)
        if f:
            out.append(f)
    return out

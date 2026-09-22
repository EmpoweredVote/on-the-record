# Evidence Transcript Lane — extract evidence from ingested transcripts

**Status:** Draft for review (brainstormed with Chris 2026-09-21)
**Repo:** on-the-record only — `src/evidence/data.py` (+ a transcript source path in `pipeline.py`),
`scripts/evidence_slice.py` (a `--source` flag), tests. Reads the existing `meetings.*` corpus
(read-only). **No schema change; no ev-accounts change.**
**Follows:** the evidence loop architecture (`docs/evidence-program/evidence-loop-architecture.md`).
This builds the **first missing wire: ingested transcripts → evidence.**

## Why

The evidence pipeline currently reads only compass-cited **web URLs**, fetched over HTTP. It ignores
the diarized debate/forum/interview/meeting transcripts we have already ingested (`meetings.*`) — which
are the candidate's **own spoken words at the actual event**: the richest, most defensible first-person
source, and exactly what the own-words rule wants. Coverage already exists (LA Mayor: Bass 8 meetings,
Raman 8, as linked speakers). This lane feeds those transcripts through the same
extract → verify → cross-check → judge → disposition pipeline into `inform.evidence_items`.

Transcript turns are Tier-1 **by construction**: they are the candidate speaking (own words) at the
primary venue (the event itself). So this lane should reliably produce green evidence where the web
lane produced third-person positions.

## Approach (assumes the "direct" model)

Per the open decision in the architecture note, this spec assumes evidence reads transcripts
**directly** (evidence = the hub; read-rank/compass become views later). If we later choose to bridge
the existing `essentials.quotes` flow instead, this lane is where that decision lands — flagged as a
revisit, not blocking.

## Data — `src/evidence/data.py`

Add `fetch_transcript_sources(conn, politician_id) -> list[TranscriptSource]` (read-only):

- Find the meetings where this politician is a linked speaker: `meetings.speakers` rows with
  `politician_id = %s` (join to `meetings.segments` on `speaker_id`, and `meetings.meetings` for
  metadata).
- For each such meeting, assemble the candidate's **own turns** — their `segments.text` in
  `segment_index` order — as the source text to extract from. Include the **immediately preceding
  non-candidate turn** (e.g. the moderator's question) as lightweight context, since the eliciting
  question gives a stance its meaning (and aligns with Read & Rank's question-as-unit).
- Carry, per meeting: `meeting_id`, a **source_url** (the meeting's `source_url` or published page),
  a **deep-link base** (`video_url`) + the candidate's first turn `start_time` for **click-to-seek**,
  the meeting `title`/`date`/`event_kind`, and the raw turn text.

A `TranscriptSource` dataclass captures those fields. (No transcript is fetched over the network — the
text is already in the DB.)

## Pipeline — a transcript source path in `src/evidence/pipeline.py`

The existing `run_source` assumes `fetcher(url) -> text` and classifies a web domain. For transcripts
the text is already in hand and the venue is known, so add `run_transcript_source(...)` (or generalize
`run_source` with an injected text + known source_type) that:

- Skips the HTTP fetch and domain triage; sets `source_type = PRIMARY`.
- Runs `extract_quotes` on the candidate's turn text (chunked as usual), with the preceding question
  available as context.
- Applies the **verbatim gate** against the transcript turn text (the quote must be an exact substring
  of what they said).
- Runs the cross-check and judge as usual. `own_words` and `primary` are **definitionally true** here
  (their own speech at the event); the substantive gate that still matters is **`judge:mechanism`** (a
  debate answer can still be a bare goal) and the tag check. (Running the full cross-check is harmless;
  the plan may short-circuit `own_words`/`primary` for transcript sources to save calls — a detail, not
  a requirement.)
- Builds `EvidenceItem`s with `deep_link` = the meeting video URL + the turn's `start_time`
  (click-to-seek), `source_url` = the meeting page, and `context` = the eliciting question + turn.

`run_candidate` gains an optional transcript-sources argument; the committer and dedup key are
unchanged (dedup: `politician_id, source_url, md5(lower(verbatim_text))`).

## Runner — `scripts/evidence_slice.py`

Add `--source web|transcripts|both` (default `both`). `web` = today's behavior
(`fetch_cited_sources` + `fetch_page_text`); `transcripts` = `fetch_transcript_sources` +
`run_transcript_source`; `both` runs each lane and merges items/leads before scoring + artifacts.

## Tests (offline, mock DB / fixtures)

- `fetch_transcript_sources`: given mocked `speakers`/`segments`/`meetings` rows, it returns one source
  per meeting with the candidate's turns in order, the preceding question as context, and the
  deep-link base + first `start_time`. (db mocked, no network.)
- The transcript path builds `EvidenceItem`s with `source_type=PRIMARY`, a `deep_link` carrying the
  timestamp, and runs the verbatim gate against the turn text (a quote that is a substring passes; a
  reworded one drops as `verbatim-fail`). Providers mocked.
- The runner parses `--source` and routes to the right lane(s).
- Full suite stays green.

## Validation (human-gated, artifact-based)

Run `--source transcripts` for **Bass and Raman** (8 meetings each), artifacts only. Expect: green
evidence that is genuinely their spoken words, each with a **click-to-seek deep link** to the moment in
the video; `judge:mechanism` still filtering bare goals. Report green/flagged counts and spot-check
that several deep links resolve to the right timestamp. Chris decides whether to commit to prod
(new rows; the prior web-lane Bass rows are untouched).

## Risks / decisions

- **Direct vs. bridge** the existing transcript→`essentials.quotes` flow — assumed direct here; revisit
  if we'd rather derive from that flow.
- **Turn assembly:** feeding the candidate's turns with the preceding question as context is the unit;
  if turns are short/interleaved, the plan tunes how much adjacent context to include (bounded).
- **Volume/cost:** 8 meetings/candidate × many turns → several extractor calls (chunked). Bounded per
  candidate; fine for a slice.
- **Deep-link correctness** depends on `video_url` + `start_time` format (click-to-seek). The plan pins
  the exact URL/timestamp shape (per the web-publishing click-to-seek convention).
- **Council vs. campaign speech:** a candidate's city-council/official remarks are still their own words
  and valid evidence; `event_kind` lets a later view weight debate/forum over routine business.

## Success criteria

- Offline: `fetch_transcript_sources` shapes sources correctly from mocked rows; the transcript path
  produces `PRIMARY` `EvidenceItem`s with timestamped deep links and enforces the verbatim gate;
  `--source` routes correctly; full suite green.
- Live (gated, artifacts): Bass + Raman transcript runs yield green own-words evidence with working
  click-to-seek deep links, mechanism-gated.

## Cost amendment (2026-09-21, after the Bass base-case run)

The base design (feed the whole interleaved transcript; cross-check every quote against
`source_text[:60000]`) cost ~$1–2 per candidate and ~50 min for Bass alone: 209 items → 170
non-dropped, each re-sending up to 60K chars of transcript to the cross-checker (~10M chars). Two
changes bring it to cents with no loss of the trust model (Chris's call: keep the independent check,
trim its input):

1. **Extractor input = the candidate's own turns only, each preceded by its eliciting (immediately
   prior, different-speaker) turn for context** — not every speaker. `fetch_transcript_sources` filters
   segments to the candidate's `speaker_id`(s) (from `meetings.speakers` for that meeting+politician)
   and includes the one preceding turn as the question. `segments` (for timestamps) becomes the
   candidate's turns only. Cuts extractor input ~2× on debates, much more on council meetings.
2. **Trim the cross-check's input to the quote's local window** (~±800 chars around the quote in
   `full_text`), not the whole transcript. Keeps the independent second-model check (own-words /
   in-context / tag) but sends ~1–2K instead of 60K. `_evaluate_quote` gains an optional
   `crosscheck_text` (verbatim still checks the full source; cross-check uses the window); the web lane
   passes nothing and is unchanged.

## Execution notes

- Run with the MAIN checkout `.venv/bin/python`; `OPENROUTER_API_KEY` exported; `--env-file
  <ev-accounts>/backend/.env` for the read-only DB. Models: extractor `haiku-or`, crosschecker
  `gemini-flash`, judge `deepseek` (OpenRouter).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

### Cost-amendment follow-up (2026-09-21): definitional primary/own-words

Trimming the cross-check to a local window broke its `primary`/`own_words` judgment: from a ~1.6K window it cannot tell the source is the candidate's own event, so it answered `primary=false` on their own speech and false-flagged ~half the greens (offline sim: Bass green 10→28, Raman 6→25 once corrected). Fix: for transcript sources, `own_words` and `primary` are **definitional TRUE** (their own words at the event); the cross-check still judges `in_context` + `tag` on the window, and the judge still gates mechanism. `_evaluate_quote` gains a `definitional_primary` flag; `run_transcript_source` sets it. The web lane is unchanged. Green counts are validated by the offline sim over the real cross-check/judge outputs, so no extra LLM run is required to confirm.

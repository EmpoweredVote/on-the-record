# The Evidence Loop — connecting design

**Status:** Living architecture note (2026-09-21). This is the map that connects the pieces of the
Sources & Evidence program so no session loses the whole picture. It is not a buildable spec; each
slice gets its own spec/plan under `docs/superpowers/`.

## The vision (what we are building)

A **defensible, primary-source evidence library per politician** — for each issue, the candidate's
own words (later: votes and actions), each pointing to a **primary source** with enough **context that
a reader can follow the link and vet it.** Think of it as **a Zotero / bibliography for politicians'
stances**: every entry is a citation of a position a candidate actually holds, in their own words.

The bar is trust and defensibility: *a candidate must not be able to say "I never said that."* So an
entry must be **verbatim their words**, from a **primary source**, tagged to a **topic**, and — the
differentiation rule — must state a **forward-looking approach** (the HOW / the mechanism), not merely
an agreeable goal. The "why" is a nice-to-have when present, not required.

**Read & Rank quotes and Compass stance values are VIEWS derived from this evidence layer**, not
separate curation jobs. Long-term, this evidence moves into essentials as the comprehensive, diggable
record behind every politician.

## The evidence entry (the citation)

Stored in `inform.evidence_items` (already live). Each row is one citation:

- **politician** + **topic** — a Compass `topic_id` when it fits, or a free `issue` label when the
  position is broader/narrower than the Compass set. (The table already supports both.)
- **verbatim_text** — the candidate's exact words (first person, or a sentence directly quoted from
  them). Never reworded.
- **source_url / deep_link / context** — where it came from and enough surrounding passage to vet it.
- **machine_status** (green / flagged) + human **review_status** (pending / accepted / rejected) +
  **review_reason** — the human decisions are an always-on, categorized learning signal.
- **gate_flags / provenance** — why the machine judged it as it did (verbatim gate, cross-check,
  judge:mechanism), and which models ran.

The forward-looking / HOW requirement is enforced by the pipeline's `judge:mechanism` gate; the
own-words requirement by the extractor + cross-checker (first person or a direct quotation only).

## The pieces (most already exist)

1. **Discovery** (`src/discovery/`, live — PR #231 + hub registry): finds candidate sources and
   classifies each with `event_kind` (debate / forum / podcast / questionnaire / news_clip…),
   `original_vs_clip`, and a **`route`**: `ingest` (a full event recording → transcribe) or
   `quote_source` (a page to quote from). It is a human-reviewed queue.
2. **Ingestion + the transcript corpus** (`meetings.*`, live): the diarized/transcribed debates,
   forums, interviews, floor proceedings — the candidate's spoken words, already linked to a
   politician via speaker → `politician_id`.
3. **The evidence pipeline** (`src/evidence/`, `scripts/evidence_slice.py`, live — PRs #246/#247/#249/#250):
   extract → verbatim gate → independent cross-check → judge → disposition (green/flagged/dropped),
   plus chase-the-primary **leads**. Writes to `inform.evidence_items` via `scripts/commit_evidence.py`.
   The review surface is the ev-accounts admin page (PR #569).
4. **Read & Rank / publish-quotes** (`essentials.quotes`, live): already turns ingested transcripts
   into curated quotes. In the target architecture this becomes a **view derived from accepted
   evidence**, not a parallel track.
5. **Compass**: stance values + reasoning — also a **view** derived from accepted evidence.

## What is wired vs. not

**Today the evidence pipeline reads only the compass-cited web URLs** (`inform.politician_context.sources`),
fetched fresh over HTTP. It does **not** read the ingested transcript corpus, and its leads stop at a
`leads.json` file. Two connections are missing:

- **Ingested transcripts → evidence** (the first slice we build): the evidence pipeline should read the
  `meetings` transcripts for a candidate, so their spoken first-person words become evidence. This is
  the richest own-words source and needs no fetching.
- **Leads → discovery** (parallel track): the evidence pipeline's chase-the-primary leads should land
  in the **discovery review queue** as "find this primary" tasks, where discovery's existing
  classify-and-`ingest` machinery already knows how to bring the primary in.

## The loop (and its brake)

```
news/web quote  →  LEAD ("said X at debate/podcast Y")
   →  discovery review queue        ← the BRAKE: policy / human picks which leads to chase
        →  route = ingest  →  transcribe into meetings.*
             →  evidence pipeline reads the transcript  →  verbatim primary → inform.evidence_items
                  →  may surface a NEW lead  →  back to the queue
```

The **discovery review queue is the natural bound** — leads do not auto-chase forever; they queue for
approval (policy, or the steward's judgment), so the loop cannot run ad infinitum. This also fits the
steward role: choosing which leads are worth chasing, not clicking every row.

## Curation policy (settled while reviewing Bass, 2026-09-21)

- **A quote is the candidate's literal words** — first person (I / we / my / our / us) or a sentence
  directly quoted from them. Third-person prose ("Bass will…", "the Mayor has…") is a *position*, not a
  quote. This is a **text-voice test, per quote — never a per-domain block** (a campaign site's "I
  will…" counts; its "Bass will…" does not).
- **Capture is faithful and full; condensing to essence is a separate, deferred step** governed by
  `docs/quote-curation/PRINCIPLES.md` + `EDITORIAL.md` and the `publish-quotes` skill. The evidence
  record is always the exact words; a derived view trims for display.
- **Track record ≠ stance.** "She declared a state of emergency" is an action/record, not a
  forward-looking position — a later votes/actions evidence type, not a quote.
- **Ballotpedia:** only the Candidate Connection questionnaire is her words; the rest is editorial.

## Open decisions (not yet settled)

- **Transcripts into evidence — read directly vs. bridge the existing `essentials.quotes` flow.**
  Current lean: evidence reads transcripts **directly** and read-rank/compass become views over
  accepted evidence. Worth a closer look before committing.
- **Loop termination policy** beyond the human queue — depth cap? value threshold? Decide when the
  leads → discovery wire is built.
- **Coverage/equity:** incumbents' web presence is third-person, so their quote yield is low until
  their spoken sources are ingested — a reason the transcript lane matters most for officeholders.

## Status (2026-09-21)

Shipped to on-the-record `main`: trust-core (#246), write-path (#247), pipeline-robustness (#249),
extraction-quality / own-words (#250). ev-accounts: `inform.evidence_items` migrations 1884/1885 (prod)
+ the review surface (#569). Live data: LA Mayor — Raman (reviewed) + Bass (13→4 green after the
own-words fix; 108 flagged), 2-candidate comparability.

**Next, in order of leverage:**
1. **Transcripts → evidence** (this branch's slice) — unlock the best own-words source.
2. **Leads → discovery** (parallel session) — close the chase-the-primary loop.
3. **Auto-handle flagged** — stop surfacing the flagged bucket for manual review (Nithya flagged
   precision 0.00; Bass 108 flagged) so the steward reviews green, not noise.
4. **Editorializing step** — condense accepted evidence to display quotes per the curation rules.
5. **Derive read-rank + compass as views**; then **votes/actions** as evidence types.

Program memory: `evidence-sources-and-evidence-program` (indexed in `MEMORY.md`).

---
name: publish-quotes
description: Turn raw on-the-record meeting/interview transcript text into clean, faithful, single-claim quotes and insert them into the ev-accounts essentials.quotes table as not-live (draft) records tagged to a politician and a compass topic. Use when the user wants to split, clean, condense, or save a spoken quote, add quotes for a politician on a topic, or prepare quotes for Read & Rank review.
---

# Publish Quotes

Convert messy spoken transcript text into faithful, on-the-record quotes stored in
`essentials.quotes` (in the **ev-accounts** DB) as **not-live drafts**. A human then picks
the single live quote per topic in `/admin/readrank-quotes`.

Principles (the *why*): `essentials/docs/QUOTE-CURATION-PRINCIPLES.md`. This skill is the
operational procedure that implements them.

Two distinct jobs, do them in order:
1. **Craft the quotes** — editorial judgment. See [EDITORIAL.md](EDITORIAL.md). Never skip; never
   editorialize. The user owns every wording call.
2. **Store them** — deterministic. Use `scripts/insert_quotes.py`. DB facts in [REFERENCE.md](REFERENCE.md).

## Workflow

- [ ] **Finalize wording with the user.** Split into single-claim quotes, trim filler (silent for
      tics/stutters, `…` for substantive cuts), bracket any inserted words `[like this]`, keep policy
      attribution, cut personal attacks. Follow [EDITORIAL.md](EDITORIAL.md). Confirm the exact text
      before touching the DB. **Every quote must be a verbatim sentence from the source** — including
      written/campaign-site quotes; never a curator-summarized bullet list.
- [ ] **Produce the blind version (standard step).** Set `deidentified_text` = the canonical quote
      **plus extra de-identification**: strip speaker self-ID ("as governor", "in my district", own
      record) and depersonalize named people ("Newsom" → "[the current administration]"). This is
      Read & Rank's blind-card text — do it for every quote, not just occasionally. If de-id would
      change the position, pick a different quote. See [EDITORIAL.md](EDITORIAL.md).
- [ ] **Resolve the politician.** Look up `politician_id` in `essentials.politicians` and show the
      user the matched name to confirm. Candidates may not have a row — verify they exist.
- [ ] **Pick the topic_key.** Must be a canonical key in `inform.compass_topics` (lowercase). The
      script validates this and refuses unknown keys. **Responsiveness gate:** assign a quote to the
      topic whose *framed question* it actually answers — not just the subject it mentions. A quote
      that's about the topic but dodges its question (e.g. answering "how to prevent homelessness"
      under a "criminalization of homelessness" topic) is off-question: re-home it to the topic it
      answers, or leave the candidate absent. Don't rank an off-question quote for distinctiveness.
      A candidate who only spoke in record/attacks (no forward position) is **absent** — don't
      launder record into a pseudo-position. See `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` §7.1.
- [ ] **Reconcile curation labels → topic keys.** A curation-page publish export uses free-text
      `topic_label`s. Map each to a canonical `inform.compass_topics` key and set it as the quote's
      `topic_key`. Confirm the mapping with the user.
- [ ] **Write the editor note.** For every quote capture why it was selected and, if edited, what
      changed and why. Confirm with the user before insert. The script enforces a non-empty note.
- [ ] **Resolve the source.** Get the meeting's YouTube URL from `meetings.meetings.source_url`.
      Capture each quote's start time (seconds) so the script deep-links the exact moment.
- [ ] **Dry-run the insert**, show the user the preview, then `--commit`.
- [ ] **Verify**, then tell the user to select the live quote in `/admin/readrank-quotes`.
- [ ] **Auto-run the audit (handoff).** After `--commit`, run the `audit-quotes` skill scoped to the
      just-inserted ids: `audit-quotes --ids <id1,id2,...> --include-drafts --scope-label "<race> new"`.
      Show the findings before the user selects the live quote. Fix mechanical/guided findings via the
      audit's gated flow; surface decision-required ones. See `.claude/skills/audit-quotes/SKILL.md`.

## Running the script

Always use the on-the-record venv (it has psycopg2). Dry-run first — `--commit` writes for real.

```bash
.venv/bin/python .claude/skills/publish-quotes/scripts/insert_quotes.py batch.json            # preview
.venv/bin/python .claude/skills/publish-quotes/scripts/insert_quotes.py batch.json --commit   # write
```

`batch.json`:
```json
{
  "politician_id": "9a60d603-194d-410f-ae01-85bd6293f1a7",
  "topic_key": "abortion",
  "source_url": "https://www.youtube.com/watch?v=VIZ1h4OaImU",
  "quotes": [
    {"text": "First single-claim quote …", "timestamp_seconds": 919,
     "editor_note": "Why this quote was picked; note any edits and why."},
    {"text": "Second quote …", "timestamp_seconds": 974,
     "editor_note": "Verbatim, no edits — clearest line on the topic.",
     "topic_key": "housing", "source_url": "https://www.youtube.com/watch?v=OTHER"}
  ]
}
```
Use `"politician_name": "Steve Hilton"` instead of `politician_id` to look up by name (errors if
not exactly one match). Add `"deidentified": "…"` to a quote to override the default verbatim copy.

Every quote **requires** a non-empty `editor_note` — the script refuses the batch otherwise.
Per-quote `topic_key` / `source_url` override the batch-level defaults, so one batch can
span multiple topics and sources (e.g. straight from a curation-page export).

## Non-negotiables

- Insert as **drafts only** (`readrank_selected = false`). Selecting the live quote is a human step.
- `deidentified_text` is the **blind Read & Rank card text** and must be populated for the row to
  be admin-selectable. Produce it as a **standard step** (see workflow): canonical quote + extra
  de-identification (strip speaker self-ID, depersonalize named people). A verbatim copy is only
  correct when the canonical quote already carries no speaker-identifying or personal-name material.
- Every quote carries an **`editor_note`** (selection rationale + edit justification). No blank notes.
- House cap: **≤ 2 drafts per (politician, topic)** — the script warns when exceeded.
- Production DB write — **always dry-run and get the user's OK before `--commit`.**

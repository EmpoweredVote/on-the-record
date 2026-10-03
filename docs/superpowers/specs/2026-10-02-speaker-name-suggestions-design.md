# Speaker name suggestions — design

Date: 2026-10-02 · Status: approved design, spec under review · Owner: Chris

## Problem

In review, Chris names speakers who are not on any roster — witnesses, staff,
hosts — by listening to their self-introduction ("Rabbi Aaron Spiegel from the
Indy Multi-Faith …") and then searching the web for the exact spelling. On the
Indiana Senate Judiciary hearing of 2026-01-14 that was ~20 of 33 speakers.

Speaker ID today cannot help:

- Layer 2 (`src/identify.py`, `apply_pattern_matching`) already has
  `self_identification` and `chair_recognition` patterns, but rule CSIDENT-02
  discards every name whose surname is not on the roster
  (`_surname_matches_roster`). Witnesses are never on a roster, so they are
  always dropped.
- Nothing verifies spelling.
- Layer 3 (LLM speaker ID) is switched off (`SPEAKER_ID_LLM_ENABLED`).

## Goal

Pre-fill a suggested name, role and source for each speaker in review, with
the evidence shown, so that Chris confirms with one key instead of searching.
v1 never applies a name without Chris. A later, separate decision may allow
auto-apply for tiers that prove themselves (see "Graduation").

Non-goals: replacing voice profiles; naming speakers who never state or are
never called by a name; building profiles of private individuals.

## Attribution rules

### Evidence (strongest first)

| # | Evidence | Window | Names |
|---|---|---|---|
| E1 | Self-introduction: "my name is X", "I'm X, (with\|from\|representing\|on behalf of) ORG", "X, (president\|director\|…) of ORG" | First ~60 words of the speaker's first substantial turn (≥ 10 words) | This speaker |
| E2 | Chair call: "next we'll hear from X", "we'll call X", "welcome X", "X, you're recognized", "Senator X" | Last ~25 words before a turn change | The next different speaker |
| E3 | Thank-back: "thank you, Ms. X" | First ~15 words after the turn ends, by a different speaker | The speaker who just finished |
| E4 | Captions agree (source captions contain the same name) | Whole meeting | Corroboration only; never names alone |

### Exclusions (anti-misattribution)

| # | Rule |
|---|---|
| X1 | Mentions of others never count: "my colleague (Senator) X", "as X said", "X asked me", "the bill by X", family words ("my son/wife X"), possessives ("X's"). |
| X2 | A name outside the E1 intro window does not name the current speaker. |
| X3 | In group intros, names after "here with" / "joined by" / "and my colleague" are other people. |
| X4 | One name, one speaker: if a name lands on two labels, both are a conflict and neither is pre-filled. |
| X5 | Two different names on one label, each with E1 or E2+E3, is a conflict; flag it as a possible diarization mis-merge (see existing mis-merge detector). |
| X6 | A name with an office title (Senator, Representative, Judge, Mayor, Councilmember, Commissioner, …) resolves only against the politicians table, never against the web. |

### Tiers

- **Strong:** E1 and (E2 or E3) agree.
- **Medium:** E1 alone, or E2 and E3 agree.
- **Weak:** E2 alone ("called on only") or E3 alone ("thanked only").

In v1 every tier waits for Chris. The tier decides ordering, wording and
whether it may be pre-filled (see "Evaluation").

### Roles

Guessed in `name_candidates`, shown in review, always editable:

- Title word + politicians-table match → `official`, linked to `politician_id`.
- Staff cues ("Legislative Services Agency", "committee counsel/attorney",
  "fiscal analyst", "city staff", "clerk") → `staff`.
- "on behalf of" / "representing" / "(title) of ORG" → `presenter`.
- Otherwise, within public testimony → `public_comment`.
- Campaign events (debate, forum) keep their own vocabulary
  (`candidate` / `moderator` / `panelist`, `src/event_kinds.py`).

## Architecture

```
transcript ─► name_evidence ─► name_candidates ─► name_lookup (+ verify) ─► name_suggestions.json ─► review (GUI, terminal)
                                                                                                        └─► accept/edit log
```

| Unit | File | Responsibility | Depends on |
|---|---|---|---|
| Evidence | `src/name_evidence.py` | Extract E1–E4 with exact quote + segment id; apply X1–X3. | Segments only (pure) |
| Candidates | `src/name_candidates.py` | Group per label; tiers; X4–X6 conflicts; role guess. | Evidence (pure) |
| Lookup | `src/name_lookup.py` | Resolve spelling, in order, first hit wins: (a) politicians in the meeting's state; (b) `meetings.local_people` from past meetings; (c) Claude Code researcher; (d) page verification. Cache. | DB (read-only), `claude` CLI, HTTP |
| Output | `<meeting_dir>/name_suggestions.json` | One record per label: name, tier, role, evidence quotes, source URL, `verified` flag, conflicts. | — |
| Review | GUI review panel, then terminal `--review` | Show suggestion + evidence; one key accepts; log accept/edit. | `name_suggestions.json` |
| Eval | `scripts/eval_name_suggestions.py` | Score against gold (below). | Gold meetings |

### Claude Code researcher (lookup step c)

- Runs only on Chris's Mac, under his Claude subscription:
  `claude -p --output-format json --allowedTools WebSearch,WebFetch --max-turns 6`.
- Called only for non-titled names that have an affiliation or a full
  (first + last) name. One call per (name, affiliation); results cached in
  `~/CouncilScribe/config/name_lookup_cache.json`.
- Prompt asks for strict JSON: `{"name", "affiliation", "url"}` or
  `{"not_found": true}`.
- 120-second timeout. A missing/unauthenticated CLI, rate limit, timeout or bad
  JSON → no lookup; the suggestion stays, marked "spelling not verified".
- Not available in CI (GitHub Actions). Moving lookup off the Mac later means
  adding a second researcher behind the same interface (e.g. a search API),
  which needs a CTO vendor check first.

### Page verification (lookup step d)

Our code fetches the returned URL itself (browser User-Agent) and requires the
exact suggested name — case-insensitive, whitespace- and punctuation-normalized
— to appear in the page text. Only then is the suggestion `verified`. Otherwise
it is shown as "spelling not verified" and the URL is not shown as a source.

### Privacy rules

- Send only: spoken name, stated affiliation, meeting city/state. Never
  transcript text.
- Store only: the spelling and one source URL.
- Never search a speaker who gives no affiliation and only a common or partial
  name (e.g. "Michael", "Chelsey").

## Error handling

A failure removes a suggestion or lowers it to "not verified". It never stops
the pipeline.

| Failure | Behaviour |
|---|---|
| `claude` unavailable / timeout / rate limit / bad JSON | Suggestion kept, "spelling not verified". |
| Source page fetch fails or lacks the name | "Spelling not verified"; URL not shown; reason logged. |
| DB unreachable | Lookup steps a–b skipped; evidence and tiers still produced. |
| X4/X5 conflict | Shown as a conflict, never pre-filled. |
| Chris edits a suggestion | Chris's value wins; edit logged. |

## Evaluation

Gold set: meetings whose speakers carry `id_method == "human_review"` (673
names across ~170 meetings on 2026-10-02; 529 non-politician speakers).
Voice-profile-only names are excluded so the test does not grade itself.

1. Run evidence + candidates on each gold meeting's transcript text with all
   speaker names removed.
2. **Attribution score** with `src/speaker_id_eval.classify`
   (correct / safe_null / hallucination / miss / wrong; surname match).
3. **Spelling score:** exact full-name match against the gold name.
4. Report per tier and per `event_kind`. The headline number is
   wrong + hallucination.
5. **Pre-fill bar:** a tier is pre-filled in review only if it scores
   ≥ 95% correct and ≤ 2% wrong + hallucination on the gold set. Tiers that
   fail are shown as plain hints, not pre-filled.
6. **Lookup test:** about 50 gold witnesses (stratified by event kind) through
   lookup steps a–d; report spelling accuracy split by verified / not verified.
   Capped at 50 to stay within subscription usage.

### Graduation to auto-apply (later, separate decision)

A tier may auto-apply only if it passes the pre-fill bar on the gold set and
Chris accepts its suggestions unchanged ≥ 98% of the time over his next ~10
reviewed meetings (from the accept/edit log). Auto-apply is never enabled by
this spec.

## Testing

- Unit tests for `name_evidence` and `name_candidates` on small invented
  transcripts: every E and X rule, including "my colleague Senator Brown",
  "here with Jackson Franklin", one name on two labels, a name mid-turn.
- Fixtures from real meetings: Jan 14 2026 IGA hearing (Spiegel, Sample,
  Kurtz) and one Bloomington council meeting with public comment.
- `name_lookup` with a fake `claude` runner and fake page fetcher: verified,
  not verified, timeout, bad JSON, cache hit.
- Each slice's PR includes the evaluation output.

## Build order (one PR each)

1. **Slice 1 — evidence + candidates + eval.** No lookup, no review change.
   Delivers real per-tier numbers before more is built.
2. **Slice 2 — lookup + page verification** and the 50-witness spelling test.
3. **Slice 3 — review:** GUI panel, then terminal; accept/edit logging.
4. **Later — auto-apply**, only per the graduation rule, by separate decision.

## Open points

- The GUI review panel location for the evidence block is decided in slice 3
  against the current panel layout.
- Whether Layer 2's existing roster-only `self_identification` stays as-is or is
  replaced by this module is decided after slice 1's numbers.

## Refinements from planning (2026-10-02)

- **Partial names:** a first-name-only name ("my name is Nitya") is kept as a
  hint but is never pre-filled.
- **"Misspelled" eval outcome:** same speaker and same first name, different
  surname spelling (Whisper writes "Peter Pearson" for gold "Peter Berezin").
  It counts as correct attribution and is reported separately as a spelling
  miss for slice 2's lookup to fix.
- **Junk gold labels** ("Candidate7", "Host (Unknown - CRG)") count as no-name.

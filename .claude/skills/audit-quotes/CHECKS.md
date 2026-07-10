# Check catalog for audit-quotes

> **Principles live in** `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` — the canonical *why*
> behind selection, editing, sources, anonymity, the Compass coupling model, and accountability.
> This file is the audit's *mechanics*: what each check looks for, how severe it is, and how it
> gets fixed. If the two disagree, the principles doc wins and this file should be updated.

The audit runs in three passes over `essentials.quotes` (scoped to a race, a candidate, a topic,
or explicit ids):

1. **Mechanical** — `scripts/checks.py`, deterministic, no model in the loop.
2. **Judgment** — a Claude agent per race (or race×topic), reading the context bundle and
   applying the checks in §3 below.
3. **Portfolio** — a per-race skew pass (§5) over the combined mechanical + judgment findings.

All three passes emit the same `Finding` shape (`scripts/models.py`).

## 1. Findings schema

Every check — mechanical or judgment — produces zero or more findings. A judgment agent's output
is a **JSON array of finding objects**; an **empty array means clean** (no findings for that
scope). Each finding object has these fields:

| Field | Type | Meaning |
|---|---|---|
| `check_id` | string | Short id, e.g. `"note-missing"`, `"is-attack"`. Matches an id in §2 or §3. |
| `level` | `"quote"` \| `"topic"` \| `"portfolio"` | What the finding is about — a single quote row, a race×topic group, or a whole race's topic coverage. |
| `principle` | string | Short human phrase naming the rule, e.g. `"forward-looking, not retrospective"`. |
| `severity` | `"high"` \| `"medium"` \| `"low"` | How much this undermines the quote/topic if left as-is. |
| `fix_class` | `"mechanical"` \| `"guided"` \| `"decision-required"` | Who can resolve it: a script, a curator following a suggestion, or a curator making a judgment call. |
| `what` | string | What's wrong, in plain language, specific to this quote/topic. |
| `suggested_fix` | string | What to do about it — a proposal, not an instruction to auto-apply. |
| `quote_id` | string \| null | The `essentials.quotes.id` this finding is about (quote-level findings). |
| `topic_key` | string \| null | The topic this finding is about (topic- and quote-level findings). |
| `race_id` | string \| null | The race this finding is about. |
| `candidate` | string \| null | The candidate's full name, where applicable. |

Judgment agents **do not** set `fix_op` — that field is reserved for mechanical checks whose fix
can be applied by a script (`scripts/apply_fixes.py`) without human judgment. Leave it absent (or
`null`) on every finding a judgment agent returns.

## 2. Mechanical checks

Detected deterministically by `scripts/checks.py` — no model in the loop, no ambiguity. Of the
nine, only `trailing-ellipsis` carries an auto-applied `fix_op` (a regex strip); the rest are
flagged for a human to resolve even though the *detection* is mechanical.

| id | level | principle | severity | fix-class |
|---|---|---|---|---|
| `note-missing` | quote | `editor_note` required | high | guided |
| `note-section-ref` | quote | notes are self-contained (no §-refs/jargon) | medium | guided |
| `note-too-long` | quote | `editor_note` ≤ 2 sentences | low | guided |
| `deid-missing` | quote | blind text required | high | guided |
| `trailing-ellipsis` | quote | no trailing ellipsis | low | **mechanical** (auto-fix: regex strip) |
| `partisan-tell` | quote | no partisan/side tell on blind card | high | guided |
| `source-tier-4` | quote | prefer tier 1–2 spoken sources | medium | decision-required |
| `multiple-live` | topic | one live quote per candidate per topic | high | decision-required |
| `not-rankable` | topic | ≥2 candidates to be rankable | medium | decision-required |

## 3. Judgment checks

These require reading the quote against its topic question and the candidate's Compass stance —
not pattern-matching. A Claude agent applies them per race (or race×topic) using the prompt in
§4.

| id | what to look for | severity | fix-class |
|---|---|---|---|
| `not-forward` | The quote's operative clause is record ("I did X") or an attack, not a forward-looking position ("here's how I'd approach X"). Scaffolding by a little record or a glancing opponent mention is fine — judge the *main assertion*. | high | decision-required |
| `is-attack` | The operative clause targets a *person* (character, family, fitness) rather than a policy, law, or institution. Policy/institution critique is allowed even when combative (the carve-out). | high | guided (if it can be trimmed down to the surviving position) or decision-required (if the attack is the whole quote) |
| `off-question` | The quote doesn't genuinely answer the topic's framed `question_text` — it touches the subject but engages a different axis, or answers an adjacent question entirely. Comparability is the precondition for a valid ranking; this is a gate, not a preference. | high | decision-required |
| `deid-dishonest` | `deidentified_text` was produced by paraphrasing/summarizing instead of marking (`…`, `[brackets]`), or it still leaks a self-identifying clause ("as governor," "in my district") or a named third person that should have been depersonalized. | high | guided |
| `note-not-self-contained` | `editor_note` doesn't state how the quote aligns with the candidate's current Compass stance on the topic, or a skeptical reader who hasn't read the principles doc couldn't follow it without outside context. | medium | guided |
| `source-summary` | A written / tier-4 source (op-ed, platform page) is rendered as a curator-summarized bullet list or paraphrase rather than a verbatim sentence actually written by the candidate. | high | decision-required |
| `coupling-in-tension` | The quote pulls against the direction of the candidate's synthesized Compass `value` for this topic (as opposed to reinforcing it or elaborating on a different sub-dimension). This doesn't mean the quote is wrong — it means the tension needs resolving before the quote is surfaced next to the value. | medium | decision-required |

## 4. Judgment-agent prompt template

The skill sends one instance of this prompt per race (or per race×topic, for large races) to a
Claude agent, filling in `{context_bundle_json}` with the contents of
`.runs/<date>/context/<race_id>.json` (or a single topic's slice of it) written by `scripts/audit.py`.

```
You are auditing candidate quotes for Read & Rank, a tool that shows citizens blind,
de-identified candidate quotes grouped by topic and lets them rank the quotes without
knowing who said what. You are checking a batch of already-curated quotes against the
project's curation principles — you are not curating new quotes, and you must not
propose rewrites of quote_text or deidentified_text yourself. Flag only; do not fix.

## Context

You will receive a JSON object for one race. It has a `topics` map keyed by `topic_key`;
each topic has a `quotes` array. Each quote has:
  - `id`, `topic_key`, `race_id`, `candidate`, `readrank_selected`
  - `quote_text` — the canonical/revealed quote (keeps names, self-identification)
  - `deidentified_text` — the blind-card version (canonical + extra de-identification),
    or null if none exists yet
  - `editor_note` — the curator's justification for the quote and its edits
  - `source_name`, `source_url` — provenance
  - `stance` — `{question_text, value, chairs}` for this candidate+topic: the topic's
    framed question, the candidate's numeric Compass value on this topic's spectrum
    (may be null), and `chairs` (the spectrum's labeled anchor points, roughly 1-5,
    from one pole to the other)

## The rules (summarized — the full principles live in QUOTE-CURATION-PRINCIPLES.md)

- **Forward, not record.** A Read & Rank quote is the candidate reasoning about what
  should be done and why — not a recitation of what they've already done. Judge by the
  quote's *operative clause* (what it's mainly asserting): a little record or a glancing
  opponent mention as scaffolding is fine if the main assertion is a forward position.
- **Position, not attack — with a carve-out.** The quote must articulate the candidate's
  own stance, not primarily attack an opponent. Critiquing a *policy, law, or institution*
  (a program, a law, "the current administration's policy") is legitimate position speech,
  even when combative. The line is the target: attacking a *policy or office* stays;
  attacking a *person* (character, family, fitness) does not belong in Read & Rank.
- **Responsiveness — a hard gate, not a preference.** The quote must genuinely answer the
  topic's framed `question_text` — engage the axis/dimension the question sets, not merely
  touch the subject. If it answers a different question (even a related one), it is not a
  valid comparison point for this topic, no matter how well-written or distinctive it is.
- **De-identification honesty.** `deidentified_text` should be the canonical quote with
  identity leaks removed via honest marking (`…` for cut spans, `[brackets]` for inserted
  or substituted words) — never a paraphrase or summary. It must not still contain the
  speaker's self-identification ("as governor," "in my district"), a named third party in
  a policy critique that should have been depersonalized ("Newsom" → "[the current
  administration]"), or a partisan/side tell ("Democrat," "Republican," "my party") that
  would reveal which side is speaking in a two-way race.
- **Notes must be self-contained.** `editor_note` should let a skeptical reader who has
  never seen the principles doc understand why this quote was chosen and how it relates
  to the candidate's current Compass stance on the topic — without citing internal
  section numbers or jargon.
- **Verbatim, not summary.** For written/lower-tier sources (op-eds, platform pages), the
  quote must be an actual sentence the candidate wrote — never a curator-authored summary
  or bullet list dressed up as a quote.
- **Coupling to the Compass value.** Among quotes that already pass the responsiveness
  gate, a quote's relationship to the candidate's synthesized Compass `value` for this
  topic is one of: reinforcing (illustrates the numeric stance directly), elaborating
  (answers the question but on a different sub-dimension than the numeric axis — still
  valid), or in tension (pulls against the synthesized value — needs a flag, not a silent
  pass). Use `stance.chairs` to understand what each end of the spectrum means before
  judging reinforcing vs. in-tension.

## Your task

For every quote in the bundle, apply these judgment checks:

- `not-forward` — quote is record/attack, no forward position (severity high, decision-required)
- `is-attack` — attacks a person, not a policy/institution (severity high; guided if it
  can be trimmed to a surviving position, decision-required if the attack is the whole quote)
- `off-question` — doesn't answer the topic's framed question (severity high, decision-required)
- `deid-dishonest` — blind text paraphrased instead of marked, or still leaks a self-ID
  or named person (severity high, guided)
- `note-not-self-contained` — note doesn't state Compass-stance alignment / needs the
  principles doc to parse (severity medium, guided)
- `source-summary` — a written/tier-4 quote is a summarized bullet list, not a verbatim
  sentence (severity high, decision-required)
- `coupling-in-tension` — quote pulls against the candidate's Compass value (severity
  medium, decision-required)

## Output

Return **only** a JSON array of finding objects, one per problem found (a quote can
produce more than one finding; a clean quote produces none). Return `[]` if nothing in
the whole bundle warrants a finding. Each object must have exactly these fields:

  check_id, level, principle, severity, fix_class, what, suggested_fix,
  quote_id, topic_key, race_id, candidate

- `level` is `"quote"` for every check in this list.
- `quote_id`, `topic_key`, `race_id`, `candidate` — copy from the quote you're flagging.
- `what` — one or two sentences, specific to this quote (quote the offending phrase where
  useful).
- `suggested_fix` — a proposal for a human curator to consider, not an instruction you
  are authorized to execute.
- Do **not** include a `fix_op` field.
- Do not rewrite `quote_text` or `deidentified_text` yourself, and do not invent findings
  outside the seven check ids above.

Context bundle:
{context_bundle_json}
```

## 5. Portfolio check

Run once per race, after the mechanical and judgment passes for every topic in that race are in
hand. This is a **skew audit**, not a balancing instruction (principles §8: "process neutrality
with a skew audit" — never engineer outcome balance).

- **Compute per-candidate topic coverage**: for each candidate in the race, the count (and list)
  of topics where they have a live (`readrank_selected`) quote that passed responsiveness, versus
  the total topics in the race.
- **Compare across candidates.** If one candidate is live on most of the race's topics while
  another is live on few or none — i.e. coverage is lopsided rather than roughly comparable —
  that asymmetry is worth surfacing.
- **Emit one finding** at `level: "portfolio"`:
  - `check_id`: `coverage-skew`
  - `severity`: `medium`
  - `fix_class`: `decision-required`
  - `principle`: `"equal curation effort across candidates and topics"`
  - `what`: describe the asymmetry concretely — e.g. "Candidate A is live on 8/9 topics;
    Candidate B is live on 2/9, absent from housing, climate-change, immigration, ..."
  - `suggested_fix`: frame it as **a signal to investigate, not a defect to correct** — the
    skew may be a true reflection of one candidate being more on-record or more articulate
    (which voters should see, per §8), or it may be an effort gap in the curation pass that
    should get a second look. Never suggest sourcing a quote *in order to* balance the
    count; only ever suggest checking effort/coverage was applied evenly.
  - `race_id`: the race id. `topic_key` and `quote_id`/`candidate` are left null — this
    finding is about the race's topic portfolio as a whole, not a single quote or topic.
- If coverage is roughly comparable across candidates, emit no `coverage-skew` finding for
  that race.

# Editorial discipline for quotes

> **Principles live in** `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` — the canonical *why*
> behind selection, editing, sources, anonymity, the Compass coupling model, and accountability.
> This file is the editing *mechanics*. If the two disagree, the principles doc wins and this
> file should be updated.

The goal: make the speaker's position **clear** without **editorializing**. Every cut must be
honest and auditable against the full source passage. The user owns every wording decision —
present options and a recommendation; never decide for them.

## Splitting a long quote

- **Split at the speaker's own pivots** ("Secondly…", "the final piece…", "and then…"). Following
  their structure isn't editorializing; imposing your own is.
- **One claim per quote.** A quote that makes two arguments should usually become two quotes. This
  reads cleaner and lets each stand or fall on its own.

## Trimming spoken text

- **Marking policy (journalistic standard).** Silently remove pure verbal tics ("um", "you know",
  repeated "just"), stutters, and false starts / self-corrections — *no mark needed.* Mark any
  removal of **substantive words or a span** with `…`. Total transparency is preserved by the full
  source passage / show-your-work view, so the inline quote can read clean without being dishonest.
- **Bracket any inserted or changed word: `[work with]`, `[it]`, `[the community]`.** If the
  speaker didn't say it, it must be visibly yours. Use only to bridge grammar, never to change meaning.
- **Never cut a load-bearing qualifier.** Anything that modifies the *certainty, conditionality, or
  scope* of the position stays ("I support X **but only if** Y"). Only pure filler is cuttable — when
  in doubt, keep it.
- **Ellipsis-density is a quality signal.** If a quote needs many `…` to cohere, the source span is
  too scattered. The fix is not to paraphrase or pad — **(a) split into separate sentences, each from
  a contiguous run**, and **(b) reclassify self-corrections/restatements as silent cleanup.** If it
  still needs a thicket of marks, it's carrying too many sub-claims — drop one. *E.g. "…block housing
  … filed by … labor unions to extract … project labor agreements" → "…block housing. … They're filed
  by labor unions to extract what they call project labor agreements."*
- **Never reorder** the speaker's points.
- **Repair broken spoken sentences** (merge a fragment like "Our money is just not okay" into the
  prior clause) **only when meaning is unchanged.**

## Punctuation (spoken text is unpunctuated — you supply it)

Every mark is interpretive and carries the same "never change meaning" cap as any edit. Punctuate
to reflect the speaker's actual delivery, not to add polish.

- **Respect punctuation the transcript already has** when it reflects delivery — don't "upgrade" a
  plain period into a dramatic em dash.
- **Neutrality hierarchy — least-interpretive mark that works:** periods/commas → colon/semicolon
  (only when that explanatory/balancing relationship is genuinely there) → **em dash only for a real
  self-interruption or aside the speaker actually made.** Never use a dash to manufacture emphasis or
  to fuse two of their sentences into one. When tempted, use a period.
- **No added exclamation points.** Question marks only for actual questions.
- **Keep faithful comma-splices** that mirror a deliberate spoken rhythm ("they call it sprawl, I
  call it the California dream") — don't normalize them into periods.
- **Normalize numbers** ("300,000", "70%"). If a sentence boundary changes what a clause modifies,
  check the audio and note the call in the `editor_note`.

## Faithfulness judgment calls

- **Keep policy / administration attribution in the canonical quote.** Naming the administration
  or policy responsible ("the Newsom administration", "as governor") is accountable on-the-record
  speech — keep it in `quote_text`.
- **Cut genuine personal attacks** (a person's character, family, fitness). That's the line:
  attacking a *policy or office* stays; attacking a *person* goes.
- **When another speaker supplies a word**, attribute it honestly — put the borrowed term in
  quotes (`'abortion tourism'`) and/or "what some have called …", and note the interjection rather
  than silently merging it into the speaker's mouth.

## Two layers: canonical vs. blind (`deidentified_text`)

Read & Rank shows quotes **blind** — the citizen ranks them without knowing who spoke. So every
quote has two renderings, and **producing the blind version is a standard step, not an occasional
override:**

- **`quote_text` — canonical / revealed.** Keeps names and speaker self-identification. Shown
  everywhere post-reveal (reveal card, Compass, Essentials).
- **`deidentified_text` — blind card only.** The canonical quote **plus extra de-identification:**
  - **Strip speaker self-identification** — "as governor", "in my district", touting their own
    record. This leaks *who is speaking* and must go.
  - **Depersonalize named people** in policy critiques — "Newsom" → "[the current administration]".
    (Blind card only; the canonical quote keeps the name.)
  - Extra redactions still obey the substance cap (never change the position) and are still honestly
    marked (`…`, `[brackets]`). They relax back to the canonical quote at reveal.
- If de-identifying would change the *position itself*, the quote isn't usable blind — pick another.

## Before storing

Read the finalized quotes back to the user verbatim and get explicit sign-off on each. The wording
that goes in the DB is the wording the public will see.

## Editor note (required)

Every quote needs a one-to-few-sentence `editor_note`: **why this quote** (what stance it
captures, why it's the clearest evidence) and, **if you edited it**, exactly what you changed
and why (trimmed filler with `…`, bracketed an inserted word, condensed two sentences). If it's
verbatim, say "verbatim, no edits." This is the defense of the wording — write it for a skeptical
reader, not as a note-to-self.

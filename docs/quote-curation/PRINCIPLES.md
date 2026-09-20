# Quote curation principles

> Mechanics that implement this: publish-quotes/EDITORIAL.md (editing/de-id) and audit-quotes/CHECKS.md (checks). If a mechanics file disagrees with this document, this document wins.

This is the canonical *why* behind how a candidate's quote gets into Read & Rank, how it is
edited, and how it is judged. The mechanics files above encode these principles as concrete
checks, regexes, and editing rules; when a mechanic and a principle pull apart, fix the
mechanic to match this document, not the reverse.

## Selection philosophy

A Read & Rank quote exists to let a citizen compare candidates on one question, blind to who is
speaking. That single purpose drives every other rule in this document: a quote is chosen
because it is a genuine, forward-looking answer to a question voters care about, not because it
is colorful, quotable, or favorable to one side. Curation is a selection problem before it is an
editing problem — most of the discipline lives in choosing which sentence, from which source,
answers which question, and the trimming rules in EDITORIAL.md only clean up what selection
already got right. A quote that requires clever editing to *become* on-point was probably the
wrong quote to pick; when in doubt, go back to the source and find the passage that already
answers the question cleanly. Selection also has to survive contact with an adversarial reading:
assume a skeptical reader will re-fetch the source and re-derive the position, and choose only
quotes that hold up under that scrutiny.

## Coupling model

A quote does not stand alone — it is curated evidence for a candidate's position on a topic, and
that position is a numeric *stance value* on the Compass's five-chair spectrum for that topic
(see [Five chairs](#five-chairs)). The coupling is to the **value under the topic's current
season/revision**, not to the topic in the abstract: per the compass revision model (ADR 0004)
and the season model (ADR 0005/0006), a topic's question text and its five-rung ladder can be
rewritten, and a season pins each topic to a specific version of that ladder for the citizens
answering it right now. A quote written against one wording of a spectrum is not automatically
valid evidence for a different wording of it, even if the `topic_key` is unchanged — `topic_key`
identifies the *topic*, not the specific stance ladder a quote was read against.

The operational consequence is that **a stance-value change must move its public reasoning with
it.** If a candidate's synthesized Compass value on a topic shifts — because a ladder revision
re-scored the rungs, because a season re-pinned the topic to a new version, or because new
evidence changed the read — the quotes surfaced as the reasoning behind that value have to be
re-checked against the new wording, not left in place on the assumption that a topic-level match
is enough. This is what the audit's `coupling-in-tension` check exists to catch: a quote that
pulls against the direction of the candidate's current value is a signal to resolve, not evidence
to silently discard or silently keep. A quote can legitimately *reinforce* the value (illustrate
the numeric stance directly) or *elaborate* on it (answer the question on a different
sub-dimension, still valid) without being in tension; only a quote that pulls the other way needs
a decision.

## Ranking question

Every topic's quotes are ranked against a specific question — the **ranking question** — and it
is the ranking question, not the topic label, that a quote must answer to be usable. In most
races this is simply the Compass's canonical `question_text` for that topic. But a race can carry
a per-race override (`stance.question_text` when `stance.override_active` is true) that sharpens
the race's real question without changing the underlying axis — the override must still engage
the *same* dimension the Compass question sets, must stay blind (name no candidate), and must
read as the race's actual question tightened, not a different question wearing the topic's name.
An override that drifts off-axis is a defect in the override itself (`question-override`), fixed
by correcting the override or re-homing the topic, never by loosening what counts as on-question.

This is why the on-question gate resolves against the **ranking question**, never against the
topic in isolation: "touches the subject" is not the bar. A quote can be squarely about a topic's
subject matter and still fail to engage the axis the ranking question sets — that is a different
defect (`off-question`) than simply being off-topic, and it is a hard gate, not a preference,
because comparability across candidates depends on every surfaced quote answering the same
question.

## Anonymity and the blind card

Read & Rank shows quotes to citizens **blind** — without revealing who spoke — so that a ranking
reflects the position, not the speaker's name, party, or reputation. Every quote therefore carries
two renderings, and producing the blind one is a standard curation step, not an occasional
override: `quote_text` is the canonical, revealed wording (kept everywhere post-reveal), and
`deidentified_text` is that same quote with additional redaction of anything that would leak who
is speaking — self-identification ("as governor," "in my district"), named third parties in a
policy critique, and partisan or side tells ("Democrat," "my party") that would give away which
side of a two-way race is talking.

The anonymity principle only works if the redaction is **honest**: it must be done by marking
cuts and substitutions (`…`, `[brackets]`), the same discipline EDITORIAL.md requires everywhere
else, never by paraphrasing or summarizing the position into different words. A blind card that
silently rewrites the candidate's words to hide their identity has replaced anonymity with
invention, which is a worse defect than the leak it was trying to fix. And if removing the
identifying language would change the position itself — not just who is saying it — the quote is
not usable blind at all; pick a different quote rather than force a de-identification that
distorts the substance.

## Accountability

Read & Rank exists to let voters hold officeholders and candidates accountable for their record
and their proposals — which means quotes that name a policy, a program, or "the current
administration" are accountable on-the-record speech and belong in the canonical quote exactly as
said. Naming who is responsible for a policy is not an attack; it is the accountability the tool
is for.

The line accountability does not cross is the person. A quote whose operative content targets a
candidate's or official's character, family, or personal fitness — rather than their policy,
their office, or an institution — has left position-taking and become a personal attack, and it
does not belong in Read & Rank regardless of how pointed or well-sourced it is. Critiquing a
policy or an office is allowed even when combative; critiquing a person is not. When a quote
attacks a person but a surviving, on-position remnant can be trimmed out of it, trim it down
rather than discard the whole quote; when the attack is the entire quote, it has no usable
remainder and is dropped. The same accountability lens applies to borrowed language: when a
candidate uses someone else's term ("what some have called 'abortion tourism'"), attribute it
honestly rather than letting it read as their own coinage — the point is always to hold the
speaker accountable for what they actually said, not to launder or to overstate it.

## Sourcing

A quote is only as trustworthy as its source, and sourcing discipline runs on two axes:
independence from the candidate's own framing, and verifiability against what was actually
published. On the first axis, sources form a tier ladder by how independent they are of a
questioner the candidate is performing for — a moderated debate or an unscripted interview sits
above a self-selected campaign statement, which sits above a source shaped entirely by a
sympathetic or adversarial questioner. Lower-tier sources are not disqualified, but they are
weaker evidence and are flagged for a second look rather than treated as equivalent to a
higher-tier one.

The second axis is verification: **a quote must verify against its cited, ingested source** — the
words have to actually appear there, spoken or written by the candidate, not merely be consistent
with something they might have said. For a written medium (a platform page, an op-ed, a
questionnaire answer), the standard is the same verbatim discipline EDITORIAL.md applies to
spoken transcripts: an actual sentence the candidate wrote, not a curator's paraphrase or a
summarized bullet list dressed up as a quote. And **not every page that mentions a candidate is a
source at all.** Aggregators like ontheissues.org or Wikipedia restate or paraphrase something
said elsewhere and are pointers to an original, not the original itself — cite the underlying
speech or statement, not the aggregator. Quiz and questionnaire comparison sites (isidewith.com
and similar) publish canned option text nobody said and third-party or AI-generated rows
alongside them; nothing on such a page is quotable, from any row, because there is no utterance to
descend to. A legislative scorecard publishes a vote and a rating, never a spoken or written
position, so a quote attributed to one came from somewhere else or from nowhere. Distinguishing
these classes matters because their remedies differ: an aggregator sends you to find the real
original; a quiz site or scorecard means the quote has no real source to find and should be
dropped or re-sourced entirely.

## Differentiation

Among quotes that already pass the responsiveness gate, a rankable stance is the one that shows
**how** the candidate would actually pursue a goal — the concrete, contestable **policy lever**: the
specific instrument they would use. Build shelters; enforce the encampment ordinance; expand
treatment and services; appoint a chief committed to a stated aim; create a named program; mandate
acceptance of a specific document; triple housing construction — each names a *means* a plausible
opponent could choose differently, and it is that choice a citizen is ranking. "Who wouldn't want
safe, affordable housing?" gives a voter nothing to distinguish one candidate from another — and
neither does the same goal restated as a number. Differentiation is what makes a ranking a comparison
of *approaches* rather than a popularity contest over shared values everyone already holds.

Three things resemble a mechanism but are not, and do not on their own make a quote rankable:

- a **goal** — the agreeable end-state ("reduce homelessness", "make housing affordable") essentially
  every candidate shares;
- a **target or metric** — a number or deadline bolted onto the goal ("cut encampments 50% by 2028");
  it quantifies the end but still names no means;
- a **vague direction** — a gesture at action with no instrument ("direct our dollars to programs
  that work", "deliver immediate treatment", "work with the county", "put an end to the
  finger-pointing").

The defect this principle names (`non-differentiating-goal`) is therefore simply: **the quote names
no concrete policy lever.** A quote that offers only a goal, a target, or a vague direction fails it —
being *contested* or *specific about the outcome* does not rescue it, because the contest a ranking
needs is a contest over the *means*, and none is on offer. Such a quote is not rankable on its own:
**flag it for human judgment, and surface it only if a curator affirms it carries a real,
distinguishing position.** Prefer, and go looking for, a lever-bearing quote first; but a blanket
gate would erase legitimate, plainly-worded stances and shrink coverage, so the human — not the rule —
makes the final call on a mechanism-less quote.

A corollary for selection and extraction: the lever usually sits in the sentence *next to* the goal,
so keep them together. "We must build much more housing. That includes housing at all income levels —
deed-restricted affordable, market-rate, social housing, and shelters." states the mechanism in the
second sentence; a quote that keeps only the first has thrown the differentiation away. Prefer the
coherent two-to-three-sentence passage that carries the goal *and* its lever over the atomized goal
alone.

## Responsiveness and absence

A quote's first duty is to actually answer the topic's ranking question (see [Ranking
question](#ranking-question)) — not to be about the same subject, not to be a good soundbite, but
to engage the specific axis the question sets. When a quote's most natural home turns out not to
be the topic or race it was gathered under — the axis it actually engages lives elsewhere — the
right move is to re-home it to the topic or question it genuinely answers, rather than to force
it to count where it was found or to discard evidence that is simply filed in the wrong place.

The harder case is a candidate who has never taken a forward-looking position on a topic at all —
only a voting or governing record, with no stated reasoning a citizen could rank. That candidate
is **absent** from the topic, and absence must stay visible as absence. It is never acceptable to
launder a candidate's record into an invented forward-looking position they did not actually
state, no matter how confidently their record predicts what they would say — that is exactly the
`not-forward` defect, and it fabricates a position under the candidate's name on a page voters
are trusting to reflect what was actually said. An honest "no rankable quote here" is better
curation than a synthesized one.

## Process neutrality

The curation process itself must stay blind to outcome — it must never put a thumb on the scale
by way of **which** topics a candidate happens to appear on. A candidate who is more on-record, or
more articulate, will legitimately end up live on more topics than a quieter opponent, and that
asymmetry is a true reflection of the record, not a defect. What must not happen is an uneven
curation *effort*: one candidate combed for quotes on every topic while another is skipped or
under-searched, so that the resulting coverage reflects the curator's attention rather than the
candidates' actual records.

This is why coverage is checked across a race rather than left to accumulate quote by quote: the
audit's coverage-skew portfolio pass exists to catch a candidate who is over- or under-represented
on topics relative to their peers, and to prompt a second look at whether curation effort was
applied evenly — never to prompt sourcing a quote *in order to* even out the count. Neutrality is
in the process (equal effort across candidates and topics), not in the outcome (equal counts).

## Five chairs

Every topic's spectrum is built from five distinct **chairs** — five real, ordered positions a
politician can be seated in along one axis, from one pole to the other, not five shades of
intensity on a single position. Chair 1 through chair 5 must each correspond to a real, held
policy stance a real officeholder has actually taken, and a citizen ranking quotes blind is
implicitly being asked to distinguish which chair each quote's speaker is sitting in. This framing
is what makes `stance.chairs` — the spectrum's labeled anchor points — the reference a curator
uses to judge whether a quote reinforces, elaborates on, or sits in tension with a candidate's
synthesized value: without knowing what each end of the spectrum actually means, "reinforcing"
and "in tension" cannot be told apart. A topic whose five chairs collapse into one position
restated five ways, or whose poles are strawmen nobody actually holds, has broken the ranking
before a single quote is even chosen — the design work of naming five real chairs a citizen can
tell apart is what curation depends on, not just something the topic-authoring process happens to
also care about.

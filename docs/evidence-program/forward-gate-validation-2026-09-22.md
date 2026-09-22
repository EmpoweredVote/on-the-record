# Forward-vs-record gate — validation record (2026-09-22)

Human-gated validation of the `forward_looking` judge dimension and the
`judge:record-not-forward` disposition gate (`FORWARD_MIN = 0.5`).

## Method

Ran the live judge over two sets:

- **Real gold** — 12 human-labeled rows from `inform.evidence_items`
  (`review_status IN ('accepted','rejected')`, judge-relevant subset:
  accepted + goal-only-rejected), pulled read-only.
- **Explicit probes** — 3 clear records + 3 clear forwards drawn from the spec
  (Bass "declared a state of emergency…", the #13 "tax credit under
  Schwarzenegger" record; Nithya "reduce 50% by 2028", "triple annual housing
  construction", the #14 "what I want to deliver" forward).

Two models: **deepseek** (the current pipeline judge) and **gemini-flash** (the
intended judge after the pending swap). For each item the run recorded the
parsed `forward_looking` score, whether the model actually emitted the
`forward_looking` key in its JSON, and whether `disposition.decide` appended
`judge:record-not-forward`.

## Results

| Check | deepseek | gemini-flash |
|---|---|---|
| `forward_looking` key emitted | 18/18 | 18/18 |
| Record probes flagged | R2=0.0 FLAG, R3=0.0 FLAG, R1=0.9 green | R2=0.0 FLAG, R3=0.0 FLAG, R1=0.8 green |
| Forward probes flagged | 0/3 (all 1.0) | 0/3 (all 1.0) |
| Gold accepted now record-flagged | 0/2 | 0/2 |
| Gold goal-only-rejected record-flagged | 0/10 | 0/10 |

## Findings

1. **Key-emission risk does not materialize.** Both models returned the
   `forward_looking` key on every item (18/18). The parser's worst-case default
   of `0.0` for a missing key — the one live-run concern the final review raised
   — is not triggered by either model in practice.
2. **Clear records flag on both models.** R2 (declared-emergency recitation) and
   R3 (#13 "I did the first tax credit under Schwarzenegger") both score `0.0`
   and flag `judge:record-not-forward`. This is the case the gate was built for.
3. **Clear forwards stay green.** All three forward probes score `1.0`; no flag.
4. **R1 is a correct borderline.** "Today, on my first day in office, we hit the
   ground running, with a sea change in how the city tackles homelessness"
   scores 0.8–0.9 and does not flag. It states an approach, not a past
   accomplishment, and this matches the human green label already on that gold
   row. Not a gate miss.
5. **No over-flagging; axes independent.** Accepted gold and goal-only-rejected
   gold are not record-flagged. Goal-only quotes are forward-but-vague — the
   `mechanism` gate's job, not this one — so the two gates stay independent, as
   designed.
6. **Threshold is robust.** Scores are bimodal (records ≈ 0.0, forwards ≈
   0.8–1.0). `FORWARD_MIN = 0.5` sits in a wide empty gap, so it is insensitive
   to small score movement.

## Decision

Keep `FORWARD_MIN = 0.5`. No tuning needed. The gate is validated on both the
current (deepseek) and intended (gemini-flash) judge models, confirming it is
model-agnostic. Ready to merge.

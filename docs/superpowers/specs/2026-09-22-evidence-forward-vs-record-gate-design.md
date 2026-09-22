# Forward-vs-Record Gate — a judge dimension that flags past-record recitations

**Status:** Draft for review (brainstormed with Chris 2026-09-22)
**Repo:** on-the-record only — `src/evidence/models.py`, `judge.py`, `disposition.py`, `pipeline.py`
(+ `judge_jev.py` for adapter consistency), tests. No schema change; no ev-accounts change.
**Follows:** the judge A/B pivot — quality is a wash across judge models, and the real ceiling is
**structural**. This is the first structural gate: **forward-looking stance vs. past record.**

## Why

The evidence layer captures **stances** — what a candidate *would do*. A **track-record** recitation
(*"I made a commitment… I did the first tax credit under Arnold," "On my first day I declared a state of
emergency," "we've moved thousands off the streets," "Mayor Bass has doubled units in the pipeline"*)
states *what they did*, not a forward position. These currently **green**, because they name concrete
actions and so score high on `mechanism`. The `mechanism` gate cannot catch them — a record names
concrete actions too. In the judge A/B this was the stubborn #13 miss (mechanism 0.95, but Chris
rejected it as "running on record"). We need a **separate** signal.

## Design — a new judge dimension `forward_looking`

Mirror how `mechanism` was added:

- **`JudgeScores.forward_looking: float`** — `1.0` = a forward-looking stance/proposal (what the
  candidate *would do* / believes should happen); `0.0` = a recitation of past record / accomplishment
  (what they *did*). Required field, positioned before `notes`.
- **`GateResults.judge_forward_looking: Optional[float] = None`**.
- **`judge.py`** — add `forward_looking` to `_INSTRUCTIONS` with a crisp definition + examples
  (forward: "I will…", "we should…", "as mayor I would…"; record: "I did…", "we have…", "on day one I
  declared…", a stat of results). `build_judge_prompt` unchanged shape; `parse_judge` reads
  `forward_looking` via `_clamp(d.get("forward_looking"), 0.0)` — **default 0.0**, so a total parse
  failure (already worst-defaulting the other fields) also flags record, consistent with the existing
  pattern; the prompt always asks for it, so a valid reply provides it.
- **`disposition.decide`** — add `FORWARD_MIN = 0.5` (tunable) and, alongside the other judge checks:
  `if gates.judge_forward_looking is not None and gates.judge_forward_looking < FORWARD_MIN:
  reasons.append("judge:record-not-forward")`. So a record recitation **flags** (never greens),
  regardless of its mechanism score. **Flag, not drop** (Chris's call) — visible + human-confirmable;
  routing record to a proper votes/actions evidence type is later work.
- **`pipeline._evaluate_quote`** — pass `judge_forward_looking=js.forward_looking` into `GateResults`
  (one line; both `run_source` and `run_transcript_source` already build `js` via `_evaluate_quote`).
- **`judge_jev.py`** — add a `forward_looking` `Score` question (ordered criteria: record → forward) so
  the eval adapter still returns a complete `JudgeScores` (Jev is shelved, but keep the harness usable).

**Independent of `mechanism`, and model-agnostic.** A record can have high mechanism (#13); the gate is
a distinct axis. It runs under whatever judge model is active (deepseek today, gemini-flash after the
pending swap) — a structural gate, per the pivot.

## Threshold

`FORWARD_MIN = 0.5` to start — flag a clearly-record statement, but let a **blended** quote that is
dominantly a forward proposal (even if it references past action) pass. The plan validates and tunes it
against known cases: #13 and Bass's "declared emergency / doubled units" should score low (< 0.5); the
forward accepts (#14 "what I want to deliver… 911 picks up quickly, unarmed crisis responders", #7
"advocate for an uncapped film tax credit", #5 "I'm going to clear the brush whether state or city")
should score high (≥ 0.5).

## Tests (offline, mock providers)

- `parse_judge`: parses `forward_looking`; a full worst-default (json failure) yields `forward_looking
  == 0.0`; a valid reply round-trips the value.
- `disposition.decide`: a quote with high mechanism/tag/context/low dispute but `judge_forward_looking
  < FORWARD_MIN` → FLAGGED with `judge:record-not-forward` (this is the #13 case); with
  `judge_forward_looking ≥ FORWARD_MIN` and the rest passing → GREEN. Existing disposition tests stay
  green (they pass `judge_forward_looking=None` → gate inert, or are updated to set it forward).
- `judge_jev`: returns a `JudgeScores` including `forward_looking` (stubbed client).
- Full suite green.

## Validation (human-gated, small)

Run the judge (current deepseek, or gemini-flash if the swap has landed — the gate is model-agnostic) on
the 15-item labeled gold **plus** the known record cases (#13; Bass "declared emergency / doubled
units"). Confirm: record cases now flag on `judge:record-not-forward`, the forward accepts still green,
and blended-but-forward quotes are not over-flagged. Report the forward_looking distribution and any
mis-flags; tune `FORWARD_MIN` if needed.

## Risks / decisions

- **Blended record+forward** is the hard case — `FORWARD_MIN` tuning + the "dominant orientation" framing
  in the prompt handle it; the human review (flagged, not dropped) is the backstop.
- **Default 0.0** means a malformed judge reply flags record — consistent with the existing worst-default
  flagging; harmless (such a reply already flags on tag/mechanism/dispute).
- **Incumbents recite record heavily**, so this gate will flag a lot of incumbent record-talk — that is
  correct (record is a different evidence type; their forward stances remain the quotes).
- **Interaction with the pending judge swap:** independent — the gate is a new dimension in the shared
  judge prompt/scores, works under any model.

## Success criteria

- Offline: `forward_looking` parses + defaults correctly; `decide` flags low-forward with
  `judge:record-not-forward` and greens high-forward-otherwise-clean; `judge_jev` returns it; full suite
  green.
- Live (gated): record cases flag, forward accepts green, blended-forward not over-flagged.

## Execution notes

- Models: OpenRouter (extractor `haiku-or`, crosschecker `gemini-flash`, judge `deepseek` today).
  Run tests + validation with the MAIN checkout `.venv/bin/python`; `--env-file <ev-accounts>/backend/.env`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

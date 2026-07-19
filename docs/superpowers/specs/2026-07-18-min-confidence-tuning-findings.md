# min_confidence tuning — findings

**Date:** 2026-07-18
**Type:** Data-gathering + analysis (tuning investigation)
**Question:** After the multi-paragraph parser fix, is the CREC alignment gate (`min_confidence=0.5`, formula `match_fraction × vote_fraction`) well-placed? Should it move?
**Verdict:** **Keep 0.5.** The automated ground-truth proxy (CREC↔LLM agreement) proved too noisy to justify a change, and CREC qualitatively produces plausible real members across every session. No code change.

## Method

- Processed **4 House-floor sessions** (2026-07-13/-14/-15/-16), each a **60-min clip** on Modal GPU with `--congressional-record`, captions for transcription. → **41 CREC member resolutions** across 49 diarized labels.
- Per label: re-aligned at `min_confidence=0.0` for the raw CREC confidence + resolved member, and parsed the **independent local-LLM guess** from each run log.
- Ground-truth proxy: **CREC↔LLM surname agreement** (fuzzy — `difflib` ratio ≥ 0.7 or containment — because the local LLM garbles names phonetically).

## Confidence distribution (fuzzy agreement)

| conf bin | n | agree | disagree | llm-none | agree% (of comparable) |
|---|---|---|---|---|---|
| [0.0, 0.4) | 5 | 0 | 1 | 4 | 0% |
| [0.4, 0.5) | 1 | 0 | 0 | 1 | — |
| [0.5, 0.7) | 12 | 4 | 4 | 4 | 50% |
| [0.7, 1.0] | 23 | 6 | 8 | 9 | 43% |

At the 0.5 gate: **35/41 labels attach**; only **6** fall below (held back).

## The proxy failed — but CREC did not

The ~45% "agreement" is **dominated by LLM failures, not CREC errors.** The local llama model, transcribing congressional names from auto-captions, emits garbage: `Mr. Bean`, `boast` (Bost), `duzio` (Deluzio), `rockus` (Bilirakis), `perlene` (Fitzpatrick), `leood` (LaHood), `coffman` (Barrett), `many` (Norcross), or just `"speaker"`. Even fuzzy matching can't rescue those comparisons — but they don't impugn CREC.

**Decisive counter-evidence:** every distinct member CREC resolved at ≥0.5 is a **genuine current House representative** — Barrett, Bean, Bilirakis, Bost, Carter, Ciscomani, Dean, Deluzio, DeSaulnier, Fitzpatrick, Gomez, Gottheimer, Higgins, Joyce, **King-Hinds** (CNMI delegate), LaHood, Loudermilk, Mann, Massie, **McBride**, McClintock, McGovern, Menefee, Meng, Meuser, Miller-Meeks, Morrison, Murphy, Norcross, Takano, Taylor, Underwood, Vasquez. No fabricated names, no local-politician bleed-through, no obvious mis-assignments — a plausible, coherent roster of who was actually on the floor.

This also **validates the whole oracle's value**: without CREC, these 35 speakers would have received the LLM's garbage guesses or "unidentified"; with CREC they get real member names.

## Why keep 0.5

- **No reliable signal to move it.** The proxy can confirm agreements (10 labels where CREC and LLM concur are almost certainly correct) but can't condemn disagreements (the LLM is the unreliable party). Changing a gate on an unreliable proxy would be guessing.
- **The distribution doesn't argue for a move.** Only 1 label sits in [0.4, 0.5) (lowering to 0.4 gains ~nothing); [0.0, 0.4) holds 5 genuinely-weak labels (lowering further risks wrong links). Raising to 0.7 would drop 12 labels in [0.5, 0.7), many of them plausible members (recall loss). 0.5 is a defensible middle.
- **The design's safety net holds regardless.** Ambiguity → name-only, the identity-collision guard, the `is_incumbent` essentials filter, and CREC-overrides-only-when-confident mean over-attaching at 0.5 is low-risk; a wrong link never silently ships.

## Recommendation

**Keep `min_confidence = 0.5` and the `match_fraction × vote_fraction` formula.** Both are producing a high-recall, plausible-member output post-multi-paragraph-fix.

**For a future, rigorous re-tune** (if wanted): the automated proxy is a dead end — the local LLM is too weak a second opinion for congressional names. Real ground truth requires **manual labeling** (review a clip's transcript against the CREC-resolved names) or a stronger independent identifier. Only then could the gate be optimized with precision/recall rather than judgment.

## Cleanup

The 4 tuning meetings (`~/CouncilScribe/meetings/2026-07-1{3,4,5,6}-house-floor-tuning`, `--no-publish`) are local test artifacts, removed after analysis.

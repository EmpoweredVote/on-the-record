# VibeVoice GPU Cost Sweep Design

## Goal

Measure the real cost and performance of VibeVoice-ASR on Modal using the
same cached 2.87-hour council meeting on `L40S`, `A100-40GB`, and
`A100-80GB`.

## Architecture

Add three GPU-specific Modal functions that share one inference
implementation but write isolated manifests under:

```text
/vol/vibevoice-gpu-sweep/<meeting_id>/<gpu-key>/inference.json
```

A local runner starts the three remote functions concurrently inside one
Modal app session. Each function processes chunks sequentially so every
result measures one GPU and has unambiguous runtime and cost attribution.

## Measurements

Each result records:

- requested and actual GPU
- success or failure, including OOM details
- total inference time and per-chunk generation time
- peak allocated and reserved CUDA memory
- audio duration, chunk count, turns, speakers, and parse errors
- temporal and embedding reconciliation counts
- current Modal list-price estimate

The sweep reuses the existing VibeVoice model, revisions, preprocessing,
chunking, and parser. It does not use production inference caches.

## Outputs

The runner writes:

```text
bench/snapshots/2026-06-14-vibevoice-gpu-sweep/
  results.json
  README.md
```

Raw generated transcript text remains only in Modal diagnostic manifests
and is not copied into the repository.

## Error Handling

One failed GPU does not cancel the other runs. OOM and other failures are
reported as benchmark results. The runner exits nonzero only if dispatch
itself fails before it can write a report.

## Validation

- Unit tests cover GPU-key normalization, cost calculations, report
  rendering, and isolated dispatch.
- The three long-meeting jobs run concurrently.
- Saved results identify the code/model revisions and are small enough to
  commit safely.

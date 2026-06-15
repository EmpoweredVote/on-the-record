# VibeVoice GPU Sweep: 2026-02-04-council

| Requested | Actual | Status | Inference | Reconcile | Cost | Peak reserved | Turns | Speakers | Parse errors | Temporal | Embedding |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| L40S | NVIDIA L40S | ok | 1791.0s | 21.2s | $0.9754 | 30.19 GiB | 752 | 37 | 52 | 9 | 22 |
| A100-40GB | NVIDIA A100-SXM4-80GB | ok | 1975.2s | 15.2s | $1.1549 | 30.19 GiB | 753 | 37 | 53 | 9 | 22 |
| A100-80GB | NVIDIA A100-SXM4-80GB | ok | 1981.0s | 22.2s | $1.3798 | 30.19 GiB | 756 | 38 | 53 | 9 | 22 |

## Notes

- Costs use the requested GPU rate from Modal list prices captured on 2026-06-14, plus L4 reconciliation time. Actual billing is authoritative.
- Modal may upgrade A100 requests to 80 GB hardware without changing the requested GPU rate. Therefore requested and actual GPU names are both shown.
- Minor turn and speaker-count differences can occur across hardware; this is a cost/performance sweep, not a diarization-quality verdict.

## Interpretation

- **L40S was the lowest-cost successful option:** $0.9754 estimated total, about 16% below the A100-40GB billed request and 29% below A100-80GB.
- Peak CUDA reservation was **30.19 GiB** on every run, so the model fits inside the L40S's 48 GiB VRAM with meaningful headroom.
- The `A100-40GB` request was automatically placed on an actual A100 80GB. Its cost estimate uses the requested 40GB rate, consistent with Modal's documented automatic-upgrade behavior.
- L40S was also fastest in this simultaneous run, but one run per GPU is not enough to characterize performance variance.

## Recommendation

Use **L40S** as the preferred VibeVoice inference GPU and retain **A100-80GB** as a compatibility fallback. Run a second sweep only if tighter runtime confidence is needed.

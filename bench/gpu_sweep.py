"""Pure helpers for VibeVoice Modal GPU cost sweeps."""

from __future__ import annotations

from typing import Any


GPU_PRICES_PER_SECOND = {
    "L40S": 0.000542,
    "A100-40GB": 0.000583,
    "A100-80GB": 0.000694,
}


def gpu_key(gpu_name: str) -> str:
    """Return a stable artifact key for requested or actual GPU names."""
    normalized = gpu_name.upper().replace("_", "-")
    if "L40S" in normalized:
        return "l40s"
    if "A100" in normalized and ("80GB" in normalized or "80 GB" in normalized):
        return "a100-80gb"
    if "A100" in normalized and ("40GB" in normalized or "40 GB" in normalized):
        return "a100-40gb"
    raise ValueError(f"Unsupported GPU name: {gpu_name}")


def estimate_gpu_cost(requested_gpu: str, elapsed_seconds: float) -> float:
    """Estimate Modal GPU cost from current per-second list pricing."""
    return elapsed_seconds * GPU_PRICES_PER_SECOND[requested_gpu]


def render_markdown(meeting_id: str, results: list[dict[str, Any]]) -> str:
    """Render a compact human-readable GPU comparison."""
    lines = [
        f"# VibeVoice GPU Sweep: {meeting_id}",
        "",
        "| Requested | Actual | Status | Inference | Reconcile | Cost | Peak reserved | "
        "Turns | Speakers | Parse errors | Temporal | Embedding |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    failures = []
    for result in results:
        if result["status"] == "ok":
            lines.append(
                "| {requested_gpu} | {actual_gpu} | ok | {seconds:.1f}s | "
                "{reconcile:.1f}s | ${cost:.4f} | {peak:.2f} GiB | "
                "{turns} | {speakers} | "
                "{parse_errors} | {temporal} | {embedding} |".format(
                    requested_gpu=result["requested_gpu"],
                    actual_gpu=result["actual_gpu"],
                    seconds=result["inference_seconds"],
                    reconcile=result["reconciliation_seconds"],
                    cost=result["cost_usd"],
                    peak=result["peak_reserved_gib"],
                    turns=result["num_turns"],
                    speakers=result["num_speakers"],
                    parse_errors=result["parse_errors"],
                    temporal=result["temporal_matches"],
                    embedding=result["embedding_matches"],
                )
            )
        else:
            lines.append(
                f"| {result['requested_gpu']} | - | failed | - | - | - | - | "
                "- | - | - | - | - |"
            )
            failures.append(
                f"- **{result['requested_gpu']}:** {result.get('error', 'unknown error')}"
            )

    if failures:
        lines.extend(["", "## Failures", "", *failures])
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Costs use the requested GPU rate from Modal list prices captured "
            "on 2026-06-14, plus L4 reconciliation time. Actual billing is "
            "authoritative.",
            "- Modal may upgrade A100 requests to 80 GB hardware without changing "
            "the requested GPU rate. Therefore requested and actual GPU names are "
            "both shown.",
            "- Minor turn and speaker-count differences can occur across hardware; "
            "this is a cost/performance sweep, not a diarization-quality verdict.",
            "",
        ]
    )
    return "\n".join(lines)

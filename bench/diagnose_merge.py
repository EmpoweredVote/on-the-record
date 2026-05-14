"""Diagnose why the speaker-merge feature is a no-op on real meetings.

Loads the saved embeddings.json from local production runs (no GPU needed),
computes the full pairwise cosine similarity matrix between diarized
speakers, and reports what threshold would actually trigger merges.

Run from repo root:
    python bench/diagnose_merge.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.spatial.distance import cosine


HOME = Path.home()
MEETINGS = [
    HOME / "CouncilScribe/meetings/2026-02-04-council",
    HOME / "CouncilScribe/meetings/2026-02-18-council",
    HOME / "CouncilScribe/meetings/2026-02-25-council",
]


def load_embeddings(meeting_dir: Path) -> dict[str, np.ndarray]:
    """Load saved per-speaker embedding centroids."""
    data = json.loads((meeting_dir / "embeddings.json").read_text())
    # Stored as { speaker_label: [float, float, ...] }
    return {k: np.asarray(v, dtype=np.float64) for k, v in data.items()}


def load_speech_time(meeting_dir: Path) -> dict[str, float]:
    """Compute total speech time per speaker from diarization.json."""
    segs = json.loads((meeting_dir / "diarization.json").read_text())
    out: dict[str, float] = {}
    for s in segs:
        lbl = s["speaker_label"]
        out[lbl] = out.get(lbl, 0.0) + (s["end_time"] - s["start_time"])
    return out


def pairwise_similarities(emb: dict[str, np.ndarray]) -> list[tuple[float, str, str]]:
    """All unordered (cos_sim, a, b) tuples, sorted high → low."""
    labels = sorted(emb.keys())
    pairs = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            sim = 1.0 - cosine(emb[a], emb[b])
            pairs.append((sim, a, b))
    pairs.sort(reverse=True)
    return pairs


def groups_at_threshold(emb: dict[str, np.ndarray], thresh: float) -> tuple[int, list[list[str]]]:
    """Union-find groups that would merge at this threshold."""
    labels = sorted(emb.keys())
    parent = {l: l for l in labels}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for sim, a, b in pairwise_similarities(emb):
        if sim >= thresh:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for l in labels:
        r = find(l)
        groups.setdefault(r, []).append(l)
    return len(groups), [sorted(g) for g in groups.values() if len(g) > 1]


def diagnose(meeting_dir: Path) -> None:
    name = meeting_dir.name
    print(f"\n{'=' * 70}")
    print(f"  {name}")
    print(f"{'=' * 70}")

    emb = load_embeddings(meeting_dir)
    speech_time = load_speech_time(meeting_dir)
    labels = sorted(emb.keys())
    n = len(labels)
    print(f"  {n} speakers from diarization")
    print(f"  Total speech: {sum(speech_time.values()):.0f}s")
    print(f"  Per-speaker speech: "
          f"min={min(speech_time.values()):.0f}s, "
          f"median={sorted(speech_time.values())[n//2]:.0f}s, "
          f"max={max(speech_time.values()):.0f}s")

    pairs = pairwise_similarities(emb)
    sims = [p[0] for p in pairs]
    print(f"\n  Pairwise cosine similarity ({len(sims)} pairs):")
    print(f"    max = {max(sims):.3f}    p95 = {np.percentile(sims, 95):.3f}    "
          f"p75 = {np.percentile(sims, 75):.3f}    "
          f"median = {np.median(sims):.3f}    min = {min(sims):.3f}")

    print(f"\n  Top 10 most-similar pairs:")
    for sim, a, b in pairs[:10]:
        ta, tb = speech_time.get(a, 0), speech_time.get(b, 0)
        print(f"    {sim:.3f}   {a} ({ta:.0f}s)  ↔  {b} ({tb:.0f}s)")

    print(f"\n  Speakers remaining at threshold:")
    print(f"    {'thresh':>8}  {'groups':>7}  {'merges':>7}  merge_examples")
    for t in [0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50]:
        ngroups, merged_groups = groups_at_threshold(emb, t)
        n_merges = n - ngroups
        examples = "; ".join("+".join(g) for g in merged_groups[:2])
        if len(merged_groups) > 2:
            examples += f"; +{len(merged_groups) - 2} more"
        print(f"    {t:>8.2f}  {ngroups:>7}  {n_merges:>7}  {examples}")


def main() -> None:
    for m in MEETINGS:
        if not (m / "embeddings.json").exists():
            print(f"skip (no embeddings): {m}")
            continue
        diagnose(m)


if __name__ == "__main__":
    main()

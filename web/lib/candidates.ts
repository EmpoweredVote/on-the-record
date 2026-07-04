import type { Candidate } from "./types";

export const UNLABELED = "Unlabeled";

// The bucket a candidate curates under. Blank/whitespace labels collapse to a
// single "Unlabeled" group.
export function labelOf(c: Candidate): string {
  return c.label.trim() || UNLABELED;
}

// Group candidates by label, groups sorted alphabetically. "Unlabeled" sorts
// with the rest (capital U) — good enough; curation is small.
export function groupByLabel(cands: Candidate[]): [string, Candidate[]][] {
  const map = new Map<string, Candidate[]>();
  for (const c of cands) {
    const k = labelOf(c);
    const bucket = map.get(k);
    if (bucket) bucket.push(c);
    else map.set(k, [c]);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

// Toggle the star on `id`. Only one candidate per label may be starred (it's the
// single live pick for that topic), so starring one clears any sibling in the
// same label group. Returns a new array; inputs are not mutated.
export function applyStar(cands: Candidate[], id: string): Candidate[] {
  const target = cands.find((c) => c.id === id);
  if (!target) return cands;
  const lbl = labelOf(target);
  const willStar = !target.starred;
  return cands.map((c) => {
    if (c.id === id) return { ...c, starred: willStar };
    if (willStar && labelOf(c) === lbl && c.starred) return { ...c, starred: false };
    return c;
  });
}

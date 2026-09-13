// Pure formatting helper for the House-floor draft queue row. Kept separate
// from the client component so it has real unit-test coverage (the .tsx
// components are verified only by `npm run build`).
import type { DraftListItem } from "./adminQueries";

export interface DraftRowView {
  id: string;
  date: string;
  title: string;
  duration: string;
  gateVerdict: string;
  coverage: string;
  counts: string;
}

function fmtDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function draftRowView(item: DraftListItem): DraftRowView {
  const meta = item.processingMetadata ?? {};
  const cov = typeof meta.gate_coverage === "number" ? `${Math.round(meta.gate_coverage * 100)}%` : "—";
  return {
    id: item.meeting_id,
    date: item.meeting_date ?? "—",
    title: item.title ?? "Untitled",
    duration: fmtDuration(item.duration_seconds ?? null),
    gateVerdict: meta.gate_verdict ?? "—",
    coverage: cov,
    counts: `${item.speaker_count ?? 0} speakers · ${item.named} named · ${item.linked} linked`,
  };
}

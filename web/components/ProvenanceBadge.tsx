import type { ProvenanceStatus } from "@/lib/types";

const COPY: Record<ProvenanceStatus, { label: string; title: string }> = {
  predicted: { label: "✦ AI predicted", title: "Automated — pending human review." },
  verified: { label: "✓ Verified", title: "Confirmed by a human reviewer." },
};

export default function ProvenanceBadge({ status }: { status: ProvenanceStatus }) {
  const c = COPY[status];
  return (
    <span className={`provBadge prov-${status}`} title={c.title}>
      {c.label}
    </span>
  );
}

// Map a speaker's id_method to a provenance status.
export function speakerStatus(idMethod: string | null): ProvenanceStatus {
  return idMethod === "human_review" ? "verified" : "predicted";
}

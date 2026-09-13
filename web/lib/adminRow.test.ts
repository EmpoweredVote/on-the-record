import { describe, it, expect } from "vitest";
import { draftRowView } from "./adminRow";
import type { DraftListItem } from "./adminQueries";

describe("draftRowView", () => {
  it("formats gate verdict and coverage from processingMetadata", () => {
    const v = draftRowView({
      meeting_id: "m1",
      meeting_date: "2026-09-02",
      title: "US House Floor",
      duration_seconds: 33660,
      speaker_count: 67,
      named: 52,
      linked: 42,
      processingMetadata: { gate_verdict: "review", gate_coverage: 0.63 },
    } as unknown as DraftListItem);
    expect(v.gateVerdict).toBe("review");
    expect(v.coverage).toBe("63%");
    expect(v.counts).toBe("67 speakers · 52 named · 42 linked");
    expect(v.duration).toBe("9h 21m");
  });

  it("handles missing metadata (pre-fix drafts)", () => {
    const v = draftRowView({
      meeting_id: "m2",
      meeting_date: "2026-09-10",
      title: "Pro forma",
      duration_seconds: 180,
      speaker_count: 3,
      named: 0,
      linked: 0,
      processingMetadata: null,
    } as unknown as DraftListItem);
    expect(v.gateVerdict).toBe("—");
    expect(v.coverage).toBe("—");
    expect(v.duration).toBe("3m");
  });
});

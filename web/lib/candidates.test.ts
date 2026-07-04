import { describe, it, expect } from "vitest";
import { labelOf, groupByLabel, applyStar, UNLABELED } from "./candidates";
import type { Candidate } from "./types";

function cand(over: Partial<Candidate>): Candidate {
  return {
    id: "1",
    politician_id: "p",
    meeting_id: "m",
    meeting_title: "M",
    meeting_date: "2026-04-15",
    segment_id: 0,
    start_time: 0,
    source_url: null,
    playback_kind: null,
    orig_text: "o",
    edit_text: "e",
    label: "",
    note: "",
    starred: false,
    created_at: 0,
    ...over,
  };
}

describe("labelOf", () => {
  it("falls back to Unlabeled for blank/whitespace labels", () => {
    expect(labelOf(cand({ label: "" }))).toBe(UNLABELED);
    expect(labelOf(cand({ label: "   " }))).toBe(UNLABELED);
  });
  it("trims real labels", () => {
    expect(labelOf(cand({ label: " housing " }))).toBe("housing");
  });
});

describe("groupByLabel", () => {
  it("buckets by trimmed label and sorts groups alphabetically", () => {
    const groups = groupByLabel([
      cand({ id: "a", label: "housing" }),
      cand({ id: "b", label: "budget" }),
      cand({ id: "c", label: "housing" }),
    ]);
    expect(groups.map((g) => g[0])).toEqual(["budget", "housing"]);
    expect(groups[1][1].map((c) => c.id)).toEqual(["a", "c"]);
  });
});

describe("applyStar", () => {
  it("stars the target and clears siblings in the same label", () => {
    const before = [
      cand({ id: "a", label: "housing", starred: true }),
      cand({ id: "b", label: "housing", starred: false }),
      cand({ id: "c", label: "budget", starred: true }),
    ];
    const after = applyStar(before, "b");
    expect(after.find((c) => c.id === "b")!.starred).toBe(true);
    expect(after.find((c) => c.id === "a")!.starred).toBe(false); // sibling cleared
    expect(after.find((c) => c.id === "c")!.starred).toBe(true); // other label untouched
  });

  it("un-stars when the target was already starred", () => {
    const after = applyStar([cand({ id: "a", label: "housing", starred: true })], "a");
    expect(after[0].starred).toBe(false);
  });

  it("does not mutate the input array or items", () => {
    const before = [cand({ id: "a", label: "housing", starred: false })];
    const after = applyStar(before, "a");
    expect(before[0].starred).toBe(false);
    expect(after).not.toBe(before);
  });

  it("returns input unchanged for an unknown id", () => {
    const before = [cand({ id: "a" })];
    expect(applyStar(before, "zzz")).toBe(before);
  });
});

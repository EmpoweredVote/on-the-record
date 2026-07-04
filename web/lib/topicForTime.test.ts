import { describe, it, expect } from "vitest";
import { topicForTime } from "./topicForTime";
import type { SummarySection } from "./types";

function section(
  start: number | null,
  end: number | null,
  topics: { key: string; title: string; status: "predicted" | "verified" }[]
): SummarySection {
  return {
    section_type: "discussion",
    title: "x",
    content: "",
    start_time: start,
    end_time: end,
    sort_order: 0,
    topics,
  };
}

const SECTIONS: SummarySection[] = [
  section(0, 420, [{ key: "housing", title: "Housing", status: "verified" }]),
  section(420, 900, [{ key: "public-safety", title: "Public Safety", status: "predicted" }]),
  section(900, 1600, []), // substantive but untagged
];

describe("topicForTime", () => {
  it("returns the topic of the containing section", () => {
    expect(topicForTime(SECTIONS, 95)?.key).toBe("housing");
    expect(topicForTime(SECTIONS, 640)?.key).toBe("public-safety");
  });

  it("is inclusive of start and exclusive of end", () => {
    expect(topicForTime(SECTIONS, 0)?.key).toBe("housing");
    expect(topicForTime(SECTIONS, 420)?.key).toBe("public-safety"); // boundary → next section
  });

  it("returns null in an untagged section", () => {
    expect(topicForTime(SECTIONS, 1000)).toBeNull();
  });

  it("returns null outside any section", () => {
    expect(topicForTime(SECTIONS, 5000)).toBeNull();
  });

  it("carries the predicted/verified status through", () => {
    expect(topicForTime(SECTIONS, 640)?.status).toBe("predicted");
    expect(topicForTime(SECTIONS, 95)?.status).toBe("verified");
  });

  it("ignores sections with missing time bounds and handles empty/null input", () => {
    expect(topicForTime([section(null, null, [{ key: "x", title: "X", status: "verified" }])], 10)).toBeNull();
    expect(topicForTime(null, 10)).toBeNull();
    expect(topicForTime([], 10)).toBeNull();
  });
});

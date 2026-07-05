import { describe, expect, it } from "vitest";
import { buildTopicHueMap, TOPIC_HUES, topicHueStyle } from "./topicColors";
import type { SectionTopicRef, SummarySection } from "./types";

function sec(...topics: string[]): SummarySection {
  return {
    section_type: "topic",
    title: "T",
    content: "",
    start_time: 0,
    end_time: 1,
    sort_order: 0,
    topics: topics.map((key): SectionTopicRef => ({ key, title: key, status: "predicted" })),
  } as SummarySection;
}

describe("buildTopicHueMap", () => {
  it("assigns hues in first-seen order across meetings and dedupes", () => {
    const map = buildTopicHueMap([
      [sec("housing", "homelessness")],
      [sec("homelessness"), sec("transit")], // homelessness repeats
    ]);
    expect(map.get("housing")).toBe(TOPIC_HUES[0]);
    expect(map.get("homelessness")).toBe(TOPIC_HUES[1]);
    expect(map.get("transit")).toBe(TOPIC_HUES[2]);
    expect(map.size).toBe(3);
  });

  it("cycles the palette when there are more topics than hues", () => {
    const keys = Array.from({ length: TOPIC_HUES.length + 1 }, (_, i) => `t${i}`);
    const map = buildTopicHueMap([[sec(...keys)]]);
    // the (N+1)th distinct topic wraps back to the first hue
    expect(map.get(`t${TOPIC_HUES.length}`)).toBe(TOPIC_HUES[0]);
  });

  it("ignores null/undefined groups and empty topics", () => {
    expect(buildTopicHueMap([undefined, [sec()]]).size).toBe(0);
  });
});

describe("topicHueStyle", () => {
  it("returns a --th custom property, or undefined when no hue", () => {
    expect(topicHueStyle(210)).toEqual({ "--th": "210" });
    expect(topicHueStyle(undefined)).toBeUndefined();
  });
});

import { describe, expect, it } from "vitest";
import { buildOutline, meetingTopics } from "./outline";
import type { SectionTopicRef, SummarySection } from "./types";

function section(
  section_type: string,
  title = "T",
  topics: SectionTopicRef[] = []
): SummarySection {
  return {
    section_type,
    title,
    content: "",
    start_time: 0,
    end_time: 1,
    sort_order: 0,
    topics,
  } as SummarySection;
}

function topic(key: string, title = key): SectionTopicRef {
  return { key, title, status: "predicted" };
}

describe("buildOutline", () => {
  it("includes interview 'topic' sections (regression: interviews had empty outlines)", () => {
    const sections = [
      section("topic", "Homelessness Crisis and Solutions"),
      section("topic", "Housing Development"),
    ];
    expect(buildOutline(sections).map((s) => s.title)).toEqual([
      "Homelessness Crisis and Solutions",
      "Housing Development",
    ]);
  });

  it("keeps council substantive types and drops procedural ones", () => {
    const sections = [
      section("opening"),
      section("discussion", "Zoning"),
      section("roll_call"),
      section("vote", "Budget Vote"),
      section("closing"),
    ];
    expect(buildOutline(sections).map((s) => s.title)).toEqual([
      "Zoning",
      "Budget Vote",
    ]);
  });

  it("handles null/undefined sections", () => {
    expect(buildOutline(null)).toEqual([]);
    expect(buildOutline(undefined)).toEqual([]);
  });
});

describe("meetingTopics", () => {
  it("collects distinct topics across sections in first-seen order", () => {
    const sections = [
      section("topic", "Intro", []),
      section("topic", "A", [topic("homelessness"), topic("housing")]),
      section("topic", "B", [topic("housing"), topic("economic-development")]),
    ];
    expect(meetingTopics(sections).map((t) => t.key)).toEqual([
      "homelessness",
      "housing",
      "economic-development",
    ]);
  });

  it("returns [] when no section has topics (genuinely untagged)", () => {
    expect(meetingTopics([section("topic", "Intro"), section("topic", "Outro")])).toEqual([]);
    expect(meetingTopics(null)).toEqual([]);
  });
});

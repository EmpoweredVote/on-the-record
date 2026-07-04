import { describe, expect, it } from "vitest";
import { buildOutline } from "./outline";
import type { SummarySection } from "./types";

function section(section_type: string, title = "T"): SummarySection {
  return {
    section_type,
    title,
    content: "",
    start_time: 0,
    end_time: 1,
    sort_order: 0,
    topics: [],
  } as SummarySection;
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

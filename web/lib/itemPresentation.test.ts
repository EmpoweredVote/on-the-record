import { describe, expect, it } from "vitest";
import { itemStateBadge, outcomeLabel } from "./itemPresentation";

describe("outcomeLabel", () => {
  it("maps passed", () => {
    expect(outcomeLabel("passed")).toBe("Passed");
  });

  it("maps failed", () => {
    expect(outcomeLabel("failed")).toBe("Failed");
  });

  it("maps continued", () => {
    expect(outcomeLabel("continued")).toBe("Continued to a later meeting");
  });

  it("maps pulled", () => {
    expect(outcomeLabel("pulled")).toBe("Pulled from the agenda");
  });

  it("maps no-action", () => {
    expect(outcomeLabel("no-action")).toBe("No action taken");
  });

  it("returns null for null or unknown outcomes", () => {
    expect(outcomeLabel(null)).toBeNull();
    expect(outcomeLabel("something-else")).toBeNull();
  });
});

describe("itemStateBadge", () => {
  it("labels an upcoming item 'Coming up'", () => {
    expect(itemStateBadge("upcoming", "2026-07-29")).toEqual({
      label: "Coming up",
      tone: "upcoming",
    });
  });

  it("labels a happened item with the meeting date", () => {
    expect(itemStateBadge("happened", "2026-07-29")).toEqual({
      label: "From the meeting on July 29, 2026",
      tone: "happened",
    });
  });
});

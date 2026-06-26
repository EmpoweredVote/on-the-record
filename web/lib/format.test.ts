import { describe, it, expect } from "vitest";
import { formatDuration } from "./format";

describe("formatDuration", () => {
  it("returns empty string for null", () => {
    expect(formatDuration(null)).toBe("");
  });

  it("returns empty string for zero", () => {
    expect(formatDuration(0)).toBe("");
  });

  it("formats sub-hour durations as minutes", () => {
    expect(formatDuration(2880)).toBe("48m");
  });

  it("formats hour-plus durations as hours and minutes", () => {
    expect(formatDuration(8040)).toBe("2h 14m");
  });
});

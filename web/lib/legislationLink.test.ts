import { describe, expect, it } from "vitest";
import { legislationUrl } from "./legislationLink";

describe("legislationUrl", () => {
  it("builds an ordinance page URL", () => {
    expect(legislationUrl("Ordinance 2026-16")).toBe(
      "https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-16"
    );
  });

  it("builds a resolution page URL", () => {
    expect(legislationUrl("Resolution 2026-14")).toBe(
      "https://bloomington.in.gov/council/legislation/Resolution/2026/2026-14"
    );
  });

  it("percent-encodes the appropriation ordinance path", () => {
    expect(legislationUrl("Appropriation Ordinance 2026-3")).toBe(
      "https://bloomington.in.gov/council/legislation/Appropriation%20Ordinance/2026/2026-03"
    );
  });

  it("zero-pads single-digit numbers (2026-1 is a 404; 2026-01 is the page)", () => {
    expect(legislationUrl("Ordinance 2026-7")).toBe(
      "https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-07"
    );
  });

  it("is case- and whitespace-tolerant on the type", () => {
    expect(legislationUrl("  ordinance   2026-16 ")).toBe(
      "https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-16"
    );
  });

  it("returns null for unknown types, malformed refs, and null", () => {
    expect(legislationUrl("Proclamation 2026-2")).toBeNull();
    expect(legislationUrl("Ordinance 26-07")).toBeNull(); // 2-digit year: not the page pattern
    expect(legislationUrl("Ordinance")).toBeNull();
    expect(legislationUrl("")).toBeNull();
    expect(legislationUrl(null)).toBeNull();
  });
});

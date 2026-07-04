import { describe, it, expect } from "vitest";
import { quoteDeepLink } from "./sourceLink";

describe("quoteDeepLink", () => {
  it("returns null when there is no source url", () => {
    expect(quoteDeepLink(null, "youtube", 95)).toBeNull();
    expect(quoteDeepLink(undefined, "youtube", 95)).toBeNull();
  });

  it("appends &t=<n>s for a youtube url that already has a query", () => {
    expect(quoteDeepLink("https://www.youtube.com/watch?v=abc", "youtube", 640)).toBe(
      "https://www.youtube.com/watch?v=abc&t=640s"
    );
  });

  it("uses ?t=<n>s when the youtube url has no query", () => {
    expect(quoteDeepLink("https://youtu.be/abc", "youtube", 95)).toBe(
      "https://youtu.be/abc?t=95s"
    );
  });

  it("floors fractional seconds and clamps negatives to zero", () => {
    expect(quoteDeepLink("https://youtu.be/abc", "youtube", 95.9)).toBe("https://youtu.be/abc?t=95s");
    expect(quoteDeepLink("https://youtu.be/abc", "youtube", -3)).toBe("https://youtu.be/abc?t=0s");
  });

  it("does not add a fragment for non-youtube sources", () => {
    expect(quoteDeepLink("https://example.com/clip.mp4", "file", 120)).toBe(
      "https://example.com/clip.mp4"
    );
    expect(quoteDeepLink("https://example.com/x", null, 120)).toBe("https://example.com/x");
  });
});

import { describe, expect, it } from "vitest";
import {
  groupItemSpeakers,
  itemStateBadge,
  outcomeHeadline,
  outcomeLabel,
  voteBuckets,
  votePositionBucket,
} from "./itemPresentation";
import type { ItemSpeaker, ItemVoteRecord } from "./types";

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

describe("outcomeHeadline", () => {
  it("prefers the clerk-recorded result (outcome AND margin) over the bare word", () => {
    expect(outcomeHeadline("failed", [{ result: "Failed 4–4" }])).toEqual({
      text: "Failed 4–4",
      glyph: "✗",
      tone: "failed",
    });
  });

  it("uses the LAST vote — the dispositive motion", () => {
    expect(
      outcomeHeadline("passed", [{ result: "Amendment failed 3–5" }, { result: "Passed 8–0" }])
        ?.text
    ).toBe("Passed 8–0");
  });

  it("falls back to the outcome label when no votes are recorded yet", () => {
    expect(outcomeHeadline("passed", [])).toEqual({
      text: "Passed",
      glyph: "✓",
      tone: "passed",
    });
    expect(outcomeHeadline("continued", [])).toEqual({
      text: "Continued to a later meeting",
      glyph: "→",
      tone: "continued",
    });
  });

  it("is neutral-toned for pulled/no-action and null when nothing is known", () => {
    expect(outcomeHeadline("pulled", [])?.tone).toBe("neutral");
    expect(outcomeHeadline("pulled", [])?.glyph).toBeNull();
    expect(outcomeHeadline(null, [])).toBeNull();
  });

  it("shows a recorded vote even when the outcome column is empty", () => {
    expect(outcomeHeadline(null, [{ result: "Passed 8–0" }])).toEqual({
      text: "Passed 8–0",
      glyph: null,
      tone: "neutral",
    });
  });
});

describe("votePositionBucket", () => {
  it("accepts clerk-memo and federal vocabulary", () => {
    expect(votePositionBucket("aye")).toBe("for");
    expect(votePositionBucket("Yea")).toBe("for");
    expect(votePositionBucket("yes")).toBe("for");
    expect(votePositionBucket("nay")).toBe("against");
    expect(votePositionBucket("No")).toBe("against");
    expect(votePositionBucket("abstain")).toBe("abstain");
    expect(votePositionBucket("Present")).toBe("abstain");
    expect(votePositionBucket("mystery")).toBe("other");
  });
});

describe("voteBuckets", () => {
  const rec = (position: string, name: string): ItemVoteRecord => ({
    position,
    name,
    politician_id: null,
  });

  it("always shows For and Against, even when empty", () => {
    const buckets = voteBuckets([rec("aye", "A"), rec("aye", "B")]);
    expect(buckets.map((b) => b.key)).toEqual(["for", "against"]);
    expect(buckets[0].records).toHaveLength(2);
    expect(buckets[1].records).toHaveLength(0);
    expect(buckets[0].label).toBe("For");
    expect(buckets[1].label).toBe("Against");
  });

  it("adds abstain/other tabs only when occupied", () => {
    const buckets = voteBuckets([
      rec("aye", "A"),
      rec("nay", "B"),
      rec("abstain", "C"),
      rec("recused", "D"),
    ]);
    expect(buckets.map((b) => b.key)).toEqual(["for", "against", "abstain", "other"]);
    expect(buckets.find((b) => b.key === "abstain")?.records[0].name).toBe("C");
  });
});

describe("groupItemSpeakers", () => {
  const sp = (name: string, politicianId: string | null): ItemSpeaker => ({
    name,
    politician_id: politicianId,
    role: null,
    first_spoke_seconds: 0,
    segment_count: 1,
  });

  it("splits officials (linked) from everyone else and drops Non-speaker", () => {
    const groups = groupItemSpeakers([
      sp("Kate Rosenbarger", "p1"),
      sp("Buff Brown", null),
      sp("Non-speaker", null),
      sp("Unidentified Speaker", null),
    ]);
    expect(groups.officials.map((s) => s.name)).toEqual(["Kate Rosenbarger"]);
    expect(groups.others.map((s) => s.name)).toEqual([
      "Buff Brown",
      "Unidentified Speaker",
    ]);
  });
});

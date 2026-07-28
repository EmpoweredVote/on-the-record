import { describe, expect, it } from "vitest";
import {
  groupItemSpeakers,
  itemStateBadge,
  outcomeGlyph,
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

describe("outcomeGlyph", () => {
  it("marks only the unambiguous outcomes", () => {
    expect(outcomeGlyph("passed")).toBe("✓");
    expect(outcomeGlyph("failed")).toBe("✗");
    expect(outcomeGlyph("continued")).toBeNull();
    expect(outcomeGlyph(null)).toBeNull();
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

import { describe, expect, it } from "vitest";
import type { Meeting } from "./types";
import { formatMeetingWhen, groupByDate } from "./upcoming";

function meeting(overrides: Partial<Meeting>): Meeting {
  return {
    meeting_id: "m1",
    slug: null,
    title: null,
    event_kind: "council",
    city: null,
    chamber_id: null,
    race_id: null,
    meeting_type: "Regular Session",
    meeting_date: "2026-07-29",
    source_url: null,
    playback_kind: null,
    playback_url: null,
    duration_seconds: null,
    clip_start_seconds: null,
    clip_end_seconds: null,
    summary_preview: null,
    speakers: [],
    speaker_count: null,
    event_orgs: [],
    source_title: null,
    thumbnail_url: null,
    status: "upcoming",
    starts_at: null,
    timezone: null,
    ...overrides,
  };
}

describe("formatMeetingWhen", () => {
  it("renders in the meeting's own zone when starts_at + timezone are present", () => {
    // 22:30 UTC on July 29 is 6:30 PM in Indiana (EDT, UTC-4).
    const out = formatMeetingWhen(
      "2026-07-29",
      "2026-07-29T22:30:00.000Z",
      "America/Indiana/Indianapolis"
    );
    expect(out).toContain("Wednesday");
    expect(out).toContain("July 29");
    expect(out).toMatch(/6:30/);
  });

  it("falls back to date-only when starts_at is null", () => {
    const out = formatMeetingWhen("2026-07-29", null, "America/Indiana/Indianapolis");
    expect(out).toContain("Wednesday");
    expect(out).toContain("July 29");
    expect(out).not.toMatch(/\d:\d{2}/);
  });

  it("falls back to date-only when timezone is null even with starts_at", () => {
    const out = formatMeetingWhen("2026-07-29", "2026-07-29T22:30:00.000Z", null);
    expect(out).toContain("Wednesday");
    expect(out).toContain("July 29");
    expect(out).not.toMatch(/\d:\d{2}/);
  });

  it("falls back to date-only on an invalid zone string without throwing", () => {
    const out = formatMeetingWhen(
      "2026-07-29",
      "2026-07-29T22:30:00.000Z",
      "Not/A_Real_Zone"
    );
    expect(out).toContain("Wednesday");
    expect(out).toContain("July 29");
    expect(out).not.toMatch(/\d:\d{2}/);
  });
});

describe("groupByDate", () => {
  it("groups adjacent same-date meetings preserving order", () => {
    const a = meeting({ meeting_id: "a", meeting_date: "2026-07-29" });
    const b = meeting({ meeting_id: "b", meeting_date: "2026-07-29" });
    const c = meeting({ meeting_id: "c", meeting_date: "2026-07-30" });
    const groups = groupByDate([a, b, c]);
    expect(groups).toHaveLength(2);
    expect(groups[0].date).toBe("2026-07-29");
    expect(groups[0].meetings.map((m) => m.meeting_id)).toEqual(["a", "b"]);
    expect(groups[1].date).toBe("2026-07-30");
    expect(groups[1].meetings.map((m) => m.meeting_id)).toEqual(["c"]);
  });

  it("returns an empty list for no meetings", () => {
    expect(groupByDate([])).toEqual([]);
  });
});
